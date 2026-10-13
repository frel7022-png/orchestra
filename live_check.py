"""지금 이 순간 시장·계좌 상태 — 사용자가 뭔가를 물으면 세션이 먼저 돌린다(2026-10-13 사용자 지시).

히스토리 CSV(index_history/bigcap_history)는 마지막 매매일지 반영 시점(장중일 수도 있음)에 멈춰
있어서 "오늘"을 말할 때 쓰면 틀린다. 그래서 여기서는 지수·대형주를 네이버 일별시세 **확정 종가**로
처음부터 다시 받고, 오늘 값만 실시간 시세로 덮어쓴다. 파일에는 아무것도 쓰지 않는다.

보유 수량·평단·예수금은 마지막 매매일지 반영분 기준(오늘 매매는 반영 전이면 안 잡힘).

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


def main():
    now = datetime.now()
    today = core.today_kst_str()
    anchor = str(core.load_index_history()["날짜"].min())   # 8/14 — 앱의 누적 기준일과 같게

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
    h = core.load_holdings()
    st = core.load_state()
    codes = h["종목코드"].astype(str)
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
        return cum * 100, day * 100

    ex_cum, ex_day = blend(core.synthetic_kospi_ex_bigcap(idx, bigdf))
    mn_cum, mn_day = blend(idx)

    cash = st["cash"]
    tot, tot_prev = float(val.sum()) + cash, float(prev.sum()) + cash
    ah = core.load_history()
    base = ah[ah["날짜"] >= anchor].sort_values("날짜")
    acct_cum = (tot / float(base["총자산"].iloc[0]) - 1) * 100 if not base.empty else None

    print(f"[{now:%Y-%m-%d %H:%M}] 지수·대형주 = 확정 종가 + 오늘 실시간 · 보유 = 매매일지 {ah['날짜'].max()} 반영분")
    print(f"코스피 {iq['KOSPI']['change_pct']:+.2f}% ({iq['KOSPI']['price']:,.2f}) · "
          f"코스닥 {iq['KOSDAQ']['change_pct']:+.2f}% ({iq['KOSDAQ']['price']:,.2f})")
    print(f"혼합지수(삼성·하이닉스 제외) 당일 {ex_day:+.2f}% · 누적 {ex_cum:+.2f}%   (코스피 비중 {wk:.0%})")
    print(f"혼합지수(일반)             당일 {mn_day:+.2f}% · 누적 {mn_cum:+.2f}%")
    print(f"내 주식 당일 {(val.sum() / prev.sum() - 1) * 100:+.2f}% · "
          f"내 계좌 당일 {(tot / tot_prev - 1) * 100:+.2f}% ({tot - tot_prev:+,.0f}원)"
          + (f" · 계좌 누적({anchor[5:]}~) {acct_cum:+.2f}%" if acct_cum is not None else ""))
    print(f"총자산 {tot:,.0f}원 · 최초자본 대비 {(tot / st['initial'] - 1) * 100:+.2f}% · "
          f"예수금 {cash:,.0f}원 ({cash / tot * 100:.1f}%) · 평가손익 {val.sum() - cost.sum():+,.0f}원")
    print(f"보유 {len(h)}종목: 상승 {(chg > 0).sum()} · 하락 {(chg < 0).sum()} · 보합 {(chg == 0).sum()}")
    t = h.assign(등락=chg).sort_values("등락")
    print("많이 빠진: " + ", ".join(f"{r.종목명} {r.등락:+.1f}%" for r in t.head(6).itertuples()))
    print("많이 오른: " + ", ".join(f"{r.종목명} {r.등락:+.1f}%" for r in t.tail(4)[::-1].itertuples()))


if __name__ == "__main__":
    main()
