"""그날 new1 거래 내역 출력 — 텔레그램 허브(친구 창구)용, 읽기 전용 (2026-10-15 신설).

    python C:/Users/frel/Desktop/new1/tg_trades.py [YYYY-MM-DD]

날짜를 안 주면 transactions.csv에 있는 가장 최근 매매일. 이 레포의 transactions.csv만 읽고
아무 파일도 쓰지 않는다. 다른 계좌 데이터는 읽지도 출력하지도 않는다.
"""
import sys
import warnings

warnings.filterwarnings("ignore")

import pandas as pd

import portfolio_core as core


def main():
    print_trades(sys.argv[1] if len(sys.argv) > 1 else None)


def print_trades(day=None):
    tx = core.load_transactions()
    tx = tx[tx["구분"].isin(["매수", "매도", "입금", "출금"])]
    if tx.empty:
        print("거래 기록 없음")
        return
    trade_days = sorted(tx[tx["구분"].isin(["매수", "매도"])]["날짜"].unique())
    day = day or trade_days[-1]
    t = tx[tx["날짜"] == day]
    print(f"[{day}] new1 거래 · 매매일지 반영분 기준 (마지막 반영일 {trade_days[-1]})")
    if t.empty:
        print("이 날짜 거래 없음")
        return
    for kind in ("매수", "매도"):
        g = t[t["구분"] == kind]
        amt = (g["수량"].astype(float) * g["단가"].astype(float)).sum()
        line = f"{kind} {len(g)}건 · {amt:,.0f}원"
        if kind == "매도" and len(g):
            line += f" · 실현손익 {pd.to_numeric(g['실현손익'], errors='coerce').sum():+,.0f}원"
        print(line)
        for _, r in g.iterrows():
            q, p = float(r["수량"]), float(r["단가"])
            extra = ""
            if kind == "매도":
                rp = pd.to_numeric(r["실현손익"], errors="coerce")
                extra = f" (실현 {rp:+,.0f}원)" if pd.notna(rp) else ""
            print(f"  {r['종목명']} {q:g}주 @ {p:,.0f} = {q * p:,.0f}원{extra}")
    for _, r in t[t["구분"].isin(["입금", "출금"])].iterrows():
        print(f"{r['구분']} {float(r['단가']):,.0f}원 ({r['메모']})")


if __name__ == "__main__":
    main()
