"""케이스북(가칭) — new1 사이클 하나 = 사례 하나 (2026-10-02 버전 1, 참고용).

전량매도로 끝난 사이클마다 진입 전 하락·그 구간 외인 변화, 보유 중 흐름, 매도 시점 외인 방향,
매도 후 주가·외인 변화를 요약 숫자로 남긴다. 원본 시계열은 DB/네이버에 있으니 요약만 저장.
전체 사이클을 기록하고 태그(외인 신호·진입 경로·결과 유형·CFG)로 나눠 본다.

산출: casebook/casebook.csv, casebook/cases.json(그래프용 시계열), 리포트는 세션이 별도로 작성.
사용: python casebook.py
"""
import json
import tomllib
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

import portfolio_core as core

HERE = Path(__file__).parent
OUT = HERE / "casebook"
PRE = 10          # 진입 전 비교 구간(거래일)
EXIT_WIN = 5      # 매도 직전 외인 방향 구간(거래일)
POST = (5, 20, 60)
SIG_PP, SIG_REL = 0.3, 5.0   # 외인 신호 판정(CFG와 같은 문턱)


def tag_signal(dpp, rel):
    if dpp is None:
        return "외인 데이터 없음"
    if dpp >= SIG_PP or (rel is not None and rel >= SIG_REL):
        return "모음"
    if dpp <= -SIG_PP or (rel is not None and rel <= -SIG_REL):
        return "빠짐"
    return "중립"


