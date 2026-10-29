"""Basecamp — 꾸준히 빠지다 평지에서 짐(물량)을 꾸리는 종목 (2026-10-13 신설, Volume 패널 자리).

사용자 가설: 꾸준히 빠지다가 바닥을 다지는데, 그 평지 구간에서 거래량이 평소보다 2~3배로 꾸준히
터진다면 누군가 던지는 물량(악재·공매도 물량 등)을 가격 안 올리고 받아내는 중 = 매집 신호.

문턱으로 거르지 않고(하락률 10%든 20%든 열어둠) Fishing·Up/Down처럼 **점수 순 상위 N개**로 꾸린다.
평지는 **누적**한다(2026-10-13 사용자 확정): 오늘 기준으로 거꾸로 세서 가격이 좁은 밴드 안에 머문
가장 긴 구간(최소 3일)을 평지로 잡는다. 버티는 날이 늘수록 평지 일수가 1씩 쌓인다.

점수 = 하락 깊이 × 평지 평탄도 × 거래량 배수(꾸준함 가중)
  - 하락 깊이: 평지 직전 구간 고점 대비 평지 평균가 하락률. 25%에서 1.0, 35%에서 상한.
  - 평탄도: 평지 밴드 폭(고−저)/평균과 평지 안의 추세(기울기 × 길이)가 작을수록 1에 가깝다.
  - 거래량 배수: 평지 거래량 중간값 ÷ "하락 시작 전" 거래량 중간값. 하락 중 투매 거래량이 기준을
    부풀리지 않게 기준은 고점 이전 구간(최대 20거래일)으로 잡는다. 꾸준함 = 평지 날들 중 기준의 2배 이상인 날 비율.

누가 받는지(외인·기관)는 점수에 넣지 않고 표시만 한다(사용자 확정: 가중치 대신 다른 색). "받는손" =
평지 구간 외인 순매수 합 > 0 이고 외인 보유율도 올랐으면 "외인", 아니면 기관 순매수 합 > 0 이면 "기관".
아침 리포트 ①(액션)은 리포트에 외인 분석을 쓰지 않는 원칙이라(역할 분리 — ③ CFG가 ①·② 겹침을 본다, 대표 2026-10-21) 거기선 쓰지 않고, 앱 패널과 Basecamp 리포트에서만 색으로 쓴다.
오늘 거래량은 장중이라 미완성이므로 평지 계산에서 빼고, 오늘 실시간 가격은 상태(돌파/다지는 중/이탈)에만 쓴다.
"""
import math

import numpy as np
import pandas as pd

BASE_MIN, BASE_MAX = 3, 30      # 평지 길이 범위(거래일) — 이 안에서 밴드를 지킨 가장 긴 구간
BAND_MAX = 0.07                 # 평지 = 종가 (고−저)/평균이 이 안
SLOPE_MAX = 0.05                # 평지 = 구간 추세(기울기 × 길이 / 평균)가 이 안
PRE_MIN = 8                     # 평지 앞에 최소 이만큼의 구간이 있어야 "빠지다"를 판단
BASELINE_MAX = 20               # 하락 전 거래량 기준 구간 최대 길이
MIN_DROP = 0.05                 # "빠졌다"고 부를 최소 하락(이보다 덜 빠지면 그냥 횡보)
HEAVY_MULT = 2.0                # "대량" = 기준 거래량의 2배 이상
SHAPE_MIN = 5                   # "모양이 잘 보이는 다지기" 줄에 올릴 최소 평지 일수
SPIKE_JUMP = 0.15               # 고점이 직전 대비 이만큼 1~3일 만에 튀었으면 뉴스 급등 고점으로 보고 제외


def _merge(price_hist: pd.DataFrame, flow_hist: pd.DataFrame) -> pd.DataFrame:
    p = price_hist[["종목코드", "종목명", "날짜", "종가"]].copy()
    p["종목코드"] = p["종목코드"].astype(str)
    if flow_hist is not None and not flow_hist.empty:
        v = flow_hist[["종목코드", "날짜", "거래량"]].copy()
        v["종목코드"] = v["종목코드"].astype(str)
        p = p.merge(v, on=["종목코드", "날짜"], how="left")
    else:
        p["거래량"] = np.nan
    p["종가"] = pd.to_numeric(p["종가"], errors="coerce")
    p["거래량"] = pd.to_numeric(p["거래량"], errors="coerce")
    return p.dropna(subset=["종가"]).sort_values(["종목코드", "날짜"])


