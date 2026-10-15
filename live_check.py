"""지금 이 순간 시장·계좌 상태 — 사용자가 뭔가를 물으면 세션이 먼저 돌린다(2026-10-13 사용자 지시).

히스토리 CSV(index_history/bigcap_history)는 마지막 매매일지 반영 시점(장중일 수도 있음)에 멈춰
있어서 "오늘"을 말할 때 쓰면 틀린다. 그래서 여기서는 지수·대형주를 네이버 일별시세 **확정 종가**로
처음부터 다시 받고, 오늘 값만 실시간 시세로 덮어쓴다. 파일에는 아무것도 쓰지 않는다.

보유 수량·평단·예수금은 마지막 매매일지 반영분 기준(오늘 매매는 반영 전이면 안 잡힘).
morning_report.py 액션 리포트 맨 위 "계좌 vs 혼합지수" 칸도 live_snapshot()을 그대로 쓴다.

사용: python live_check.py
"""
import warnings
from datetime import datetime

import pandas as pd

import portfolio_core as core

warnings.filterwarnings("ignore", category=FutureWarning)


def confirmed_series(code: str, start: str, today: str) -> dict:
    """{날짜: 확정 종가} — today 행은 장중이면 아직 미확정이라 호출부가 실시간으로 덮어쓴다."""
    return {r["날짜"]: float(r["종가"]) for r in core.fetch_daily_price_history(code, start, today)}


def live_snapshot(holdings: pd.DataFrame | None = None, quotes: dict | None = None) -> dict:
    """지금 시장·계좌 상태. 누적은 전부 앵커일(index_history 첫날, 8/14) 대비, 당일은 전일 확정 종가 대비.
    holdings/quotes를 넘기면 그걸 쓴다(morning_report가 이미 받은 실시간 시세를 재사용하려고)."""
    now = datetime.now()
    today = core.today_kst_str()
    anchor = str(core.load_index_history()["날짜"].min())

    # 지수·대형주: 확정 종가 + 오늘 실시간
    iq = core.fetch_index_quotes()
    bq = core.fetch_bigcap_quotes()
    k = confirmed_series("KOSPI", anchor, today)
    q = confirmed_series("KOSDAQ", anchor, today)
    k[today], q[today] = iq["KOSPI"]["price"], iq["KOSDAQ"]["price"]
    dates = sorted(set(k) & set(q))
    idx = pd.DataFrame({"날짜": dates, "KOSPI": [k[d] for d in dates], "KOSDAQ": [q[d] for d in dates]})
    big = {nm: confirmed_series(code, anchor, today) for nm, code in core.BIGCAP_CODES.items()}
    for nm in big:
        if bq.get(nm):
            big[nm][today] = bq[nm]
    bdates = sorted(set.intersection(*(set(v) for v in big.values())))
    bigdf = pd.DataFrame({"날짜": bdates, **{nm: [big[nm][d] for d in bdates] for nm in big}})

    # 보유종목 실시간
    h = core.load_holdings() if holdings is None else holdings
    st = core.load_state()
    codes = h["종목코드"].astype(str)
    if quotes is None:
        quotes, _ = core.fetch_quotes(codes.tolist())
    px = codes.map(lambda c: (quotes.get(c) or {}).get("price")).fillna(h["현재가"]).astype(float)
    chg = codes.map(lambda c: (quotes.get(c) or {}).get("change_pct")).fillna(0).astype(float)
    qty = pd.to_numeric(h["수량"])
    val = qty * px
    prev = val / (1 + chg / 100)
    cost = qty * pd.to_numeric(h["평단가"])
    mk = h["종목명"].map(core.load_market_cache())
    ks, kq = float(val[mk == "KOSPI"].sum()), float(val[mk == "KOSDAQ"].sum())
    wk = ks / (ks + kq) if ks + kq else 1.0

    def blend(df):
        a, b = df["KOSPI"].astype(float), df["KOSDAQ"].astype(float)
        cum = wk * (a.iloc[-1] / a.iloc[0] - 1) + (1 - wk) * (b.iloc[-1] / b.iloc[0] - 1)
        day = wk * (a.iloc[-1] / a.iloc[-2] - 1) + (1 - wk) * (b.iloc[-1] / b.iloc[-2] - 1)
        return cum, day

    cash = st["cash"]
    tot, tot_prev = float(val.sum()) + cash, float(prev.sum()) + cash

    # 계좌·주식 누적: 앱과 같은 정의(compute_index_vs_account의 마지막 스냅샷) × 그 스냅샷 이후 실시간 변화.
    # 마지막 스냅샷 = 마지막 매매일지 반영일이라 그 뒤로 보유·예수금 흐름이 없다.
    ah = core.load_history()
    tx = core.load_transactions()
    me = core.compute_index_vs_account(tx, ah, core.load_index_history(), st["initial"],
                                       st.get("fee_rate", 0.0), kospi_weight=wk).get("me")
    acct_cum = stock_cum = None
    if me is not None and not me.empty:
        d = str(me["날짜"].iloc[-1])
        row = ah[ah["날짜"] == d]
        if not row.empty:
            tot_d = float(row["총자산"].iloc[0])
            acct_cum = (1 + float(me["계좌수익"].iloc[-1])) * (tot / tot_d) - 1
            if tot_d - cash > 0:
                stock_cum = (1 + float(me["주식수익"].iloc[-1])) * (float(val.sum()) / (tot_d - cash)) - 1

    t = h.assign(등락=chg).sort_values("등락")
    return {
        "asof": now.strftime("%Y-%m-%d %H:%M"), "holdings_asof": str(ah["날짜"].max()), "anchor": anchor,
        "kospi": iq["KOSPI"], "kosdaq": iq["KOSDAQ"], "wk": wk,
        "ex": blend(core.synthetic_kospi_ex_bigcap(idx, bigdf)), "main": blend(idx),
        "acct": (acct_cum, tot / tot_prev - 1), "acct_won": tot - tot_prev,
        "stock": (stock_cum, float(val.sum()) / float(prev.sum()) - 1),
        "total": tot, "initial": st["initial"], "cash": cash, "eval_pl": float(val.sum() - cost.sum()),
        "n": len(h), "n_up": int((chg > 0).sum()), "n_down": int((chg < 0).sum()), "n_flat": int((chg == 0).sum()),
        "idx": idx, "bigdf": bigdf, "me": me, "today": today, "holdings": h, "px": px, "chg": chg,
        "worst": [(r.종목명, r.등락) for r in t.head(6).itertuples()],
        "best": [(r.종목명, r.등락) for r in t.tail(4)[::-1].itertuples()],
    }


