"""추천 사례집 — 추천한 종목마다 "그때 어땠나"를 한 줄(숫자)과 한 장(이야기)으로 남기고,
새 추천과 비슷한 옛 사례를 **목록(index)만 보고** 찾아 견준다 (2026-10-19 대표 지시).

대표: "이런 걸 쌓아두는 저장고 따로 둬서… 나중에 나에게 추천할 때 너가 이걸 참고할 수도 있고.
       단 리스트 보고 맞는 거만 매칭. 다 읽는 건 오해와 무리를 낳을 수 있음."

    python pick_cases.py build                 # picks_log + manual.csv의 사례마다 그 추천일까지 데이터로 특징 계산 → index.csv·shapes.csv
    python pick_cases.py similar 종목명 [날짜]   # 그 종목의 그날(기본 오늘) 모습과 비슷한 옛 사례: 모양 상위·특징 상위 따로
    python pick_cases.py add 종목명 날짜 "출처" "의도"   # picks_log 밖의 추천(리포트에서 짚은 것 등)을 사례로 등록

저장고 pick_cases/
- index.csv   사례 한 줄 = 특징(아래). **이것만 보고 고른다.**
- shapes.csv  사례별 직전 SHAPE_DAYS 거래일 종가 모양(마지막 = 1로 맞춤)
- manual.csv  picks_log 밖에서 들어온 추천(종목명, 날짜, 출처, 의도)
- cases/날짜_종목명.md  사례 한 장(이야기: 추천 의도, 그때 상황, 벌어진 일, 대표와 나눈 말). 고른 것만 연다.

특징(전부 추천일 d0 **포함 그 이전** 확정 데이터만 — 그 뒤는 보지 않는다):
- r20·r60: 20·60거래일 수익률 / dd60: 60거래일 고점 대비 / sig20: 20거래일 하루 변동 σ
- 외인율: d0 외국인보유율 / dF20: 20거래일 외인 %p 변화 / 외인몫10·기관몫10: 최근 10거래일 순매수 ÷ 거래량
- 칸20: (r20 부호 × dF20 부호) 4분면 / 시총억: 발행주식수(써스데이 shares.csv) × d0 종가 / 섹터·시장
결과 숫자(초과 수익)는 여기 안 둔다 — `picks_score.csv`·`tracking/`에서 그때그때 읽는다(숫자 한 곳 원칙).
"""
import math
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

import portfolio_core as core
import picks

HERE = Path(__file__).parent
DIR = HERE / "pick_cases"
INDEX = DIR / "index.csv"
SHAPES = DIR / "shapes.csv"
MANUAL = DIR / "manual.csv"
CASES = DIR / "cases"
SHARES = HERE.parent / "Thursday" / "data" / "shares.csv"
SHAPE_DAYS = 60
FEATS = ["r20", "r60", "dd60", "sig20", "dF20", "외인몫10", "기관몫10", "log시총"]
TOP_N = 5


def _closes(code: str, d0: str) -> list[tuple[str, float]]:
    start = (date.fromisoformat(d0) - timedelta(days=200)).isoformat()
    rows = core.fetch_daily_price_history(code, start, d0)
    return [(r["날짜"], float(r["종가"])) for r in rows if r["날짜"] <= d0 and r.get("거래량", 1) > 0]


def features(closes: list[tuple[str, float]], flow: pd.DataFrame, d0: str, shares: float | None) -> tuple[dict, list[float]]:
    """종가 [(날짜, 종가)]와 그 종목 investor_flow → (특징, 모양). d0 이후 행은 무시한다. 순수 함수."""
    c = [p for d, p in closes if d <= d0]
    f = flow[flow["날짜"] <= d0].sort_values("날짜") if not flow.empty else flow
    out = {}
    if len(c) >= 21:
        out["r20"] = (c[-1] / c[-21] - 1) * 100
        lr = [math.log(c[i] / c[i - 1]) for i in range(len(c) - 20, len(c))]
        m = sum(lr) / len(lr)
        out["sig20"] = math.sqrt(sum((x - m) ** 2 for x in lr) / len(lr)) * 100
    if len(c) >= 61:
        out["r60"] = (c[-1] / c[-61] - 1) * 100
    if c:
        out["dd60"] = (c[-1] / max(c[-60:]) - 1) * 100
        if shares:
            out["시총억"] = shares * c[-1] / 1e8
            out["log시총"] = math.log10(out["시총억"])
    if not f.empty:
        fp = pd.to_numeric(f["외국인보유율"], errors="coerce").dropna()
        if len(fp):
            out["외인율"] = float(fp.iloc[-1])
        if len(fp) >= 21:
            out["dF20"] = float(fp.iloc[-1] - fp.iloc[-21])
        t = f.tail(10)
        vol = pd.to_numeric(t["거래량"], errors="coerce").sum()
        if vol:
            out["외인몫10"] = pd.to_numeric(t["외국인순매수"], errors="coerce").sum() / vol * 100
            out["기관몫10"] = pd.to_numeric(t["기관순매수"], errors="coerce").sum() / vol * 100
    if "r20" in out and "dF20" in out:
        out["칸20"] = core._link_quadrant(out["r20"], out["dF20"])
    shape = [p / c[-1] for p in c[-SHAPE_DAYS:]] if len(c) >= SHAPE_DAYS else []
    return out, shape


