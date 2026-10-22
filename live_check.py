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



QUOTE_FAIL_STOP = 0.10  # 보유 평가금액의 이 비율 이상이 시세를 못 받으면 한 번 다시 받고, 그래도면 답을 멈춘다
                        # (대표 확정 2026-10-22 "오케이", 데이터센터/세팅 선반 1). 그 아래면 표시만 하고 계속.


def quote_fail_share(h: pd.DataFrame, quotes: dict) -> tuple[list[str], float]:
    """시세(change_pct)를 못 받은 종목명과, 그 종목들이 보유 평가금액(마지막 저장 현재가 기준)에서 차지하는 비율."""
    codes = h["종목코드"].astype(str)
    got = codes.map(lambda c: (quotes.get(c) or {}).get("change_pct") is not None)
    val = pd.to_numeric(h["수량"], errors="coerce").fillna(0) * pd.to_numeric(h["현재가"], errors="coerce").fillna(0)
    total = float(val.sum())
    share = float(val[~got.values].sum()) / total if total else 0.0
    return h.loc[~got.values, "종목명"].astype(str).tolist(), share


def confirmed_series(code: str, start: str, today: str) -> dict:
    """{날짜: 확정 종가} — today 행은 장중이면 아직 미확정이라 호출부가 실시간으로 덮어쓴다."""
    return {r["날짜"]: float(r["종가"]) for r in core.fetch_daily_price_history(code, start, today)}


def basis_label(now, cal_today: str, today: str, closed: bool) -> str:
    """숫자 기준 한마디: 장중 / 장 마감 후 / 개장 전 · 휴장(마지막 거래일 MM-DD 종가 기준)."""
    if not closed:
        return "장중" if now.strftime("%H:%M") < "15:30" else "장 마감 후"
    why = "개장 전" if now.weekday() < 5 and now.strftime("%H:%M") < "09:00" else "휴장"
    return f"{why} · 마지막 거래일 {today[5:]} 종가 기준"


def live_snapshot(holdings: pd.DataFrame | None = None, quotes: dict | None = None) -> dict:
    """지금 시장·계좌 상태. 누적은 전부 앵커일(index_history 첫날, 8/14) 대비, 당일은 전일 확정 종가 대비.
    holdings/quotes를 넘기면 그걸 쓴다(morning_report가 이미 받은 실시간 시세를 재사용하려고)."""
    now = datetime.now()
    cal_today = core.today_kst_str()
    anchor = str(core.load_index_history()["날짜"].min())
    # 오늘 장이 안 열렸으면(주말·공휴일·개장 전) 오늘 행을 만들지 않고 마지막 거래일 확정 종가를 "오늘"로 쓴다.
    # 안 그러면 지수엔 전 거래일 종가를 복사한 가짜 오늘 행(당일 0%)이 생기고, 보유종목 등락률(시세 API의
    # change_pct = 마지막 거래일 하루치)과 서로 다른 날을 비교하게 된다(2026-10-15 위크·대표 질문으로 발견).
    # 조회 실패(None)면 장이 열린 것으로 본다.
    closed = core.market_traded_on(cal_today) is False

    # 지수·대형주: 확정 종가 + 오늘 실시간
    iq = core.fetch_index_quotes()
    bq = core.fetch_bigcap_quotes()
    k = confirmed_series("KOSPI", anchor, cal_today)
    q = confirmed_series("KOSDAQ", anchor, cal_today)
    if closed:
        today = max(set(k) & set(q))
    else:
        today = cal_today
        k[today], q[today] = iq["KOSPI"]["price"], iq["KOSDAQ"]["price"]
    dates = sorted(set(k) & set(q))
    idx = pd.DataFrame({"날짜": dates, "KOSPI": [k[d] for d in dates], "KOSDAQ": [q[d] for d in dates]})
    big = {nm: confirmed_series(code, anchor, cal_today) for nm, code in core.BIGCAP_CODES.items()}
    for nm in big:
        if bq.get(nm) and not closed:
            big[nm][today] = bq[nm]
    bdates = sorted(set.intersection(*(set(v) for v in big.values())))
    bigdf = pd.DataFrame({"날짜": bdates, **{nm: [big[nm][d] for d in bdates] for nm in big}})

    # 보유종목 실시간
    h = core.load_holdings() if holdings is None else holdings
    st = core.load_state()
    codes = h["종목코드"].astype(str)
    if quotes is None:
        quotes, _ = core.fetch_quotes(codes.tolist())
    # 문턱 이상 실패면 못 받은 종목만 한 번 다시 받는다(QUOTE_FAIL_STOP). 그래도면 quote_stop = True.
    _, share = quote_fail_share(h, quotes)
    if share >= QUOTE_FAIL_STOP:
        miss = [c for c in codes if (quotes.get(c) or {}).get("change_pct") is None]
        again, _ = core.fetch_quotes(miss)
        quotes = {**quotes, **{k: v for k, v in (again or {}).items() if v}}
        _, share = quote_fail_share(h, quotes)
    # 시세를 못 받은 종목은 표시가 = portfolio_data 현재가, 등락 = 0으로 채우되(합계가 안 깨지게)
    # **조용히 0으로 섞지 않고** 목록으로 돌려준다 — 예전엔 실패 종목이 '보합'으로 세어졌다(Q-28, 대표 10-22 9-0 ③ ⓐ).
    got = codes.map(lambda c: (quotes.get(c) or {}).get("change_pct") is not None)
    failed = h.loc[~got.values, "종목명"].astype(str).tolist()
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
    # 당일(내 주식·내 계좌 %·원)은 시세를 받은 종목만으로 잰다 — 실패 종목(등락 0 채움)이 분모에 남아
    # 당일 %를 묽게 하지 않게(Q-28, 0930 보고서 ⓐ "당일 계산에서 뺌"). 총자산·누적은 전체 그대로.
    val_ok, prev_ok = float(val[got.values].sum()), float(prev[got.values].sum())
    day_stock = val_ok / prev_ok - 1 if prev_ok else 0.0   # 전부 실패면 0(첫 줄 경고가 대신 말함)
    day_acct = (val_ok + cash) / (prev_ok + cash) - 1 if prev_ok + cash else 0.0

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

    t = h.assign(등락=chg)[got.values].sort_values("등락")
    ok_chg = chg[got.values]
    return {
        "asof": now.strftime("%Y-%m-%d %H:%M"), "holdings_asof": str(ah["날짜"].max()), "anchor": anchor,
        "kospi": iq["KOSPI"], "kosdaq": iq["KOSDAQ"], "wk": wk,
        "ex": blend(core.synthetic_kospi_ex_bigcap(idx, bigdf)), "main": blend(idx),
        "acct": (acct_cum, day_acct), "acct_won": val_ok - prev_ok,
        "stock": (stock_cum, day_stock),
        "total": tot, "initial": st["initial"], "cash": cash, "eval_pl": float(val.sum() - cost.sum()),
        "n": len(h), "n_up": int((ok_chg > 0).sum()), "n_down": int((ok_chg < 0).sum()),
        "n_flat": int((ok_chg == 0).sum()), "quote_failed": failed,
        "quote_fail_share": share, "quote_stop": share >= QUOTE_FAIL_STOP,
        "idx": idx, "bigdf": bigdf, "me": me, "today": today, "cal_today": cal_today, "closed": closed,
        "basis": basis_label(now, cal_today, today, closed), "holdings": h, "px": px, "chg": chg,
        "worst": [(r.종목명, r.등락) for r in t.head(6).itertuples()],
        "best": [(r.종목명, r.등락) for r in t.tail(4)[::-1].itertuples()],
    }