def main():
    s = live_snapshot()
    pct = lambda v: "-" if v is None else f"{v * 100:+.2f}%"
    print(f"[{s['asof']}] 지수·대형주 = 확정 종가 + 오늘 실시간 · 보유 = 매매일지 {s['holdings_asof']} 반영분")
    print(f"코스피 {s['kospi']['change_pct']:+.2f}% ({s['kospi']['price']:,.2f}) · "
          f"코스닥 {s['kosdaq']['change_pct']:+.2f}% ({s['kosdaq']['price']:,.2f})")
    print(f"혼합지수(삼성·하이닉스 제외) 당일 {pct(s['ex'][1])} · 누적 {pct(s['ex'][0])}   (코스피 비중 {s['wk']:.0%})")
    print(f"혼합지수(일반)             당일 {pct(s['main'][1])} · 누적 {pct(s['main'][0])}")
    print(f"내 주식 당일 {pct(s['stock'][1])} · 누적 {pct(s['stock'][0])}")
    print(f"내 계좌 당일 {pct(s['acct'][1])} ({s['acct_won']:+,.0f}원) · 누적 {pct(s['acct'][0])}")
    print(f"총자산 {s['total']:,.0f}원 · 최초자본 대비 {pct(s['total'] / s['initial'] - 1)} · "
          f"예수금 {s['cash']:,.0f}원 ({s['cash'] / s['total'] * 100:.1f}%) · 평가손익 {s['eval_pl']:+,.0f}원")
    print(f"보유 {s['n']}종목: 상승 {s['n_up']} · 하락 {s['n_down']} · 보합 {s['n_flat']}")
    print("많이 빠진: " + ", ".join(f"{n} {v:+.1f}%" for n, v in s["worst"]))
    print("많이 오른: " + ", ".join(f"{n} {v:+.1f}%" for n, v in s["best"]))


if __name__ == "__main__":
    main()
