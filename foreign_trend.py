"""외인 추이 — 관심종목 전체 대비 외인 비중이 어긋난 종목 (Link 리포트 칸, 대표 확정 2026-10-22).

대표 원문(위크 전달): 「창은 최근 5거래일 /20 거래일 /60 거래일/ 누적 거래일 → 한 그래프에 표기, 눈으로 확인할 수
있게. 구획은 막대(즉, 선)로 나누기 · 전체 중앙값이라 함은 대상 종목 전체의 평균 외인 변화(5·20·60·누적 순으로) ·
a 스펙 맞아(Link에 칸으로) · 외인 추이는 둘 다」

- 대상: 외인 비중 자료가 있는 관심종목(비중 0·자료 없음 제외). 창 = 최근 5·20·60거래일·누적(그 종목 가격추적 시작일부터).
- 기준선 = 대상 종목 전체의 **평균** 외인 변화(창마다 따로). 머리엔 평균·중앙값·오른/내린 수.
- 두 층(대표: 「원천적인 외인 비중이 ~% 늘었다 … 비중이 들어간 건 심화분석」):
  - 기본층 = 원천 변화(시작 대비 %, 비중 크기로 가중 안 함) − 평균 → 위·아래 TOP_N.
  - 심화층 = 영향력 %p(총 주식 수 대비 몫). |%p| ≥ 전체 |%p| 중앙값 × IMPACT_FLOOR로 0 근처를 거르고 %p − 평균으로 줄 세움.
    σ는 거르는 문이 아니라 "평소와 다름" 칸(|σ배| ≥ SIGMA_FLAG). 걸린 게 TOP_N보다 적으면 바닥 밑에서 |%p| 큰 순으로
    채우고 "가까운 순"으로 표시(대표 원칙 — 문턱에 걸린 게 없으면 가장 가까운 것).
- 극단 이탈 감시: 외인 비중이 자기 비중 대비 WATCH_DROP% 이하로 하루에 또는 WATCH_DAYS거래일 동안 빠진 종목.
  리포트에 후보로 보이고, 트래킹 등록(picks_log 구분 "감시")은 세션 장부 걸음으로 한다 — 리포트는 장부를 안 쓴다.
숫자(창·TOP_N·IMPACT_FLOOR·SIGMA_FLAG·SIGMA_N·WATCH_DROP·WATCH_DAYS)의 지금 값·이력은 뇌_초안 데이터센터/세팅 선반 1.
"""
import math

import pandas as pd

WINDOWS = (5, 20, 60, None)    # None = 누적(그 종목 가격추적 시작일부터)
TOP_N = 10
IMPACT_FLOOR = 1.0             # 심화층 바닥: |%p| ≥ 전체 |%p| 중앙값 × 이 값
SIGMA_FLAG = 2.0               # "평소와 다름" 표시: |σ배| ≥ 이 값
SIGMA_N = 20                   # σ_f = 창 시작 전 하루 %p 변화 이 개수의 표본표준편차
WATCH_DROP = -10.0             # 극단 이탈: 자기 비중 대비 이 % 이하
WATCH_DAYS = 5                 # 극단 이탈: 하루 또는 이 거래일 동안


def window_label(w) -> str:
    return "누적" if w is None else f"{w}거래일"


