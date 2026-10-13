"""Basecamp — 죽죽 빠지다 평지에서 짐(물량)을 꾸리는 종목 (2026-10-13 신설, Volume 패널 자리).

사용자 가설: 20~30%씩 꾸준히 빠지다가 바닥을 다지는데, 그 평지 구간에서 거래량이 평소의 2~3배로
꾸준히 터진다면 누군가 던지는 물량(악재·공매도 물량 등)을 가격 안 올리고 받아내는 중 = 매집 신호.

고정 기준(15일 평균, −20% 같은 문턱)으로 거르면 "거의 맞는데 하나 안 걸리는" 종목이 다 빠지므로,
Fishing·Up/Down처럼 **점수를 매겨 비슷한 순으로 상위 N개**를 꾸린다. 평지 길이(5~12거래일)도
종목마다 점수가 가장 잘 나오는 길이를 고른다.

점수 = 하락 깊이 × 평지 평탄도 × 거래량 배수(꾸준함 가중)
  - 하락 깊이: 평지 직전 구간 고점 대비 평지 평균가 하락률. 25%에서 1.0, 35%에서 상한.
  - 평탄도: 평지 구간 종가 밴드 폭(고−저)/평균, 그리고 평지 안의 추세(선형 기울기 × 길이)가 작을수록 1에 가깝다.
  - 거래량 배수: 평지 구간 거래량 중간값 ÷ "하락 시작 전" 거래량 중간값. 하락 중 투매 거래량이
    기준에 섞여 평균을 부풀리지 않게, 기준은 고점 이전 구간(최대 20거래일)으로 잡는다.
    꾸준함 = 평지 날들 중 기준의 2배 이상인 날의 비율.

외인·기관(누가 받는지)은 일부러 점수에 넣지 않는다 — 아침 리포트 ①(액션)은 외인 정보 없이
보고, ③ CFG가 "그 평지 구간 동안 외인이 실제로 늘었나"를 따로 확인한다(morning_report.py).
오늘 거래량은 장중이라 미완성이므로 평지 계산에서 빼고, 오늘 실시간 가격은 상태(돌파/다지는 중/이탈)에만 쓴다.
"""
import math

import numpy as np
import pandas as pd

BASE_MIN, BASE_MAX = 5, 12      # 평지 길이 후보(거래일)
PRE_MIN = 8                     # 평지 앞에 최소 이만큼의 구간이 있어야 "빠지다"를 판단
BASELINE_MAX = 20               # 하락 전 거래량 기준 구간 최대 길이
MIN_DROP = 0.10                 # 이보다 덜 빠진 건 후보에서 제외(점수 0)
HEAVY_MULT = 2.0                # "대량" = 기준 거래량의 2배 이상
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


def _score_window(closes: np.ndarray, vols: np.ndarray, L: int) -> dict | None:
    base, pre = closes[-L:], closes[:-L]
    if len(pre) < PRE_MIN:
        return None
    peak_i = int(np.argmax(pre))
    peak = float(pre[peak_i])
    if len(pre) - peak_i < 3:            # 고점에서 평지까지 최소 3거래일은 빠져야 "죽죽 내려온" 것
        return None
    # 고점이 뉴스 한 방으로 1~3일 만에 튀어 오른 꼭대기면, 그 뒤 하락은 "빠지다"가 아니라 김 빠지는 것
    # (실측: 샘표·샘표식품 9/10~11 거래량 수백 배 급등 → 이후 되돌림이 1·2위로 잡힘). 고점 직전 10일
    # 중간값보다 SPIKE_JUMP 넘게 높고, 3거래일 전 가격보다도 그만큼 높으면 급등 고점으로 보고 버린다.
    before = pre[max(0, peak_i - 10):peak_i]
    if len(before) >= 3:
        ref = float(np.median(before))
        if peak > ref * (1 + SPIKE_JUMP) and peak > float(pre[max(0, peak_i - 3)]) * (1 + SPIKE_JUMP):
            return None
    bmean = float(base.mean())
    drop = 1 - bmean / peak
    if drop < MIN_DROP:
        return None
    band = float(base.max() - base.min()) / bmean
    slope = float(np.polyfit(np.arange(L), base, 1)[0]) * (L - 1) / bmean
    bv = vols[-L:]
    bv = bv[~np.isnan(bv)]
    pre_v = vols[:-L][max(0, peak_i - BASELINE_MAX + 1): peak_i + 1]
    pre_v = pre_v[~np.isnan(pre_v)]
    if len(pre_v) < 5:                   # 고점 전 거래량이 부족하면 평지 앞 전체 구간으로 대체
        pre_v = vols[:-L][~np.isnan(vols[:-L])]
    if len(bv) < max(3, L // 2) or len(pre_v) < 5:
        return None
    baseline = float(np.median(pre_v))
    if baseline <= 0:
        return None
    ratio = float(np.median(bv)) / baseline
    heavy = int((bv >= HEAVY_MULT * baseline).sum())
    depth_s = min(drop, 0.35) / 0.25
    flat_s = math.exp(-band / 0.08) * math.exp(-abs(slope) / 0.05)
    vol_s = math.log2(1 + ratio) * (0.5 + heavy / len(bv))
    return {"score": depth_s * flat_s * vol_s, "L": L, "peak_i": peak_i, "peak": peak, "drop": drop,
            "band": band, "slope": slope, "ratio": ratio, "heavy": heavy, "n_vol": len(bv),
            "baseline": baseline, "base_hi": float(base.max()), "base_lo": float(base.min())}


def scan(price_hist: pd.DataFrame, flow_hist: pd.DataFrame, live_quotes: dict | None = None,
         today: str | None = None, top_n: int = 15) -> list[dict]:
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
        best = None
        for L in range(BASE_MIN, BASE_MAX + 1):
            if len(closes) < L + PRE_MIN:
                break
            r = _score_window(closes, vols, L)
            if r and (best is None or r["score"] > best["score"]):
                best = r
        if not best:
            continue
        L = best["L"]
        now = live_quotes.get(code) or float(closes[-1])
        if now > best["base_hi"]:
            status = "돌파"
        elif now < best["base_lo"] * 0.97:
            status = "이탈"
        else:
            status = "다지는 중"
        out.append({
            "종목코드": code, "종목명": g["종목명"].iloc[-1], "score": best["score"],
            "고점일": dates[best["peak_i"]], "고점": best["peak"], "하락률": -best["drop"] * 100,
            "평지시작": dates[-L], "평지끝": dates[-1], "평지일수": L,
            "밴드": best["band"] * 100, "기울기": best["slope"] * 100,
            "거래량배수": best["ratio"], "대량일수": best["heavy"], "거래량일수": best["n_vol"],
            "기준거래량": best["baseline"], "평지고": best["base_hi"], "평지저": best["base_lo"],
            "현재가": float(now), "고점대비": (float(now) / best["peak"] - 1) * 100, "상태": status,
        })
    out.sort(key=lambda x: -x["score"])
    return out[:top_n] if top_n else out
