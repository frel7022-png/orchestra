"""관측소 바다 — 바다의 상태 (2026-10-20 대표 "세워봐", 테스트 기간 — 그려 보고 고치며 일주일).

대표 그림: 종목들은 같은 배 위에서 달린다. 바다의 물살 = 환율 추세 · 시장 외인 · 주가 전체.
물살이 거꾸로 칠 때 외인 비중이 늘거나 **그대로 버틴** 종목은 그만큼 가중치가 있다. 왜 들어왔나(예측)는 안 보고,
그 구간에 실제로 벌어진 변동(팩트)만 본다. 환율·시장 외인은 트래킹, 종목 판단은 외인 판단, 바다 대비 무게는 바라보는 눈(바다를 보고 상대적으로 평가한다).
(셀: 뇌_초안/관측소/바다.md — 재기만 한다. 대표 원문은 뇌_초안/기록관/대표_원문.md, 판단 "역풍 속 버팀"은 뇌_초안/바라보는_눈/바다를_보고_상대적으로_평가한다.md)

    python sea.py            # 환율 갱신 + 지금 바다 + 역풍 속 상대 외인 상위
    python sea.py 종목명       # 그 종목이 지금 바다 구간에서 어디쯤인가

구간은 고정 20일이 아니라 바다가 정한다: 환율 일봉에 종목 파도와 같은 지그재그(picks._pivots, 자기 하루 변동 σ × WAVE_K
되돌림 = 방향 전환)를 대서, 지금 환율이 한 방향으로 가는 구간의 시작일을 잡는다.
바다는 섞지 않고 나란히 둔다(벤치 두 개 원칙): ① 환율 ② 시장 외인 순매수(코스피·코스닥) ③ 관심종목 외인 비중 가운데 값 ④ 관심종목 주가 가운데 값.
종목을 견주는 기준은 ③(우리 종목은 비인기라 코스피 전체 외인과 물살이 다르다). 외인 비중은 주식 수 기준이라 환율로 나누지 않는다.
상대 외인 = 그 종목 외인 비중 변화(%p) − ③. 이건 표시다 — 판단(포기 조짐 등)은 그대로 대표.

출처: 환율 = 네이버 `api.stock.naver.com/marketindex/exchange/FX_USDKRW/prices`(원/달러 일별 종가), fx_history.csv에 쌓는다.
길이는 외인 DB 시작(2026-07)에 맞춘다 — 긴 과거로 소급하지 않는다(대표 10-19).
저장고: fx_history.csv · sea/state_log.csv(하루 한 줄) · sea/relative_날짜.csv(그날 종목별 상대 외인).
"""
import math
import statistics
import sys
import tomllib
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

import picks
import portfolio_core as core

HERE = Path(__file__).parent
FX_CSV = HERE / "fx_history.csv"
OUT = HERE / "sea"
FX_START = "2026-07-01"
FX_URL = "https://api.stock.naver.com/marketindex/exchange/FX_USDKRW/prices"
TOP = 15


# ---------- 환율 ----------
def fetch_fx(start: str = FX_START) -> pd.DataFrame:
    rows, page = [], 1
    while page < 20:
        try:
            r = requests.get(FX_URL, params={"page": page, "pageSize": 60},
                             headers={"User-Agent": "Mozilla/5.0"}, timeout=10).json()
        except Exception:
            break
        if not r:
            break
        rows += [(x["localTradedAt"], float(str(x["closePrice"]).replace(",", ""))) for x in r]
        if r[-1]["localTradedAt"] < start:
            break
        page += 1
    df = pd.DataFrame(rows, columns=["날짜", "원달러"])
    return df[df["날짜"] >= start].drop_duplicates("날짜").sort_values("날짜").reset_index(drop=True)


def load_fx() -> pd.DataFrame:
    return pd.read_csv(FX_CSV) if FX_CSV.exists() else pd.DataFrame(columns=["날짜", "원달러"])