def _shares_map() -> dict:
    if not SHARES.exists():
        return {}
    s = pd.read_csv(SHARES, dtype={"종목코드": str})
    return dict(zip(s["종목코드"], pd.to_numeric(s["발행주식수"], errors="coerce")))


def _flow_all() -> pd.DataFrame:
    import tomllib
    sec = tomllib.load(open(HERE / ".streamlit/secrets.toml", "rb"))["supabase"]
    return core.load_investor_flow_db(sec["url"], sec.get("anon_key") or sec.get("key"))


def _sources() -> pd.DataFrame:
    log = picks.load_log()
    a = log.sort_values(["날짜", "시각"]).drop_duplicates(["종목코드", "날짜"])
    a = pd.DataFrame({"종목명": a["종목명"], "종목코드": a["종목코드"].astype(str), "날짜": a["날짜"],
                      "출처": "picks " + a["회차"].astype(str), "의도": a["근거"]})
    if MANUAL.exists():
        m = pd.read_csv(MANUAL, dtype=str)
        n2c = picks._name2code()
        m["종목코드"] = m["종목명"].map(n2c)
        a = pd.concat([a, m[["종목명", "종목코드", "날짜", "출처", "의도"]]], ignore_index=True)
    return a.dropna(subset=["종목코드"]).drop_duplicates(["종목코드", "날짜"], keep="last")


def _stub(r: dict) -> None:
    CASES.mkdir(parents=True, exist_ok=True)
    p = CASES / f"{r['날짜']}_{r['종목명'].replace('/', '_')}.md"
    if p.exists():
        return
    p.write_text(f"""# {r['종목명']} — {r['날짜']} 추천 사례

출처: {r['출처']}
숫자(그때 특징): `pick_cases/index.csv`의 이 행 · 결과: `picks_score.csv`·`tracking/` (여기 숫자를 옮겨 적지 않는다)

## 추천 의도
{r['의도']}

## 그때 상황 (한두 줄, 그래프 모양·누가 받고 있었나)

## 벌어진 일 (날짜와 함께 덧붙인다)

## 대표와 나눈 말

## 견줘 본 사례 (similar로 찾은 것 중 실제로 견준 것만)
""", encoding="utf-8")


def build() -> pd.DataFrame:
    DIR.mkdir(exist_ok=True)
    src, fh, sh = _sources(), _flow_all(), _shares_map()
    sec = core.load_sector_cache() if hasattr(core, "load_sector_cache") else {}
    mk = core.load_market_cache()
    rows, shp = [], []
    for _, s in src.iterrows():
        code, d0 = s["종목코드"], s["날짜"]
        feat, shape = features(_closes(code, d0), fh[fh["종목코드"].astype(str) == code], d0, sh.get(code))
        r = {"사례": f"{d0}_{s['종목명']}", "종목명": s["종목명"], "종목코드": code, "날짜": d0, "출처": s["출처"],
             "의도": s["의도"], "섹터": sec.get(s["종목명"], "") if isinstance(sec, dict) else "", "시장": mk.get(s["종목명"], ""), **feat}
        rows.append(r)
        if shape:
            shp.append({"사례": r["사례"], **{f"s{i}": v for i, v in enumerate(shape)}})
        _stub(r)
    idx = pd.DataFrame(rows)
    idx.to_csv(INDEX, index=False, encoding="utf-8-sig")
    pd.DataFrame(shp).to_csv(SHAPES, index=False, encoding="utf-8-sig")
    return idx


