"""후보 장부 — 바라보는 눈 빌딩의 저장고 (2026-10-20 대표 원-2026-10-20-10, 위크 규칙 셋).

기능들(Up/Down·Watering·Fishing·Basecamp·Link·CFG)이 그날 아침 리포트 상위에 올린 종목 + 대표가 보라고 한 종목
+ 대표가 고른 추천 종목을 한 장부에. 하루 한 번(아침 리포트 생성 때 morning_report.main이 부른다).

- 목록(`ledger/index.csv`, 종목 한 줄)만 보고 필요한 종목만 연다(`ledger/days.csv`에서 그 종목 줄).
- 채점하지 않는다 — 추천(picks_log)과 섞지 않는다(위크 규칙 ①).
- off(규칙 ②): 대표가 넣은 건 대표가 빼라 할 때까지 active. 기능이 올린 건 순위에서 빠지면 그날 그대로 멈추고(마지막 날 유지,
  다시 순위에 오면 이어 감), 주간 리포트 때 이유가 사라졌으면 `off` + 이유 한 줄.
- 쓰는 곳은 새러데이 하나(규칙 ③).

사용:
  python ledger.py list [--all]                          목록(active만, --all이면 off까지)
  python ledger.py show 종목명                            그 종목 날짜별 줄
  python ledger.py add 종목명 "이유" [--by 대표|추천]       대표가 보라고 한 종목 / 대표가 고른 추천
  python ledger.py note 종목명 "왜 넣었나 한 줄"            새러데이 메모(예: 10일째 5% 빠지다 3일째 바닥)
  python ledger.py off 종목명 "이유"                       주간 리포트 때(대표가 넣은 건 대표가 빼라 할 때만)
"""
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent
OUT = HERE / "ledger"
INDEX = OUT / "index.csv"
DAYS = OUT / "days.csv"
INDEX_COLS = ["종목명", "코드", "상태", "넣은이", "출처", "처음", "마지막", "순위일수", "오늘순위", "이유", "메모", "off일", "off이유"]
DAYS_COLS = ["날짜", "종목명", "코드", "출처", "순위", "한줄"]


def _load(path, cols):
    if path.exists():
        df = pd.read_csv(path, dtype={"코드": str}).fillna("")
        for c in cols:
            if c not in df.columns:
                df[c] = ""
        return df[cols]
    return pd.DataFrame(columns=cols)