def _flat(base: np.ndarray) -> tuple[float, float]:
    m = float(base.mean())
    band = float(base.max() - base.min()) / m
    slope = float(np.polyfit(np.arange(len(base)), base, 1)[0]) * (len(base) - 1) / m
    return band, slope


def _base_length(closes: np.ndarray) -> int:
    """오늘 기준 거꾸로 하루씩 붙여가며 세는 평지 길이(누적). 붙이려는 날이 지금까지 평지 중간값에서
    BAND_MAX/2 넘게 벗어나거나, 붙인 뒤 밴드·기울기가 깨지면 거기서 멈춘다(하락 끝자락이 평지에 안 섞이게).
    최소 BASE_MIN일을 못 채우면 0."""
    L = 0
    for n in range(BASE_MIN, min(BASE_MAX, len(closes) - PRE_MIN) + 1):
        if n > BASE_MIN:
            med = float(np.median(closes[-(n - 1):]))
            if abs(closes[-n] / med - 1) > BAND_MAX / 2:
                break
        band, slope = _flat(closes[-n:])
        if band > BAND_MAX or abs(slope) > SLOPE_MAX:
            break
        L = n
    return L


def _score(closes: np.ndarray, vols: np.ndarray, L: int) -> dict | None:
    base, pre = closes[-L:], closes[:-L]
    peak_i = int(np.argmax(pre))
    peak = float(pre[peak_i])
    if len(pre) - peak_i < 3:            # 고점에서 평지까지 최소 3거래일은 빠져야 "꾸준히 내려온" 것
        return None
    # 고점이 뉴스 한 방으로 1~3일 만에 튀어 오른 꼭대기면, 그 뒤 하락은 "빠지다"가 아니라 김 빠지는 것
    # (실측: 샘표·샘표식품 9/10~11 거래량 수백 배 급등 → 이후 되돌림이 1·2위로 잡혔음).
    before = pre[max(0, peak_i - 10):peak_i]
    if len(before) >= 3:
        ref = float(np.median(before))
        if peak > ref * (1 + SPIKE_JUMP) and peak > float(pre[max(0, peak_i - 3)]) * (1 + SPIKE_JUMP):
            return None
    bmean = float(base.mean())
    drop = 1 - bmean / peak
    if drop < MIN_DROP:
        return None
    band, slope = _flat(base)
    bv = vols[-L:]
    bv = bv[~np.isnan(bv)]
    pre_v = vols[:-L][max(0, peak_i - BASELINE_MAX + 1): peak_i + 1]
    pre_v = pre_v[~np.isnan(pre_v)]
    if len(pre_v) < 5:                   # 고점 전 거래량이 부족하면 평지 앞 전체 구간으로 대체
        pre_v = vols[:-L][~np.isnan(vols[:-L])]
    if len(bv) < max(2, L // 2) or len(pre_v) < 5:
        return None
    baseline = float(np.median(pre_v))
    if baseline <= 0:
        return None
    ratio = float(np.median(bv)) / baseline
    # 하락 기간 대비(대표 원-2026-10-28-18 "두 바탕 나란히") — 고점 다음 날 ~ 평지 시작 전날 거래량 중간값.
    # 점수엔 안 넣고 보여만 준다(대표 2026-10-29 A1 「점수는 그대로, 두 비율과 '1배 넘은 수'만 붙여」).
    dv = vols[:-L][peak_i + 1:]
    dv = dv[~np.isnan(dv)]
    drop_base = float(np.median(dv)) if len(dv) >= 2 else None
    drop_ratio = float(np.median(bv)) / drop_base if drop_base else None
    # 모양 점수 — "모양이 잘 보이는 다지기" 줄용(대표 2026-10-29 「두 줄 다 내」). 거래량은 안 본다.
    # 낙폭·평지 폭을 그 종목 하루 변동폭 σ(고점 전 최대 60일 로그수익률)×√일수로 재서 고정 % 문턱을 피한다
    # (2026-10-28 다지기 5종목 방식, 창은 고정하지 않고 Basecamp가 거슬러 찾은 고점·평지를 그대로 씀 — 원-21).
    rets = np.diff(np.log(pre[:peak_i + 1]))[-60:]
    if len(rets) < 10:
        rets = np.diff(np.log(closes[:-L]))[-60:]
    sigma = float(np.std(rets)) if len(rets) >= 5 else 0.0
    drop_days = len(pre) - peak_i
    shape = None
    if sigma > 0:
        clear = drop / (sigma * math.sqrt(drop_days))
        tight = max(band / (sigma * math.sqrt(L)), 0.1)
        shape = clear / tight * math.sqrt(L / 20)
    heavy = int((bv >= HEAVY_MULT * baseline).sum())
    depth_s = min(drop, 0.35) / 0.25
    flat_s = math.exp(-band / 0.08) * math.exp(-abs(slope) / 0.05)
    vol_s = math.log2(1 + ratio) * (0.5 + heavy / len(bv))
    # 평지 길이 가중(대표 2026-10-29 B1 「약하게 — 점수 × √(평지 일수 ÷ 3)」, 원-2026-10-28-16 「평지가 길수록 더 무게를 줘」)
    return {"score": depth_s * flat_s * vol_s * math.sqrt(L / BASE_MIN), "peak_i": peak_i, "peak": peak, "drop": drop,
            "band": band, "slope": slope, "ratio": ratio, "heavy": heavy, "n_vol": len(bv),
            "baseline": baseline, "drop_ratio": drop_ratio, "sigma": sigma, "shape": shape, "base_hi": float(base.max()), "base_lo": float(base.min())}


def _absorber(flow_hist: pd.DataFrame, code: str, start: str, end: str) -> dict:
    """평지 구간에 누가 받았나 — 외인·기관 순매수 합(거래량 대비 %)과 외인 보유율 변화."""
    need = {"외국인보유율", "외국인순매수", "기관순매수", "거래량"}
    if flow_hist is None or flow_hist.empty or not need <= set(flow_hist.columns):
        return {"받는손": "", "외인pp": None, "외인시작": None, "외인끝": None, "외인몫": None, "기관몫": None}
    f = flow_hist[flow_hist["종목코드"].astype(str) == code].sort_values("날짜")
    g = f[(f["날짜"] >= start) & (f["날짜"] <= end)]
    if g.empty:
        return {"받는손": "", "외인pp": None, "외인시작": None, "외인끝": None, "외인몫": None, "기관몫": None}
    prev = f[f["날짜"] < start]
    f0 = float(prev["외국인보유율"].iloc[-1]) if not prev.empty else float(g["외국인보유율"].iloc[0])
    pp = float(g["외국인보유율"].iloc[-1]) - f0
    vol = float(pd.to_numeric(g["거래량"], errors="coerce").sum())
    fn = float(pd.to_numeric(g["외국인순매수"], errors="coerce").sum())
    inn = float(pd.to_numeric(g["기관순매수"], errors="coerce").sum())
    who = "외인" if fn > 0 and pp > 0 else ("기관" if inn > 0 else "")
    # 외인시작·외인끝: 원래 비중을 같이 보여 주려고(대표 2026-10-21 "상대적 가치" — 같은 변화도 원래 비중에 따라 무게가 다르다)
    return {"받는손": who, "외인pp": pp, "외인시작": f0, "외인끝": float(g["외국인보유율"].iloc[-1]), "외인몫": fn / vol * 100 if vol else None,
            "기관몫": inn / vol * 100 if vol else None}


def scan(price_hist: pd.DataFrame, flow_hist: pd.DataFrame, live_quotes: dict | None = None,
         today: str | None = None, top_n: int | None = 15) -> list[dict]:
    """관심종목 전체를 Basecamp 점수 순으로. 오늘(today) 행은 장중일 수 있어 평지 계산에서 뺀다."""
    if price_hist is None or price_hist.empty:
        return []
    df = _merge(price_hist, flow_hist)
    if today:
        df = df[df["날짜"] < today]
    live_quotes = live_quotes or {}
    out = []
    for code, g in df.groupby("종목코드"):
        closes, vols, dates = g["종가"].to_numpy(float), g["거래량"].to_numpy(float), g["날짜"].tolist()
        if len(closes) < BASE_MIN + PRE_MIN:
            continue
        L = _base_length(closes)
        if not L:
            continue
        r = _score(closes, vols, L)
        if not r:
            continue
        now = live_quotes.get(code) or float(closes[-1])
        if now > r["base_hi"]:
            status = "돌파"
        elif now < r["base_lo"] * 0.97:
            status = "이탈"
        else:
            status = "다지는 중"
        out.append({
            "종목코드": code, "종목명": g["종목명"].iloc[-1], "score": r["score"],
            "고점일": dates[r["peak_i"]], "고점": r["peak"], "하락률": -r["drop"] * 100,
            "평지시작": dates[-L], "평지끝": dates[-1], "평지일수": L,
            "밴드": r["band"] * 100, "기울기": r["slope"] * 100,
            "거래량배수": r["ratio"], "하락기간배수": r["drop_ratio"], "모양점수": r["shape"], "대량일수": r["heavy"], "거래량일수": r["n_vol"],
            "기준거래량": r["baseline"], "평지고": r["base_hi"], "평지저": r["base_lo"],
            "현재가": float(now), "고점대비": (float(now) / r["peak"] - 1) * 100, "상태": status,
            **_absorber(flow_hist, code, dates[-L], dates[-1]),
        })
    out.sort(key=lambda x: -x["score"])
    # 점수 순위와 평지 길이 순위를 나란히(대표 2026-10-29 B3). 길이 같으면 점수 순.
    for i, x in enumerate(out, 1):
        x["점수순위"] = i
    for i, x in enumerate(sorted(out, key=lambda x: (-x["평지일수"], -x["score"])), 1):
        x["길이순위"] = i
    return out[:top_n] if top_n else out


def over_one(results: list[dict]) -> dict:
    """평지 잡힌 종목 전체 중 평지 거래량이 두 바탕 각각의 1배를 넘은 수(대표 원-2026-10-28-20 「응 오케이」) —
    정말 받아내는 날인지, 그중 나은 것만 고른 날인지 갈리게 순위 위에 한 줄로 붙인다."""
    return {"전체": len(results),
            "하락전": sum(1 for x in results if x["거래량배수"] > 1),
            "하락기간": sum(1 for x in results if x.get("하락기간배수") is not None and x["하락기간배수"] > 1)}


def floor_lines(results: list[dict], n: int = 5) -> dict:
    """"다지는 거 뽑아줘"의 답 — 두 줄(대표 2026-10-29 「두 줄 다 내. 모양이 잘 보이는 다지기 5 + 다지며 담기는 5.
    각 줄 위에 '1배 넘은 수'」). 모양 = 모양점수 순(거래량 안 봄), 담기 = Basecamp 점수 순. 문턱으로 거르지 않는다 —
    1배 넘는 게 없어도 순위대로 n개(원-19)."""
    # 모양 줄은 평지 SHAPE_MIN일 이상만 — 사흘짜리 평지는 밴드가 작게 나와 "평평함"이 공짜로 커진다
    # (2026-10-28 다지기 5종목도 평지 5~14일에서 골랐다).
    shape = sorted([x for x in results if x.get("모양점수") is not None and x["평지일수"] >= SHAPE_MIN],
                   key=lambda x: -x["모양점수"])[:n]
    absorb = sorted(results, key=lambda x: -x["score"])[:n]
    return {"모양": shape, "담기": absorb, "1배": over_one(results)}


def _fmt_line(title: str, rows: list[dict], cnt: dict) -> str:
    out = [f"{title} — 평지 거래량 1배 넘은 수: 하락 전 대비 {cnt['하락전']}/{cnt['전체']} · 하락 기간 대비 {cnt['하락기간']}/{cnt['전체']}"]
    for i, x in enumerate(rows, 1):
        dr = "-" if x.get("하락기간배수") is None else f"×{x['하락기간배수']:.2f}"
        out.append(f"  {i}. {x['종목명']}  하락 {x['하락률']:+.1f}% · 평지 {x['평지시작'][5:]}부터 {x['평지일수']}일 · 밴드 {x['밴드']:.1f}%"
                   f" · 거래량 하락 전 ×{x['거래량배수']:.2f} / 하락 기간 {dr} · 점수 {x['score']:.2f}({x['점수순위']}위)"
                   f" · 길이 {x['길이순위']}위 · 받는손 {x.get('받는손') or '-'} · {x['상태']}")
    return "\n".join(out)


if __name__ == "__main__":
    # python basecamp.py [N] — "다지는 거 뽑아줘" 두 줄을 DB(읽기만)로 바로 찍는다.
    import sys
    import portfolio_core as core
    try:
        import tomllib
        with open(core.HERE / ".streamlit" / "secrets.toml", "rb") as f:
            sb = tomllib.load(f).get("supabase", {})
    except Exception:
        sb = {}
    url, key = sb.get("url", ""), sb.get("anon_key", "")
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    ph = core.load_watchlist_history_db(url, key)
    fh = core.load_investor_flow_db(url, key)
    res = scan(ph, fh, today=core.today_kst_str(), top_n=None)
    fl = floor_lines(res, n)
    print(_fmt_line(f"모양이 잘 보이는 다지기 {n}", fl["모양"], fl["1배"]))
    print(_fmt_line(f"다지며 담기는 {n}", fl["담기"], fl["1배"]))
