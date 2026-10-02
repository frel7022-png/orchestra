"""매수 전 외인 경고 — 사용자가 "a, b 살 거야"라고 하면 세션이 바로 돌린다(2026-10-02 사용자 지시).

외인 리포트(daily_link_report.compute_stats)와 같은 판정으로 각 종목의 외인 상태를 보고,
"외인이 먹고 빠지는 중(꺾임)"·착시·이탈·차익실현이면 경고한다. 결과는
morning_report/pick_warnings.csv 에 쌓아 연말에 "경고가 맞았나"를 검증하는 데 쓴다.

사용: python check_picks.py 종목명1 종목명2 ...
"""
import sys
from datetime import datetime

import pandas as pd

import daily_link_report as link
import portfolio_core as core

LOG = link.HERE / "morning_report" / "pick_warnings.csv"


def classify(s: dict) -> tuple[str, str]:
    P, dF = s["P"], s["dF"]
    if s.get("재매집") and (s.get("착시") or s.get("꺾임") or dF <= 0):
        return "CHECK", "고점 이후 크게 덜어냈지만, 최근 저점에서 다시 모으기 시작"
    if s.get("꺾임"):
        return "WARNING", "외인이 먹고 빠지는 중(꺾임)"
    if s.get("착시"):
        return "WARNING", "착시 — 외인 고점 대비 크게 반납"
    if P < 0 and dF <= 0:
        return "WARNING", "이탈 — 주가도 빠지고 외인도 빠지는 중"
    if P >= 0 and dF <= 0:
        return "WARNING", "차익실현 — 주가는 올랐는데 외인은 빠지는 중"
    if s.get("재매집"):
        return "OK", "외인이 다시 모으기 시작"
    if P < 0 and dF > 0:
        return "OK", "다이버전스 — 주가는 빠지는데 외인은 모으는 중"
    return "OK", "주가·외인 같이 오르는 중"


def main(names):
    data = link.load_data()
    ph, fh, live = data["ph"], data["fh"], data["live"]
    cand = core.compute_link_candidates(ph, fh, live_quotes=live)
    stats = link.compute_stats(cand, fh)
    by_name = {v["종목명"]: v for v in stats.values()}
    rows = []
    for n in names:
        s = by_name.get(n)
        if s is None:
            print(f"[?] {n}: 관심종목 외인 데이터 없음 — 판정 불가")
            rows.append({"날짜": data["today"], "시각": data["asof"], "종목명": n, "판정": "NODATA"})
            continue
        lvl, why = classify(s)
        g = fh[fh["종목코드"] == s["종목코드"]].sort_values("날짜")["외국인보유율"].tolist()
        d5 = g[-1] - g[-6] if len(g) >= 6 else None
        peak = s["기준외인비중"] + s["고점dF"]
        mark = {"WARNING": "⚠️ WARNING", "CHECK": "🔎 CHECK"}.get(lvl, "✅ OK")
        print(f"{mark} {n}: {why}")
        print(f"     주가 기준일 대비 {s['P']:+.1f}% · 외인 {s['기준외인비중']:.2f}% → 고점 {peak:.2f}% ({s['고점일'][5:]}) "
              f"→ 지금 {s['현재외인비중']:.2f}% ({s['외인최신일'][5:]}) · 고점 대비 반납 {s['반납률']:.0%}"
              + (f" · 최근 5거래일 {d5:+.2f}%p" if d5 is not None else ""))
        rows.append({"날짜": data["today"], "시각": data["asof"], "종목명": n, "판정": lvl, "사유": why,
                     "P": round(s["P"], 2), "dF": round(s["dF"], 2), "반납률": round(s["반납률"], 3),
                     "최근5일": round(d5, 2) if d5 is not None else None, "현재가": s["현재가"]})
    old = pd.read_csv(LOG) if LOG.exists() else pd.DataFrame()
    pd.concat([old, pd.DataFrame(rows)], ignore_index=True).to_csv(LOG, index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용: python check_picks.py 종목명1 종목명2 ...")
        sys.exit(1)
    main(sys.argv[1:])