def _save(df, path):
    OUT.mkdir(exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def rows_from_report(res: dict, roster: pd.DataFrame | None = None, top: int = 10) -> list[dict]:
    """아침 리포트 결과(morning_report.build의 res)에서 그날 순위에 오른 종목 줄들."""
    out = []

    def add(src, code, name, rank, line):
        out.append({"출처": src, "코드": str(code), "종목명": name, "순위": rank, "한줄": line})

    for i, x in enumerate(res.get("updown", []), 1):
        add("Up/Down", x["코드"], x["종목명"], i, f"매도가 대비 {x['pct']:+.1f}%")
    for i, x in enumerate(res.get("watering", []), 1):
        add("Watering", x["코드"], x["종목명"], i, f"마지막 매수가 대비 {x['pct_last']:+.1f}%")
    for x in res.get("fishing", []):
        add("Fishing", x["코드"], x["종목명"], x["순위"], f"기준일 대비 {x['cum']:+.1f}% · {x.get('상태', '')}")
    for i, x in enumerate(res.get("basecamp", []), 1):
        add("Basecamp", x["종목코드"], x["종목명"], i, f"고점 대비 {x['고점대비']:+.1f}% · {x.get('상태', '')}")
    for i, x in enumerate(res.get("cfg", []), 1):
        add("CFG", x["코드"], x["종목명"], i, f"{'·'.join(sorted(x['출처들']))} {x['pct']:+.1f}%")
    if roster is not None and not roster.empty:
        for _, r in roster[roster["순위"] <= top].iterrows():
            add("Link", r["종목코드"], r["종목명"], int(r["순위"]), f"{r['단계']} · P {r['P']:+.1f}% · dF {r['dF']:+.2f}%p")
    return out


def daily(today: str, rows: list[dict]) -> pd.DataFrame:
    """그날 순위 줄로 장부를 갱신. 같은 날 다시 돌리면 그날 줄만 바꾼다."""
    idx, days = _load(INDEX, INDEX_COLS), _load(DAYS, DAYS_COLS)
    days = days[days["날짜"] != today]
    new = pd.DataFrame([{**r, "날짜": today} for r in rows], columns=DAYS_COLS)
    days = pd.concat([days, new], ignore_index=True)
    on_today = set(new["종목명"])
    for name, g in new.groupby("종목명"):
        srcs = "·".join(dict.fromkeys(g["출처"]))
        if (idx["종목명"] == name).any():
            i = idx.index[idx["종목명"] == name][0]
            if idx.at[i, "상태"] == "off" and idx.at[i, "넣은이"] == "기능":
                idx.at[i, "상태"], idx.at[i, "off일"], idx.at[i, "off이유"] = "active", "", ""  # 다시 순위에 옴
            old = [s for s in str(idx.at[i, "출처"]).split("·") if s]
            idx.at[i, "출처"] = "·".join(dict.fromkeys(old + srcs.split("·")))
            idx.at[i, "마지막"] = today
        else:
            idx = pd.concat([idx, pd.DataFrame([{"종목명": name, "코드": g["코드"].iloc[0], "상태": "active", "넣은이": "기능",
                                                  "출처": srcs, "처음": today, "마지막": today, "이유": g["한줄"].iloc[0]}])],
                            ignore_index=True)
    idx = idx.fillna("")
    for i in idx.index:
        idx.at[i, "오늘순위"] = "Y" if idx.at[i, "종목명"] in on_today else ""
        idx.at[i, "순위일수"] = int(days.loc[days["종목명"] == idx.at[i, "종목명"], "날짜"].nunique())
    _save(idx, INDEX)
    _save(days, DAYS)
    return idx


def add(name: str, reason: str, by: str = "대표", today: str | None = None):
    today = today or pd.Timestamp.now(tz="Asia/Seoul").strftime("%Y-%m-%d")
    idx = _load(INDEX, INDEX_COLS)
    if (idx["종목명"] == name).any():
        i = idx.index[idx["종목명"] == name][0]
        idx.at[i, "넣은이"], idx.at[i, "상태"], idx.at[i, "이유"] = by, "active", reason
        idx.at[i, "off일"], idx.at[i, "off이유"] = "", ""
    else:
        idx = pd.concat([idx, pd.DataFrame([{"종목명": name, "상태": "active", "넣은이": by, "출처": by,
                                              "처음": today, "마지막": today, "순위일수": 0, "이유": reason}])], ignore_index=True)
    _save(idx.fillna(""), INDEX)


def note(name: str, text: str):
    idx = _load(INDEX, INDEX_COLS)
    i = idx.index[idx["종목명"] == name][0]
    idx.at[i, "메모"] = text
    _save(idx, INDEX)


def off(name: str, reason: str, today: str | None = None, by_daepyo: bool = False):
    """기능이 올린 것만 세션이 끈다. 대표가 넣은 건 by_daepyo=True(대표가 빼라 할 때)일 때만."""
    today = today or pd.Timestamp.now(tz="Asia/Seoul").strftime("%Y-%m-%d")
    idx = _load(INDEX, INDEX_COLS)
    i = idx.index[idx["종목명"] == name][0]
    if idx.at[i, "넣은이"] != "기능" and not by_daepyo:
        raise SystemExit(f"{name}은 {idx.at[i, '넣은이']}가 넣은 종목 — 대표가 빼라 할 때만 off (--daepyo)")
    idx.at[i, "상태"], idx.at[i, "off일"], idx.at[i, "off이유"] = "off", today, reason
    _save(idx, INDEX)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    a = sys.argv[1:]
    cmd = a[0] if a else "list"
    if cmd == "list":
        idx = _load(INDEX, INDEX_COLS)
        if "--all" not in a:
            idx = idx[idx["상태"] == "active"]
        print(idx[["종목명", "상태", "넣은이", "출처", "처음", "마지막", "순위일수", "오늘순위", "이유", "메모"]].to_string(index=False))
    elif cmd == "show":
        d = _load(DAYS, DAYS_COLS)
        print(d[d["종목명"] == a[1]].to_string(index=False))
    elif cmd == "add":
        by = a[a.index("--by") + 1] if "--by" in a else "대표"
        add(a[1], a[2], by=by)
    elif cmd == "note":
        note(a[1], a[2])
    elif cmd == "off":
        off(a[1], a[2], by_daepyo="--daepyo" in a)