def update_fx() -> pd.DataFrame:
    """받은 값으로 같은 날짜를 덮어쓴다(오늘 행은 장중값일 수 있어 다음 날 확정값으로 바뀐다)."""
    new = fetch_fx()
    old = load_fx()
    keep = old[~old["날짜"].isin(new["날짜"])]
    df = (pd.concat([keep, new]) if len(keep) else new).sort_values("날짜").reset_index(drop=True)
    if len(df):
        df.to_csv(FX_CSV, index=False)
    return df


def fx_leg(fx: pd.DataFrame) -> dict:
    """지금 환율 구간 — 마지막 전환점부터 오늘까지. {시작, 시작값, 끝, 지금값, 변화%, 방향(+1/-1), 거래일, 직전구간, 큰흐름}.
    큰흐름 = 지금 방향 기준 FX_START 이후 꼭짓점(내려가는 중이면 최고점)부터 — 작은 되돌림을 건너뛴 큰 파도.
    (2026-10-20 첫 실행: 작은 구간은 9-18부터 −4.4%, 큰 흐름은 7-22 꼭짓점부터 −10%대 — 대표가 말한 "10%"는 큰 흐름 쪽)"""
    d, c = list(fx["날짜"]), [float(x) for x in fx["원달러"]]
    if len(c) < 10:
        return {}
    r = [math.log(c[i] / c[i - 1]) for i in range(1, len(c))]
    sd = statistics.pstdev(r)
    piv = picks._pivots(c, picks.WAVE_K * sd) if sd > 0 else [0]
    i0 = piv[-1]
    if i0 == len(c) - 1 and len(piv) > 1:   # 오늘이 막 꼭짓점이면 직전 구간을 본다
        i0 = piv[-2]
    prev = None
    if len(piv) >= 2 and piv[-1] == i0:
        j = piv[-2]
        prev = {"시작": d[j], "끝": d[i0], "변화%": (c[i0] / c[j] - 1) * 100}
    chg = (c[-1] / c[i0] - 1) * 100
    k = max(range(len(c)), key=c.__getitem__) if chg < 0 else min(range(len(c)), key=c.__getitem__)
    big = {"시작": d[k], "시작값": c[k], "변화%": (c[-1] / c[k] - 1) * 100, "거래일": len(c) - 1 - k,
           "자료끝": k == 0}   # 꼭짓점이 자료 첫날이면 진짜 꼭짓점이 더 앞일 수 있다
    return {"시작": d[i0], "시작값": c[i0], "끝": d[-1], "지금값": c[-1], "변화%": chg,
            "방향": 1 if chg > 0 else -1, "거래일": len(c) - 1 - i0, "σ%": sd * 100, "직전구간": prev, "큰흐름": big}


# ---------- 시장 외인 ----------
def market_foreign(mf: pd.DataFrame, start: str, end: str) -> dict:
    """(start, end] 시장별 외인 순매수 합(억원). start 당일은 빼고 그 다음 날부터."""
    m = mf[(mf["날짜"] > start) & (mf["날짜"] <= end)]
    return {k: float(pd.to_numeric(g["외국인순매수"], errors="coerce").sum()) for k, g in m.groupby("시장")}


# ---------- 종목 상대 외인 ----------
def _at_or_after(g: pd.DataFrame, col: str, day: str):
    """day 이하 가장 최근 값, 없으면(커버리지가 늦게 시작) 그 이후 가장 이른 값 — foreign_pct_change_since와 같은 폴백."""
    b = g[g["날짜"] <= day]
    row = b.iloc[-1] if len(b) else (g.iloc[0] if len(g) else None)
    return None if row is None else (row["날짜"], float(row[col]))


