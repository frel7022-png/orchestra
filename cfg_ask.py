"""빠진 CFG 태그 후보 줍기 — 주간 리포트 때 한 번 (2026-10-20, 위크 회의).

원칙은 대표가 "외인 리포트 보고 골랐어"라고 말해 주는 것. 이건 대표도 빠뜨릴 수 있어서 일주일에 한 번 줍기만 한다
(대체됨 2026-10-20: 매매일지 반영마다 묻기 — 원칙을 뒤집고 잔소리가 됨).

그날 새로 산(사이클 첫 매수) 종목 중, 직전 LOOKBACK 거래일 아침 리포트(액션·외인·CFG·Basecamp)에
이름이 나왔던 것만 골라 하루 한 줄로 묶어 묻는다(종목 옆 괄호 = 어느 리포트). 대표가 답한 것만 태그 — 추측 태그 금지. 이미 cfg_tagged.csv에 있으면 뺀다.
리포트 HTML에 종목명이 들어 있는지만 본다(어느 칸이었는지는 대표 답으로).

사용: python cfg_ask.py [끝날짜] [--week]   (기본 = transactions 마지막 날짜, --week = 그 주 월요일~끝날짜)
"""
import sys
from datetime import date, timedelta
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


def candidates(day: str | None = None, week: bool = False) -> list[tuple[str, list[str]]]:
    tx = core.load_transactions()
    day = day or str(tx["날짜"].max())
    tagged = set(core.load_cfg_tags()["종목명"])
    days = [day]
    if week:
        d0 = date.fromisoformat(day)
        mon = (d0 - timedelta(days=d0.weekday())).isoformat()
        days = sorted(d for d in tx["날짜"].astype(str).unique() if mon <= d <= day)
    out, seen = [], set()
    for d in days:
        for n in new_buys(tx, d):
            if n in tagged or n in seen:
                continue
            h = report_hits(n, d)
            if h:
                out.append((n, h))
                seen.add(n)
    return out


def ask_line(rows: list[tuple[str, list[str]]]) -> str:
    """대표에게 하루 한 줄로 묻는 문장. 종목 옆 괄호 = 나왔던 리포트 종류(날짜 빼고, 중복 없이)."""
    if not rows:
        return "물어볼 종목 없음"
    parts = []
    for n, h in rows:
        kinds = list(dict.fromkeys(x.split()[0] for x in h))
        parts.append(f"{n}({'·'.join(kinds)})")
    return f"이번 주 새로 산 것 중 리포트에 나왔던 것: {', '.join(parts)} — 외인 보고 산 게 있었어?"


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    print(ask_line(candidates(args[0] if args else None, week="--week" in sys.argv)))