def _pivot(fh: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    g = fh.dropna(subset=["외국인보유율"])
    piv = g.pivot_table(index="날짜", columns="종목코드", values="외국인보유율", aggfunc="last").sort_index()
    names = g.drop_duplicates("종목코드", keep="last").set_index("종목코드")["종목명"].to_dict()
    return piv.astype(float), names


def _origin(ph: pd.DataFrame | None) -> dict:
    """종목별 가격추적 시작일(Fishing·Link·Foreigner 기준일과 같은 뿌리)."""
    if ph is None or ph.empty:
        return {}
    return ph.groupby("종목코드")["날짜"].min().to_dict()


def window_changes(fh: pd.DataFrame, ph: pd.DataFrame | None, window) -> tuple[pd.DataFrame, dict]:
    """창 하나의 종목별 외인 변화와 머리 숫자. window=None이면 누적."""
    cols = ["종목코드", "종목명", "시작일", "시작", "끝", "pp", "rel", "sigma", "n", "dev_rel", "dev_pp", "영향력", "z", "평소와다름"]
    piv, names = _pivot(fh)
    if piv.empty:
        return pd.DataFrame(columns=cols), {}
    dates = list(piv.index)
    end = dates[-1]
    origin = _origin(ph)
    rows = []
    for code in piv.columns:
        s = piv[code].dropna()
        if s.empty or s.index[-1] != end:
            continue
        if window is None:
            o = origin.get(code)
            ss = s[s.index >= o] if o else s
            if len(ss) < 2:
                continue
            start = ss.index[0]
        else:
            if len(dates) <= window:
                continue
            start = dates[-1 - window]
            if start not in s.index:
                continue
        v0, v1 = float(s[start]), float(s[end])
        if v0 <= 0 or v1 <= 0:
            continue
        diffs = s.diff().dropna()
        before = diffs[diffs.index <= start].tail(SIGMA_N)
        if len(before) < 5:
            before = diffs.tail(SIGMA_N)
        sigma = float(before.std(ddof=1)) if len(before) >= 2 else float("nan")
        n = int(((s.index > start) & (s.index <= end)).sum())
        rows.append({"종목코드": code, "종목명": names.get(code, code), "시작일": start, "시작": v0, "끝": v1,
                     "pp": v1 - v0, "rel": (v1 / v0 - 1) * 100, "sigma": sigma, "n": n})
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=cols), {}
    mean_rel, mean_pp = df["rel"].mean(), df["pp"].mean()
    med_abs = df["pp"].abs().median()
    df["dev_rel"] = df["rel"] - mean_rel
    df["dev_pp"] = df["pp"] - mean_pp
    df["영향력"] = df["pp"] / med_abs if med_abs > 0 else float("nan")
    df["z"] = [d / (sg * math.sqrt(max(k, 1))) if sg and sg > 0 else float("nan")
               for d, sg, k in zip(df["dev_pp"], df["sigma"], df["n"])]
    df["평소와다름"] = df["z"].abs() >= SIGMA_FLAG
    head = {"창": window_label(window), "끝": end,
            "시작": (min(df["시작일"]) if window is None else df["시작일"].iloc[0]),
            "종목수": len(df), "평균rel": mean_rel, "중앙rel": df["rel"].median(),
            "평균pp": mean_pp, "중앙pp": df["pp"].median(), "중앙절대pp": med_abs,
            "오름": int((df["pp"] > 0).sum()), "내림": int((df["pp"] < 0).sum()), "그대로": int((df["pp"] == 0).sum())}
    return df[cols], head