def relative_foreign(fh: pd.DataFrame, start: str, ph: pd.DataFrame | None = None,
                     live: dict | None = None) -> tuple[pd.DataFrame, dict]:
    """start 이후 종목별 외인 비중 변화(%p)와 관심종목 가운데 값 대비 상대값. 주가 변화도 같이(같은 구간).
    반환: (표, {"외인중앙": %p, "주가중앙": %, "종목수": n})."""
    rows = []
    f = fh.dropna(subset=["외국인보유율"]).sort_values("날짜")
    pg = {c: g.sort_values("날짜") for c, g in ph.groupby("종목코드")} if ph is not None and len(ph) else {}
    for code, g in f.groupby("종목코드"):
        a, z = _at_or_after(g, "외국인보유율", start), g.iloc[-1]
        if a is None or a[0] >= z["날짜"]:
            continue
        r = {"종목코드": code, "종목명": z["종목명"], "기준일": a[0], "외인시작": a[1],
             "외인지금": float(z["외국인보유율"]), "dF": float(z["외국인보유율"]) - a[1]}
        p = pg.get(code)
        if p is not None and len(p):
            b = _at_or_after(p, "종가", start)
            now = (live or {}).get(code) or float(p.iloc[-1]["종가"])
            if b and b[1] > 0:
                r["주가%"] = (now / b[1] - 1) * 100
        rows.append(r)
    df = pd.DataFrame(rows)
    if df.empty:
        return df, {}
    med = core.universe_center(df["dF"])["가운데"]   # 바다 비교 = 가운데 값(정의는 core 한 곳)
    df["상대외인"] = df["dF"] - med
    df["외인변화율%"] = df["dF"] / df["외인시작"].where(df["외인시작"] > 0) * 100   # 비중이 낮은 종목은 %p가 작게 나와서 같이 본다
    df["버팀"] = (df["dF"] >= 0) & (med < 0)   # 바다가 외인을 빼는데 이 종목은 안 빠짐 — 지금은 "안 움직인 종목"이라 다른 보고엔 안 잡힌다
    info = {"외인중앙": med, "종목수": len(df)}
    if "주가%" in df:
        info["주가중앙"] = core.universe_center(df["주가%"])["가운데"]
    return df.sort_values("상대외인", ascending=False).reset_index(drop=True), info


def tide(leg: dict, mkt: dict) -> str:
    """바다 이름표(판단 아님): 환율 방향 × 시장 외인 합 부호."""
    if not leg:
        return "모름"
    fsum = sum(mkt.values()) if mkt else 0.0
    if leg["방향"] < 0 and fsum < 0:
        return "역풍(환율↓·외인 이탈)"
    if leg["방향"] > 0 and fsum > 0:
        return "순풍(환율↑·외인 유입)"
    return f"엇갈림(환율{'↑' if leg['방향'] > 0 else '↓'}·외인 {'유입' if fsum > 0 else '이탈'})"


# ---------- 주가 전체(지수) · 배(내 계좌) ----------
def index_moves(idx: pd.DataFrame, bigdf: pd.DataFrame, wk: float, start: str) -> dict:
    """start 종가(그날 이하 가장 최근) → 마지막 행(오늘은 실시간) 변화 %. 코스피·코스닥·혼합(일반)·혼합(ex).
    idx·bigdf는 live_check.live_snapshot이 만든 것 그대로(확정 종가 + 오늘 실시간) — 계산을 따로 만들지 않는다."""
    ex = core.synthetic_kospi_ex_bigcap(idx, bigdf)

    def chg(df, col):
        b = df[df["날짜"] <= start]
        a0 = float((b if len(b) else df).iloc[-1 if len(b) else 0][col])
        return (float(df[col].iloc[-1]) / a0 - 1) * 100

    kp, kq, kx = chg(idx, "KOSPI"), chg(idx, "KOSDAQ"), chg(ex, "KOSPI")
    return {"코스피": kp, "코스닥": kq, "혼합(일반)": wk * kp + (1 - wk) * kq, "혼합(ex)": wk * kx + (1 - wk) * kq}


def ship(snap: dict, start: str) -> dict:
    """배 = 내 계좌·내 주식. start 이후 변화 %(앱 '내 계좌·내 주식'과 같은 정의 — compute_index_vs_account의 me 기준).
    바다가 아니다. 계좌를 벤치와 겨루는 판정은 누구와_싸우나(위크·대표 2026-10-20)."""
    me = snap.get("me")
    out = {}
    if me is None or me.empty:
        return out
    b = me[me["날짜"] <= start]
    if not len(b):
        return out
    for k, col in (("내 계좌", "acct"), ("내 주식", "stock")):
        now = snap[col][0]
        base = float(b[{"acct": "계좌수익", "stock": "주식수익"}[col]].iloc[-1])
        if now is not None:
            out[k] = ((1 + now) / (1 + base) - 1) * 100
    return out


