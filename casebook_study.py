"""케이스북 시장 전체 연구 — 관심종목 180개 전체의 "빠질 때 외인이 모으면 그 뒤 주가는" (2026-10-02 0호, 2026-10-06 1호 확장).

내 매매(사이클)와 무관하게, 관심종목 전체의 모든 (종목, 날짜)를 사건으로 본다.
날짜 t마다 지난 LOOK거래일 주가 변화 P, 같은 구간 외인 변화 dF(%p)·상대 증가율 rel,
구간 시작 외인 비중(base), 같은 구간 거래량 배율(vol_x = 구간 평균 / 그 직전 20거래일 평균)을 재고,
그 뒤 H거래일 주가 수익률을 붙인다. LOOK은 5/10/20 세 창을 같이 계산(창 길이에 따라 답이 바뀌는지 비교용).

출력:
- casebook/series.csv       종목별 일별 원본(날짜, 종가, 거래량, 외국인보유율) — 다른 창·기간 분석의 공통 바탕
- casebook/study_events.csv 사건 표(look 컬럼으로 창 구분, path는 사건일 이후 0~30일 주가 경로)
같은 종목의 이웃 날짜 사건은 서로 겹치므로 독립 표본이 아니다(경향 확인용).

기간 끝은 investor_flow 마지막 적재일로 자른다 — 장중에 돌려도 오늘 미확정 봉이 섞이지 않게.

사용: python casebook_study.py
"""
import tomllib
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

import portfolio_core as core

HERE = Path(__file__).parent
OUT = HERE / "casebook"
LOOKS = (5, 10, 20)
HORIZONS = (5, 10, 15, 20, 30)
PATH_LEN = 30
VOL_BASE = 20


def main():
    sec = tomllib.load(open(HERE / ".streamlit/secrets.toml", "rb"))["supabase"]
    url, key = sec["url"], sec.get("anon_key") or sec.get("key")
    fh = core.load_investor_flow_db(url, key)
    wl = fh[["종목코드", "종목명"]].drop_duplicates("종목코드")
    start = (fh["날짜"].min() if not fh.empty else "2026-07-24")
    end = (fh["날짜"].max() if not fh.empty else date.today().isoformat())
    pstart = (date.fromisoformat(start) - timedelta(days=45)).isoformat()
    rows, series = [], []
    for code, name in wl.itertuples(index=False):
        pr = core.fetch_daily_price_history(code, pstart, end)
        if not pr:
            continue
        p = pd.DataFrame(pr)[["날짜", "종가", "거래량"]]
        p = p[(p["종가"] > 0) & (p["날짜"] <= end)].reset_index(drop=True)
        f = fh[fh["종목코드"] == code][["날짜", "외국인보유율"]].dropna()
        m = p.merge(f, on="날짜", how="left")
        m["외국인보유율"] = m["외국인보유율"].ffill()
        s = m.copy()
        s.insert(0, "종목명", name)
        s.insert(0, "종목코드", code)
        series.append(s)
        px = m["종가"].astype(float).tolist()
        vol = m["거래량"].astype(float).tolist()
        fr = m["외국인보유율"].tolist()
        ds = m["날짜"].tolist()
        for look in LOOKS:
            for i in range(look, len(m)):
                f0, f1 = fr[i - look], fr[i]
                if pd.isna(f0) or pd.isna(f1) or ds[i - look] < start:
                    continue
                vb = vol[max(0, i - look - VOL_BASE):i - look]
                vw = vol[i - look + 1:i + 1]
                vb_mean = sum(vb) / len(vb) if vb else 0
                row = {"look": look, "종목코드": code, "종목명": name, "날짜": ds[i],
                       "P": (px[i] / px[i - look] - 1) * 100, "base": f0, "dF": f1 - f0,
                       "rel": (f1 - f0) / f0 * 100 if f0 else None,
                       "vol_x": (sum(vw) / len(vw)) / vb_mean if vb_mean and vw else None}
                for h in HORIZONS:
                    row[f"fwd{h}"] = (px[i + h] / px[i] - 1) * 100 if i + h < len(m) else None
                row["path"] = ";".join(f"{(px[i + k] / px[i] - 1) * 100:.2f}"
                                       for k in range(0, PATH_LEN + 1) if i + k < len(m))
                rows.append(row)
    OUT.mkdir(exist_ok=True)
    pd.concat(series, ignore_index=True).to_csv(OUT / "series.csv", index=False, encoding="utf-8-sig")
    ev = pd.DataFrame(rows)
    ev.to_csv(OUT / "study_events.csv", index=False, encoding="utf-8-sig")
    print(f"[완료] 사건 {len(ev)}개(창 {LOOKS}) · 종목 {ev['종목코드'].nunique()}개 · {ev['날짜'].min()} ~ {ev['날짜'].max()}")


if __name__ == "__main__":
    main()
