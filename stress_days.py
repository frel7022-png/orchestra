"""폭락일 성적표 — "누가 버텼고, 누가 받았나" (2026-10-16 대표 승인).

동국제강(10/15 시장 −7.6%에 −3%, 그날 기관 10억 순매수 → 다음날 +12%)·에스티팜(폭락일에도 외인 순매수)에서 나온 가설:
시장이 크게 빠진 날 덜 빠지고, 그날 외인·기관이 물량을 받아낸 종목은 이후 시장보다 낫다.

스트레스 데이 = 혼합지수(삼성·하이닉스 제외, 코스피 비중 wk) 당일 ≤ −2%. 문턱 근처 −1.8~−2.2%도 열어 둔다(대표 지시):
−1.8% 이하면 모두 넣고, −1.8~−2.2%는 '경계'로 표시한다.

    python stress_days.py            # 스트레스 데이 목록 + 날마다 '버팀+받음' 상위 + 이후 성적, CSV 저장
    python stress_days.py 2026-10-15 # 그날만 자세히

저장: stress_days.csv(날짜·벤치·경계) / stress_scores.csv(날짜×종목: 상대낙폭·외인몫·기관몫·받음·이후 +1/+3/+5일·지금 초과).
- 상대낙폭 = 종목 당일 − 벤치 당일(%p). 클수록 덜 빠짐.
- 받음% = (외인순매수 + 기관순매수) ÷ 그날 거래량 × 100. 외인몫·기관몫은 따로도 남긴다.
- 버팀점수 = 상대낙폭(%p) + 받음%/10 — 정렬용 단순 합. 가중치는 결과 보며 대표와 조정.
- 이후 초과 = 종목 수익(스트레스 데이 종가 → +N거래일 종가) − 같은 구간 벤치(%p). '지금'은 실시간.
데이터: price_history·investor_flow(Supabase, daily_link_report.load_data 재사용), index_history·bigcap_history(확정 종가).
"""
import sys
from pathlib import Path

import pandas as pd

import portfolio_core as core
import picks

HERE = Path(__file__).parent
DAYS_CSV = HERE / "stress_days.csv"
SCORES_CSV = HERE / "stress_scores.csv"
CUT, EDGE = -1.8, -2.2
FWD = (1, 3, 5)


def bench_daily(closes: dict, wk: float) -> pd.Series:
    """{날짜: 혼합지수(ex) 당일 %}"""
    ds = sorted(closes)
    return pd.Series({d1: (wk * (closes[d1][0] / closes[d0][0] - 1) + (1 - wk) * (closes[d1][1] / closes[d0][1] - 1)) * 100
                      for d0, d1 in zip(ds, ds[1:])})


def build(data: dict | None = None):
    closes, now = picks.bench_levels(live=True)
    wk = picks._wk()
    bd = bench_daily(closes, wk)
    days = bd[bd <= CUT]
    if data is None:
        import daily_link_report as link
        data = link.load_data()
    ph, fh, live = data["ph"], data["fh"], data["live"]
    px = ph.pivot_table(index="날짜", columns="종목코드", values="종가").sort_index()
    names = ph.drop_duplicates("종목코드", keep="last").set_index("종목코드")["종목명"]
    dates = list(px.index)
    fl = fh.set_index(["날짜", "종목코드"])
    ds = sorted(closes)

    def bret(d0, d1):
        b0, b1 = closes[d0], (now if d1 == "지금" else closes[d1])
        return (wk * (b1[0] / b0[0] - 1) + (1 - wk) * (b1[1] / b0[1] - 1)) * 100

    rows = []
    for d, b in days.items():
        if d not in px.index:
            continue
        i = dates.index(d)
        if i == 0:
            continue
        prev = px.iloc[i - 1]
        for c in px.columns:
            p0, p1 = prev.get(c), px.at[d, c]
            if pd.isna(p0) or pd.isna(p1) or not p0:
                continue
            r = {"날짜": d, "종목코드": c, "종목명": names.get(c, c), "벤치%": b, "종목%": (p1 / p0 - 1) * 100}
            r["상대낙폭%p"] = r["종목%"] - b
            if (d, c) in fl.index:
                f = fl.loc[(d, c)]
                vol = float(pd.to_numeric(f.get("거래량"), errors="coerce") or 0)
                fn = float(pd.to_numeric(f.get("외국인순매수"), errors="coerce") or 0)
                inn = float(pd.to_numeric(f.get("기관순매수"), errors="coerce") or 0)
                if vol:
                    r.update({"외인몫%": fn / vol * 100, "기관몫%": inn / vol * 100, "받음%": (fn + inn) / vol * 100})
            r["버팀점수"] = r["상대낙폭%p"] + r.get("받음%", 0) / 10
            later = [x for x in ds if x > d]
            for h in FWD:
                if len(later) >= h and later[h - 1] in px.index:
                    pp = px.at[later[h - 1], c]
                    if pd.notna(pp):
                        r[f"+{h}일초과%p"] = (pp / p1 - 1) * 100 - bret(d, later[h - 1])
            if now and live.get(c):
                r["지금초과%p"] = (live[c] / p1 - 1) * 100 - bret(d, "지금")
            rows.append(r)
    sc = pd.DataFrame(rows)
    dd = pd.DataFrame({"날짜": days.index, "벤치%": days.values, "경계": days.values > EDGE})
    return dd, sc, wk


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    only = sys.argv[1] if len(sys.argv) > 1 else None
    dd, sc, wk = build()
    dd.to_csv(DAYS_CSV, index=False, encoding="utf-8-sig")
    sc.to_csv(SCORES_CSV, index=False, encoding="utf-8-sig")
    pd.set_option("display.width", 250)
    print(f"스트레스 데이(혼합지수 ex 당일 ≤ {CUT}%, 코스피 비중 {wk:.0%}): {len(dd)}일")
    for _, d in dd.iterrows():
        if only and d["날짜"] != only:
            continue
        g = sc[sc["날짜"] == d["날짜"]].sort_values("버팀점수", ascending=False)
        tag = " (경계)" if d["경계"] else ""
        print(f"\n== {d['날짜']} 벤치 {d['벤치%']:+.2f}%{tag} · {len(g)}종목")
        cols = [c for c in ["종목명", "종목%", "상대낙폭%p", "외인몫%", "기관몫%", "받음%", "버팀점수",
                            "+1일초과%p", "+3일초과%p", "+5일초과%p", "지금초과%p"] if c in g]
        print(g[cols].head(12 if only else 6).round(2).to_string(index=False))
        # 가설 검증: 버팀점수 상위 1/5 vs 하위 1/5의 이후 초과
        n = max(len(g) // 5, 1)
        for col in ("+1일초과%p", "+3일초과%p", "+5일초과%p", "지금초과%p"):
            if col in g and g[col].notna().sum() >= 2 * n:
                top, bot = g.head(n)[col].mean(), g.tail(n)[col].mean()
                print(f"  {col}: 버팀 상위 {n}개 평균 {top:+.2f} · 하위 {n}개 {bot:+.2f} · 차이 {top - bot:+.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