def rank_similar(cur: dict, cur_shape: list[float], idx: pd.DataFrame, shapes: pd.DataFrame,
                 exclude: str | None = None, top: int = TOP_N) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(모양 상위, 특징 상위) — 두 잣대를 섞지 않고 따로 낸다. 모양 = 직전 종가 모양 상관, 특징 = 표준화 거리. 순수 함수."""
    idx = idx[idx["사례"] != exclude] if exclude else idx
    by_shape = pd.DataFrame()
    if cur_shape and not shapes.empty:
        s = shapes[shapes["사례"].isin(idx["사례"])].set_index("사례")
        cs = pd.Series(cur_shape[-s.shape[1]:], index=s.columns[-len(cur_shape):])
        corr = s[cs.index].apply(lambda row: row.astype(float).corr(cs), axis=1)
        by_shape = idx.set_index("사례").loc[corr.dropna().index].assign(모양상관=corr.dropna()).sort_values("모양상관", ascending=False).head(top)
    cols = [c for c in FEATS if c in idx and cur.get(c) is not None and not pd.isna(cur.get(c))]
    by_feat = pd.DataFrame()
    if cols:
        x = idx[cols].astype(float)
        mu, sd = x.mean(), x.std().replace(0, 1).fillna(1)
        z = (x - mu) / sd
        zc = (pd.Series({c: cur[c] for c in cols}) - mu) / sd
        dist = ((z - zc) ** 2).sum(axis=1, min_count=1).pow(0.5)
        by_feat = idx.assign(특징거리=dist).dropna(subset=["특징거리"]).sort_values("특징거리").head(top).set_index("사례")
    return by_shape, by_feat


def similar(name: str, d0: str | None = None) -> None:
    d0 = d0 or core.today_kst_str()
    code = picks._name2code().get(name)
    if not code:
        print("종목코드 없음")
        return
    idx = pd.read_csv(INDEX, dtype={"종목코드": str})
    idx = idx[idx["날짜"] < d0]          # 그날 이전 사례만 — 미래 사례로 견주지 않는다
    shapes = pd.read_csv(SHAPES) if SHAPES.exists() else pd.DataFrame()
    fh = _flow_all()
    cur, shape = features(_closes(code, d0), fh[fh["종목코드"].astype(str) == code], d0, _shares_map().get(code))
    a, b = rank_similar(cur, shape, idx, shapes, exclude=f"{d0}_{name}")
    pd.set_option("display.width", 250)
    show = ["종목명", "날짜", "칸20", "r20", "dd60", "dF20", "외인몫10", "기관몫10", "시총억", "의도"]
    print(f"[{name} {d0}] " + " · ".join(f"{k} {v:.2f}" if isinstance(v, float) else f"{k} {v}" for k, v in cur.items()))
    print("\n모양이 비슷한 사례(직전 종가 모양 상관):")
    print(a[["모양상관"] + [c for c in show if c in a]].round(2).to_string() if not a.empty else "없음")
    print("\n특징이 비슷한 사례(표준화 거리):")
    print(b[["특징거리"] + [c for c in show if c in b]].round(2).to_string() if not b.empty else "없음")
    print("\n결과는 picks_score.csv·tracking/에서, 이야기는 pick_cases/cases/에서 — 고른 사례만 연다.")


def add(name: str, d0: str, src: str, why: str) -> None:
    DIR.mkdir(exist_ok=True)
    m = pd.read_csv(MANUAL, dtype=str) if MANUAL.exists() else pd.DataFrame(columns=["종목명", "날짜", "출처", "의도"])
    m = pd.concat([m[~((m["종목명"] == name) & (m["날짜"] == d0))],
                   pd.DataFrame([{"종목명": name, "날짜": d0, "출처": src, "의도": why}])], ignore_index=True)
    m.to_csv(MANUAL, index=False, encoding="utf-8-sig")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    a = sys.argv[1:]
    if a and a[0] == "build":
        idx = build()
        print(f"사례 {len(idx)}줄 → {INDEX}")
    elif a and a[0] == "similar" and len(a) >= 2:
        similar(a[1], a[2] if len(a) > 2 else None)
    elif a and a[0] == "add" and len(a) >= 5:
        add(a[1], a[2], a[3], a[4])
        print("등록. build로 index를 다시 만든다.")
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