# ---------- 실행 ----------
def load_data() -> dict:
    sec = tomllib.load(open(HERE / ".streamlit/secrets.toml", "rb"))["supabase"]
    url, key = sec["url"], sec.get("anon_key") or sec.get("key")
    ph = core.load_watchlist_history_db(url, key)
    fh = core.load_investor_flow_db(url, key)
    mf = core.load_market_flow_db(url, key)
    quotes, _ = core.fetch_quotes(list(ph["종목코드"].unique()))
    live = {c: v["price"] for c, v in quotes.items() if v.get("price")}
    return {"ph": ph, "fh": fh, "mf": mf, "live": live}


def state(data: dict | None = None, fx: pd.DataFrame | None = None) -> dict:
    data = data or load_data()
    fx = fx if fx is not None else update_fx()
    leg = fx_leg(fx)
    if not leg:
        return {}
    end = max(data["fh"]["날짜"])
    mkt = market_foreign(data["mf"], leg["시작"], end)
    rel, info = relative_foreign(data["fh"], leg["시작"], data["ph"], data["live"])
    big = leg["큰흐름"]
    big_mkt = market_foreign(data["mf"], big["시작"], end)
    big_rel, big_info = relative_foreign(data["fh"], big["시작"], data["ph"], data["live"])
    out = {"leg": leg, "mkt": mkt, "rel": rel, "info": info, "tide": tide(leg, mkt), "end": end,
           "big": {"mkt": big_mkt, "rel": big_rel, "info": big_info}}
    try:
        import live_check
        snap = data.get("snap") or live_check.live_snapshot()
        for k, st in ((None, leg["시작"]), ("big", big["시작"])):
            d = out if k is None else out["big"]
            d["idx"] = index_moves(snap["idx"], snap["bigdf"], snap["wk"], st)
            d["idx_from"] = max(st, str(snap["idx"]["날짜"].iloc[0]))   # 지수 자료는 index_history 시작(8/14)부터
            d["ship"] = ship(snap, st)
    except Exception as e:   # 지수·계좌는 덤 — 실패해도 바다(환율·외인)는 낸다
        out["idx_err"] = str(e)
    return out


def save(s: dict) -> None:
    OUT.mkdir(exist_ok=True)
    today = datetime.now(core.KST).strftime("%Y-%m-%d")
    leg, info, mkt = s["leg"], s["info"], s["mkt"]
    row = {"날짜": today, "바다": s["tide"], "환율구간시작": leg["시작"], "환율시작": leg["시작값"], "환율지금": leg["지금값"],
           "환율변화%": round(leg["변화%"], 2), "구간거래일": leg["거래일"],
           "코스피외인억": round(mkt.get("KOSPI", 0)), "코스닥외인억": round(mkt.get("KOSDAQ", 0)),
           "관심외인중앙pp": round(info.get("외인중앙", 0), 3), "관심주가중앙%": round(info.get("주가중앙", 0), 2),
           "버팀수": int(s["rel"]["버팀"].sum()), "외인기준일": s["end"],
           "큰흐름시작": leg["큰흐름"]["시작"], "큰흐름환율변화%": round(leg["큰흐름"]["변화%"], 2),
           "큰흐름코스피외인억": round(s["big"]["mkt"].get("KOSPI", 0)),
           "큰흐름관심외인중앙pp": round(s["big"]["info"].get("외인중앙", 0), 3),
           "큰흐름관심주가중앙%": round(s["big"]["info"].get("주가중앙", 0), 2),
           **{f"{k}%": round(v, 2) for k, v in s.get("idx", {}).items()},
           **{f"{k}%": round(v, 2) for k, v in s.get("ship", {}).items()}}
    log = OUT / "state_log.csv"
    old = pd.read_csv(log) if log.exists() else pd.DataFrame(columns=list(row))
    keep = old[old["날짜"] != today]
    (pd.concat([keep, pd.DataFrame([row])]) if len(keep) else pd.DataFrame([row])).to_csv(
        log, index=False, encoding="utf-8-sig")
    s["rel"].to_csv(OUT / f"relative_{today}.csv", index=False, encoding="utf-8-sig")
    s["big"]["rel"].to_csv(OUT / f"relative_big_{today}.csv", index=False, encoding="utf-8-sig")