def main():
    s = live_snapshot()
    pct = lambda v: "-" if v is None else f"{v * 100:+.2f}%"
    if s["quote_stop"]:
        print(f"[멈춤] 시세 실패 {len(s['quote_failed'])}종목 = 보유 평가금액의 {s['quote_fail_share']:.0%} "
              f"(문턱 {QUOTE_FAIL_STOP:.0%}) — 한 번 다시 받아도 그대로라 숫자를 말하지 않음. 잠시 뒤 다시: "
              + ", ".join(s["quote_failed"]))
        return
    if s["quote_failed"]:
        print(f"[⚠ 시세 실패 {len(s['quote_failed'])}종목 — 당일 %·원과 상승/하락/보합 수에서 뺌(총자산·누적엔 마지막 저장가로 들어감) · 평가금액의 {s['quote_fail_share']:.0%}] "
              + ", ".join(s["quote_failed"]))
    print(f"[{s['asof']}] {s['basis']} · 지수·대형주 = 확정 종가 + 오늘 실시간 · 보유 = 매매일지 {s['holdings_asof']} 반영분")
    print(f"코스피 {s['kospi']['change_pct']:+.2f}% ({s['kospi']['price']:,.2f}) · "
          f"코스닥 {s['kosdaq']['change_pct']:+.2f}% ({s['kosdaq']['price']:,.2f})")
    print(f"혼합지수(삼성·하이닉스 제외) 당일 {pct(s['ex'][1])} · 누적 {pct(s['ex'][0])}   (코스피 비중 {s['wk']:.0%})")
    print(f"혼합지수(일반)             당일 {pct(s['main'][1])} · 누적 {pct(s['main'][0])}")
    print(f"내 주식 당일 {pct(s['stock'][1])} · 누적 {pct(s['stock'][0])}")
    print(f"내 계좌 당일 {pct(s['acct'][1])} ({s['acct_won']:+,.0f}원) · 누적 {pct(s['acct'][0])}")
    print(f"총자산 {s['total']:,.0f}원 · 최초자본 대비 {pct(s['total'] / s['initial'] - 1)} · "
          f"예수금 {s['cash']:,.0f}원 ({s['cash'] / s['total'] * 100:.1f}%) · 평가손익 {s['eval_pl']:+,.0f}원")
    print(f"보유 {s['n']}종목: 상승 {s['n_up']} · 하락 {s['n_down']} · 보합 {s['n_flat']}"
          + (f" · 시세 실패 {len(s['quote_failed'])}" if s["quote_failed"] else ""))
    print("많이 빠진: " + ", ".join(f"{n} {v:+.1f}%" for n, v in s["worst"]))
    print("많이 오른: " + ", ".join(f"{n} {v:+.1f}%" for n, v in s["best"]))


if __name__ == "__main__":
    main()
