"""CFG 태그 물어볼 종목 찾기 — 매매일지 반영 끝에 한 번 (2026-10-20, 위크 회의).

그날 새로 산(사이클 첫 매수) 종목 중, 직전 LOOKBACK 거래일 아침 리포트(액션·외인·CFG·Basecamp)에
이름이 나왔던 것만 골라 "리포트 보고 산 거야?"를 물을 후보로 찍는다. 이미 cfg_tagged.csv에 있으면 뺀다.
리포트 HTML에 종목명이 들어 있는지만 본다(어느 칸이었는지는 대표 답으로).

사용: python cfg_ask.py [YYYY-MM-DD]   (기본 = transactions 마지막 날짜)
"""
import sys
from pathlib import Path

import pandas as pd

import portfolio_core as core

HERE = Path(__file__).parent
LOOKBACK = 3  # 거래일 — 리포트를 보고 바로 사지 않고 하루이틀 지켜본 뒤 사는 경우까지(인투셀: 10-12~ 액션 리포트, 10-19 매수)
REPORT_DIRS = {
    "액션": HERE / "morning_report" / "action",
    "외인": HERE / "link_sample" / "daily",
    "CFG": HERE / "morning_report" / "cfg",
    "Basecamp": HERE / "morning_report" / "basecamp",
}


def new_buys(tx: pd.DataFrame, day: str) -> list[str]:
    """day에 매수했고, 그 매수가 사이클 첫 매수(전날 보유 0)인 종목."""
    t = tx[tx["구분"].isin(["매수", "매도"])].copy()
    out = []
    for name in t[(t["날짜"] == day) & (t["구분"] == "매수")]["종목명"].unique():
        prev = t[(t["종목명"] == name) & (t["날짜"] < day)]
        held = prev.loc[prev["구분"] == "매수", "수량"].sum() - prev.loc[prev["구분"] == "매도", "수량"].sum()
        if held <= 0:
            out.append(name)
    return out


def report_hits(name: str, day: str, lookback: int = LOOKBACK) -> list[str]:
    """day 이전(그날 포함) 리포트 날짜 중 최근 lookback개에서 name이 나온 '리포트명 날짜' 목록."""
    hits = []
    for label, d in REPORT_DIRS.items():
        files = sorted(p for p in d.glob("????-??-??.html") if p.stem <= day)[-lookback:]
        for p in files:
            if name in p.read_text(encoding="utf-8", errors="ignore"):
                hits.append(f"{label} {p.stem[5:]}")
    return hits


def candidates(day: str | None = None) -> list[tuple[str, list[str]]]:
    tx = core.load_transactions()
    day = day or str(tx["날짜"].max())
    tagged = set(core.load_cfg_tags()["종목명"])
    return [(n, h) for n in new_buys(tx, day) if n not in tagged and (h := report_hits(n, day))]


if __name__ == "__main__":
    day = sys.argv[1] if len(sys.argv) > 1 else None
    rows = candidates(day)
    if not rows:
        print("물어볼 종목 없음")
    for n, h in rows:
        print(f"{n}: {', '.join(h)} → 리포트 보고 산 거야? (CFG 태그)")