def report(s: dict, name: str | None = None) -> None:
    leg, mkt, info, rel = s["leg"], s["mkt"], s["info"], s["rel"]
    print(f"[바다] {s['tide']}  (외인 데이터 {s['end']}까지)")
    print(f"  환율  {leg['시작']} {leg['시작값']:,.1f} → {leg['끝']} {leg['지금값']:,.1f}  {leg['변화%']:+.1f}%  "
          f"({leg['거래일']}거래일, 하루 σ {leg['σ%']:.2f}%)")
    if leg.get("직전구간"):
        p = leg["직전구간"]
        print(f"        직전 구간 {p['시작']} → {p['끝']} {p['변화%']:+.1f}%")
    print("  시장 외인 순매수  " + " · ".join(f"{k} {v:+,.0f}억" for k, v in sorted(mkt.items())))
    print(f"  관심종목 {info.get('종목수', 0)}개  외인 비중 가운데 {info.get('외인중앙', 0):+.2f}%p · "
          f"주가 가운데 {info.get('주가중앙', float('nan')):+.1f}%  · 버팀 {int(rel['버팀'].sum())}종목")
    _idx_ship(s)
    _table(rel, name, f"작은 구간({leg['시작']}~) 상대 외인")
    b, bi = leg["큰흐름"], s["big"]["info"]
    edge = " (자료 첫날 — 진짜 꼭짓점은 더 앞일 수 있음)" if b.get("자료끝") else ""
    print(f"\n[큰 흐름] 환율 {b['시작']} {b['시작값']:,.1f} → {leg['지금값']:,.1f}  {b['변화%']:+.1f}% ({b['거래일']}거래일){edge}")
    print("  시장 외인 순매수  " + " · ".join(f"{k} {v:+,.0f}억" for k, v in sorted(s["big"]["mkt"].items())))
    print(f"  관심종목 외인 비중 가운데 {bi.get('외인중앙', 0):+.2f}%p · 주가 가운데 {bi.get('주가중앙', float('nan')):+.1f}%")
    _idx_ship(s["big"])
    _table(s["big"]["rel"], name, f"큰 흐름({b['시작']}~) 상대 외인")


def _idx_ship(d: dict) -> None:
    if d.get("idx"):
        print(f"  지수({d.get('idx_from', '')}~)  " + " · ".join(f"{k} {v:+.1f}%" for k, v in d["idx"].items()))
    if d.get("ship"):
        print("  배(바다 아님, 겨루기는 누구와_싸우나)  " + " · ".join(f"{k} {v:+.1f}%" for k, v in d["ship"].items()))


def _table(rel: pd.DataFrame, name: str | None, title: str) -> None:
    show = rel[rel["종목명"] == name] if name else rel.head(TOP)
    print(f"\n  {title}")
    print(f"  {'종목':<14}{'외인%p':>8}{'상대%p':>8}{'변화율%':>8}{'주가%':>8}  비중")
    for _, r in show.iterrows():
        mark = " 버팀" if r["버팀"] else ""
        px = f"{r['주가%']:+.1f}" if pd.notna(r.get("주가%")) else "-"
        rr = f"{r['외인변화율%']:+.0f}" if pd.notna(r.get("외인변화율%")) else "-"
        print(f"  {r['종목명']:<14}{r['dF']:+8.2f}{r['상대외인']:+8.2f}{rr:>8}{px:>8}  {r['외인시작']:.1f}→{r['외인지금']:.1f}{mark}")


def main() -> int:
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    s = state()
    if not s:
        print("환율 데이터 부족")
        return 1
    save(s)
    report(s, sys.argv[1] if len(sys.argv) > 1 else None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