def main():
    tx = core.load_transactions()
    cycles = core._all_cycles(tx)
    closed = [c for c in cycles if c["closed"]]
    tags = core.load_cfg_tags()
    sec = tomllib.load(open(HERE / ".streamlit/secrets.toml", "rb"))["supabase"]
    fh = core.load_investor_flow_db(sec["url"], sec.get("anon_key") or sec.get("key"))
    code_cache = core.load_code_cache()
    today = date.today().isoformat()

    # 종목별 일별 시세(진입 30일 전 ~ 오늘)를 한 번씩만 받는다
    names = sorted({c["종목"] for c in closed})
    first_d = {n: min(c["first_buy_date"] for c in closed if c["종목"] == n) for n in names}
    prices, codes = {}, {}
    for n in names:
        code = core.resolve_code(n, code_cache)
        if not code:
            continue
        codes[n] = code
        start = (date.fromisoformat(first_d[n]) - timedelta(days=45)).isoformat()
        rows = core.fetch_daily_price_history(code, start, today)
        prices[n] = pd.DataFrame(rows)[["날짜", "종가"]] if rows else pd.DataFrame(columns=["날짜", "종가"])

    seen_names = set()
    out, series = [], {}
    order = sorted(closed, key=lambda c: (c["first_buy_date"], c["종목"]))
    t_all = tx[tx["구분"].isin(["매수", "매도"])].copy()
    for c in order:
        n = c["종목"]
        route = "Up/Down 재진입" if n in seen_names else "Fishing 신규"
        seen_names.add(n)
        p = prices.get(n)
        code = codes.get(n)
        if p is None or p.empty:
            continue
        p = p.sort_values("날짜").reset_index(drop=True)
        d0, d1 = c["first_buy_date"], c["close_date"]
        i0 = p.index[p["날짜"] >= d0].min()
        i1 = p.index[p["날짜"] >= d1].min()
        if pd.isna(i0) or pd.isna(i1):
            continue
        px0, px1 = float(p.at[i0, "종가"]), float(p.at[i1, "종가"])
        pre_i = max(0, i0 - PRE)
        pre_ret = (px0 / float(p.at[pre_i, "종가"]) - 1) * 100
        hi20 = float(p.loc[max(0, i0 - 20):i0, "종가"].max())
        dd20 = (px0 / hi20 - 1) * 100
        hold = p.loc[i0:i1, "종가"].astype(float)
        mae = (hold.min() / c["first_buy_px"] - 1) * 100 if c["first_buy_px"] else None
        post = {}
        for k in POST:
            j = i1 + k
            post[k] = (float(p.at[j, "종가"]) / px1 - 1) * 100 if j < len(p) else None

        # 외인
        f = fh[fh["종목코드"] == code].sort_values("날짜") if code else pd.DataFrame()
        def fval(d, after=True):
            if f.empty:
                return None, None
            g = f[f["날짜"] >= d] if after else f[f["날짜"] <= d]
            if g.empty:
                return None, None
            r = g.iloc[0] if after else g.iloc[-1]
            return float(r["외국인보유율"]), r["날짜"]
        d_pre = p.at[pre_i, "날짜"]
        f_pre0, fd_pre0 = fval(d_pre)
        f0, _ = fval(d0, after=False)
        f_close, _ = fval(d1, after=False)
        d_exitw = p.at[max(0, i1 - EXIT_WIN), "날짜"]
        f_exit0, _ = fval(d_exitw, after=False)
        ok_pre = f_pre0 is not None and f0 is not None and fd_pre0 is not None and fd_pre0 <= d0
        f_pre_d = (f0 - f_pre0) if ok_pre else None
        f_pre_rel = (f_pre_d / f_pre0 * 100) if ok_pre and f_pre0 else None
        f_hold = (f_close - f0) if (f0 is not None and f_close is not None) else None
        f_exit = (f_close - f_exit0) if (f_close is not None and f_exit0 is not None) else None
        f_post20 = None
        if f_close is not None and i1 + 20 < len(p):
            fp, _ = fval(p.at[i1 + 20, "날짜"], after=False)
            f_post20 = fp - f_close if fp is not None else None

        cid = f"{n}|{d0}"
        out.append({
            "사례": cid, "종목명": n, "종목코드": code, "진입일": d0, "매도일": d1,
            "보유거래일": int(i1 - i0), "매수횟수": c["n_buy"], "매도횟수": c["n_sell"],
            "결과유형": core._cycle_bucket(c), "진입경로": route,
            "CFG": "Y" if core._cycle_has_cfg_tag(c, tags) else "",
            "수익률": round(c["realized"] / c["buy_amt"] * 100, 2) if c["buy_amt"] else None,
            "실현손익": round(c["realized"]), "매수금액": round(c["buy_amt"]),
            "진입전10일": round(pre_ret, 2), "진입전20일고점대비": round(dd20, 2),
            "보유중최대하락": round(mae, 2) if mae is not None else None,
            "진입전외인pp": round(f_pre_d, 2) if f_pre_d is not None else None,
            "진입전외인상대": round(f_pre_rel, 1) if f_pre_rel is not None else None,
            "외인신호": tag_signal(f_pre_d, f_pre_rel), "진입외인비중": f0,
            "보유중외인pp": round(f_hold, 2) if f_hold is not None else None,
            "매도직전5일외인pp": round(f_exit, 2) if f_exit is not None else None,
            "매도후5일": None if post[5] is None else round(post[5], 2),
            "매도후20일": None if post[20] is None else round(post[20], 2),
            "매도후60일": None if post[60] is None else round(post[60], 2),
            "매도후20일외인pp": None if f_post20 is None else round(f_post20, 2),
        })
        # 그래프용: 진입 10일 전 ~ 매도 20일 후
        lo, hi = pre_i, min(len(p) - 1, i1 + 20)
        ff = f[(f["날짜"] >= p.at[lo, "날짜"]) & (f["날짜"] <= p.at[hi, "날짜"])] if not f.empty else f
        tc = t_all[(t_all["종목명"] == n) & (t_all["날짜"] >= d0) & (t_all["날짜"] <= d1)]
        series[cid] = {
            "price": list(zip(p.loc[lo:hi, "날짜"], p.loc[lo:hi, "종가"].astype(float))),
            "foreign": list(zip(ff["날짜"], ff["외국인보유율"].astype(float))) if not ff.empty else [],
            "trades": list(zip(tc["날짜"], tc["구분"], tc["단가"].astype(float))),
        }

    OUT.mkdir(exist_ok=True)
    df = pd.DataFrame(out)
    df.to_csv(OUT / "casebook.csv", index=False, encoding="utf-8-sig")
    (OUT / "cases.json").write_text(json.dumps(series, ensure_ascii=False), encoding="utf-8")
    print(f"[완료] 사례 {len(df)}개 (전량매도 사이클 {len(closed)}개 중)")
    return df


if __name__ == "__main__":
    main()
