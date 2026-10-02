"""케이스북 시장 전체 연구 — "빠질 때 외인이 사면 나중에 오르나" (2026-10-02, 참고판).

내 매매(사이클)와 별개로, 관심종목 180개 전체의 모든 (종목, 날짜)를 사건으로 본다.
날짜 t마다 지난 LOOK거래일 주가 변화 P, 같은 구간 외인 변화 dF(%p)·상대 증가율 rel,
구간 시작 외인 비중(base)을 재고, 그 뒤 H거래일 주가 수익률을 붙인다.
→ casebook/study_events.csv (사건 표), 분석은 리포트에서.
같은 종목의 이웃 날짜 사건은 서로 겹치므로 독립 표본이 아니다(경향 확인용).

사용: python casebook_study.py
"""
import tomllib
from datetime import date
from pathlib import Path

import pandas as pd

import portfolio_core as core

HERE = Path(__file__).parent
OUT = HERE / "casebook"
LOOK = 10
HORIZONS = (5, 10, 15)


def main():
    sec = tomllib.load(open(HERE / ".streamlit/secrets.toml", "rb"))["supabase"]
    url, key = sec["url"], sec.get("anon_key") or sec.get("key")
    fh = core.load_investor_flow_db(url, key)
    wl = fh[["종목코드", "종목명"]].drop_duplicates("종목코드")
    start = (fh["날짜"].min() if not fh.empty else "2026-07-24")
    pstart = (date.fromisoformat(start) - pd.Timedelta(days=25)).isoformat()
    today = date.today().isoformat()
    rows, paths = [], []
    for code, name in wl.itertuples(index=False):
        pr = core.fetch_daily_price_history(code, pstart, today)
        if not pr:
            continue
        p = pd.DataFrame(pr)[["날짜", "종가"]]
        p = p[p["종가"] > 0].reset_index(drop=True)
        f = fh[fh["종목코드"] == code][["날짜", "외국인보유율"]].dropna()
        m = p.merge(f, on="날짜", how="left")
        m["외국인보유율"] = m["외국인보유율"].ffill()
        px = m["종가"].astype(float).tolist()
        fr = m["외국인보유율"].tolist()
        ds = m["날짜"].tolist()
        for i in range(LOOK, len(m)):
            f0, f1 = fr[i - LOOK], fr[i]
            if pd.isna(f0) or pd.isna(f1) or ds[i - LOOK] < start:
                continue
            row = {"종목코드": code, "종목명": name, "날짜": ds[i],
                   "P": (px[i] / px[i - LOOK] - 1) * 100, "base": f0, "dF": f1 - f0,
                   "rel": (f1 - f0) / f0 * 100 if f0 else None}
            for h in HORIZONS:
                row[f"fwd{h}"] = (px[i + h] / px[i] - 1) * 100 if i + h < len(m) else None
            # 0~15일 경로(평균 경로 그래프용)
            row["path"] = ";".join(f"{(px[i + k] / px[i] - 1) * 100:.2f}" for k in range(0, 16) if i + k < len(m))
            rows.append(row)
    OUT.mkdir(exist_ok=True)
    ev = pd.DataFrame(rows)
    ev.to_csv(OUT / "study_events.csv", index=False, encoding="utf-8-sig")
    print(f"[완료] 사건 {len(ev)}개 · 종목 {ev['종목코드'].nunique()}개 · {ev['날짜'].min()} ~ {ev['날짜'].max()}")


if __name__ == "__main__":
    main()