def basic_layer(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """기본층 — 원천 변화(%) − 평균, 위·아래 TOP_N."""
    if df.empty:
        return df, df
    return df.nlargest(TOP_N, "dev_rel"), df.nsmallest(TOP_N, "dev_rel")


def deep_layer(df: pd.DataFrame, med_abs: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    """심화층 — 영향력 바닥으로 거르고 %p − 평균으로 줄 세움. 모자라면 바닥 밑에서 |%p| 큰 순으로 채움(가까운 순 표시)."""
    out = []
    for sign in (1, -1):
        side = df[(df["dev_pp"] * sign) > 0].copy()
        passed = side[side["pp"].abs() >= IMPACT_FLOOR * med_abs].copy()
        passed["가까운순"] = False
        passed = passed.sort_values("dev_pp", ascending=(sign < 0)).head(TOP_N)
        if len(passed) < TOP_N:
            rest = side[side["pp"].abs() < IMPACT_FLOOR * med_abs].copy()
            rest["가까운순"] = True
            rest = rest.reindex(rest["pp"].abs().sort_values(ascending=False).index).head(TOP_N - len(passed))
            passed = pd.concat([passed, rest])
        out.append(passed)
    return out[0], out[1]


def extreme_exits(fh: pd.DataFrame) -> pd.DataFrame:
    """극단 이탈 — 자기 비중 대비 WATCH_DROP% 이하로 하루에, 또는 WATCH_DAYS거래일 동안 빠진 종목(최신일 기준)."""
    cols = ["종목코드", "종목명", "종류", "시작일", "시작", "끝", "rel", "pp"]
    piv, names = _pivot(fh)
    if piv.empty:
        return pd.DataFrame(columns=cols)
    dates = list(piv.index)
    end = dates[-1]
    rows = []
    for code in piv.columns:
        s = piv[code].dropna()
        if s.empty or s.index[-1] != end:
            continue
        v1 = float(s[end])
        hits = []
        if len(dates) >= 2 and dates[-2] in s.index:
            hits.append(("하루", dates[-2]))
        if len(dates) > WATCH_DAYS and dates[-1 - WATCH_DAYS] in s.index:
            hits.append((f"{WATCH_DAYS}거래일", dates[-1 - WATCH_DAYS]))
        for kind, d0 in hits:
            v0 = float(s[d0])
            if v0 <= 0:
                continue
            rel = (v1 / v0 - 1) * 100
            if rel <= WATCH_DROP:
                rows.append({"종목코드": code, "종목명": names.get(code, code), "종류": kind, "시작일": d0,
                             "시작": v0, "끝": v1, "rel": rel, "pp": v1 - v0})
    df = pd.DataFrame(rows, columns=cols)
    return df.sort_values("rel") if not df.empty else df


def market_series(fh: pd.DataFrame, ph: pd.DataFrame | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """전체 그래프용 — 날짜별 대상 종목 평균 외인 변화(그 종목 누적 시작 대비 %)와 평균 주가 변화(%).
    svg_chart(ps, fs)에 그대로 넣을 꼴: ps(날짜, 종가 = 100 × (1 + 평균 주가%/100)), fs(날짜, 외국인보유율 = 평균 외인 변화 %)."""
    piv, _ = _pivot(fh)
    origin = _origin(ph)
    rel_cols = []
    for code in piv.columns:
        s = piv[code].dropna()
        o = origin.get(code)
        s = s[s.index >= o] if o else s
        if len(s) < 2 or s.iloc[0] <= 0:
            continue
        rel_cols.append((s / s.iloc[0] - 1) * 100)
    fs = (pd.concat(rel_cols, axis=1).mean(axis=1).rename("외국인보유율").reset_index()
          .rename(columns={"index": "날짜"}) if rel_cols else pd.DataFrame(columns=["날짜", "외국인보유율"]))
    ps = pd.DataFrame(columns=["날짜", "종가"])
    if ph is not None and not ph.empty:
        pp_ = ph.dropna(subset=["종가"]).pivot_table(index="날짜", columns="종목코드", values="종가", aggfunc="last").sort_index()
        pc = []
        for code in pp_.columns:
            s = pp_[code].dropna().astype(float)
            if len(s) >= 2 and s.iloc[0] > 0:
                pc.append((s / s.iloc[0] - 1) * 100)
        if pc:
            m = pd.concat(pc, axis=1).mean(axis=1)
            ps = pd.DataFrame({"날짜": m.index, "종가": 100 * (1 + m.values / 100)})
    if not fs.empty and not ps.empty:
        ps = ps[ps["날짜"] >= fs["날짜"].iloc[0]].reset_index(drop=True)
    return ps, fs


def window_starts(fh: pd.DataFrame) -> list[str]:
    """그래프 세로선 자리 — 60·20·5거래일 창의 시작일(있는 것만)."""
    piv, _ = _pivot(fh)
    dates = list(piv.index)
    return [dates[-1 - w] for w in WINDOWS if w is not None and len(dates) > w]
