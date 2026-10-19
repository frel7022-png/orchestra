"""새러데이 추천 성적표 (2026-10-16 대표 승인).

세션이 대표에게 종목을 추천할 때마다 picks_log.csv에 한 줄씩 남기고, 나중에 혼합지수(삼성·하이닉스 제외) 대비로 채점한다.
칭찬용이 아니라 "어떤 근거 조합이 실제로 맞았나"를 가리려는 장부 — 틀린 추천도 그대로 남긴다(지우지 않음).

    python picks.py score          # 전체 채점표 출력 + picks_score.csv 저장
    python picks.py score 회차ID    # 그 회차만

기록(세션이 추천 직후 스크립트로 호출):
    import picks
    picks.record("2026-10-16_1328", "신규 7", [("CJ프레시웨이", "신규", "외인꾸준·폭락일받음·Basecamp"), ...])
    → 종목코드·추천가(그 순간 실시간)·벤치 수준(그 순간 실시간)을 같이 찍는다.

채점 정의
- 종목 수익률 = 추천가 → 평가 시점 가격(평가일 확정 종가, '지금'은 실시간).
- 벤치 수익률 = 혼합지수(삼성·하이닉스 제외, 코스피 비중 wk) 추천 시점 → 평가 시점.
  추천 시점 벤치는 기록할 때 찍은 실시간 수준(벤치K·벤치Q). 비어 있으면(소급 기록) 추천일 종가로 근사.
- 초과 = 종목 − 벤치(%p). 평가 시점은 지금 / 추천일 +5 / +20 / +60거래일(고정 체크포인트).
- 두 번째 기준 = 같은 기간 관심종목(watchlist) 단순평균 수익률. 시작은 추천일 확정 종가(장중 추천이어도 — 근사),
  끝은 체크포인트일 확정 종가(지금은 실시간). 컬럼 `…초과관심%p`. 두 기준 중 하나로 결론 내지 않는다.
- wk = 지금 new1 보유종목의 코스피 평가금액 비중(live_check와 같은 정의). 고정이 아니라 채점할 때마다 그 값.
- 2026-10-19 대표 확정(10-16 공동 계획 2-2): 경로 추적(tracking.py)은 그대로 두고 성적은 이 체크포인트에서 매긴다.
  예전 +5/+10 두 칸은 대체됨.
"""
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

import portfolio_core as core

HERE = Path(__file__).parent
LOG = HERE / "picks_log.csv"
SCORE = HERE / "picks_score.csv"
COLS = ["회차", "날짜", "시각", "순위", "종목명", "종목코드", "추천가", "구분", "근거", "벤치K", "벤치Q", "메모"]
HORIZONS = (5, 20, 60)   # 체크포인트(거래일) — 대표 확정 2026-10-19


def load_log() -> pd.DataFrame:
    if not LOG.exists():
        return pd.DataFrame(columns=COLS)
    return pd.read_csv(LOG, dtype={"종목코드": str, "회차": str})


def _name2code() -> dict:
    m = dict(zip(core.load_watchlist()["종목명"], core.load_watchlist()["종목코드"].astype(str)))
    h = core.load_holdings()
    m.update(dict(zip(h["종목명"], h["종목코드"].astype(str))))
    return m


def _wk() -> float:
    h = core.load_holdings()
    v = pd.to_numeric(h["수량"]) * pd.to_numeric(h["현재가"])
    mk = h["종목명"].map(core.load_market_cache())
    ks, kq = float(v[mk == "KOSPI"].sum()), float(v[mk == "KOSDAQ"].sum())
    return ks / (ks + kq) if ks + kq else 1.0


def bench_levels(live: bool = True) -> tuple[dict, tuple | None]:
    """({날짜: (코스피ex 수준, 코스닥 수준)} 확정 종가, 지금 실시간 수준 또는 None).
    코스피ex = synthetic_kospi_ex_bigcap(앱 SamHynix extracted와 같은 계산). 수준은 같은 앵커라 구간 비율만 의미 있다."""
    idx, big = core.load_index_history(), core.load_bigcap_history()
    now = None
    if live:
        iq, bq = core.fetch_index_quotes(), core.fetch_bigcap_quotes()
        today = core.today_kst_str()
        if core.market_traded_on(today) is not False and iq.get("KOSPI") and all(bq.get(n) for n in core.BIGCAP_CODES):
            idx = pd.concat([idx[idx["날짜"] < today],
                             pd.DataFrame([{"날짜": today, "KOSPI": iq["KOSPI"]["price"], "KOSDAQ": iq["KOSDAQ"]["price"]}])])
            big = pd.concat([big[big["날짜"] < today], pd.DataFrame([{"날짜": today, **{n: bq[n] for n in core.BIGCAP_CODES}}])])
            ex = core.synthetic_kospi_ex_bigcap(idx, big)
            now = (float(ex["KOSPI"].iloc[-1]), float(ex["KOSDAQ"].iloc[-1]))
            idx, big = idx[idx["날짜"] < today], big[big["날짜"] < today]
    ex = core.synthetic_kospi_ex_bigcap(idx, big)
    closes = {str(r["날짜"]): (float(r["KOSPI"]), float(r["KOSDAQ"])) for _, r in ex.iterrows()}
    return closes, now


def record(round_id: str, label: str, rows: list[tuple], memo: str = "") -> pd.DataFrame:
    """추천 한 회차를 남긴다. rows = [(종목명, 구분, 근거), ...] 순위 순. 추천가·벤치는 지금 실시간."""
    n2c = _name2code()
    codes = [n2c.get(n, "") for n, _, _ in rows]
    q, _ = core.fetch_quotes([c for c in codes if c])
    _, now = bench_levels(live=True)
    t = datetime.now()
    new = pd.DataFrame([{
        "회차": round_id, "날짜": t.strftime("%Y-%m-%d"), "시각": t.strftime("%H:%M"), "순위": i + 1,
        "종목명": n, "종목코드": c, "추천가": (q.get(c) or {}).get("price"), "구분": kind, "근거": why,
        "벤치K": now[0] if now else None, "벤치Q": now[1] if now else None, "메모": f"{label} {memo}".strip(),
    } for i, ((n, kind, why), c) in enumerate(zip([r for r in rows], codes))])
    log = load_log()
    log = pd.concat([log[log["회차"] != round_id], new], ignore_index=True)
    log.to_csv(LOG, index=False, encoding="utf-8-sig")
    return new


def _universe(data: dict | None) -> tuple[dict, dict]:
    """관심종목 확정 종가 {코드: {날짜: 종가}}와 실시간가 {코드: 가격}. data = daily_link_report.load_data() 결과."""
    if data is None:
        import daily_link_report as link
        data = link.load_data()
    ph = data["ph"]
    closes = {str(c): dict(zip(g["날짜"], pd.to_numeric(g["종가"], errors="coerce")))
              for c, g in ph.groupby(ph["종목코드"].astype(str))}
    return closes, {str(k): float(v) for k, v in (data.get("live") or {}).items()}


def _universe_ret(uc: dict, d0: str, d1: str | None, live: dict) -> float | None:
    """관심종목 단순평균 수익률(%) d0 종가 → d1 종가(d1=None이면 실시간)."""
    rs = []
    for c, m in uc.items():
        a = m.get(d0)
        b = live.get(c) if d1 is None else m.get(d1)
        if a and b and a > 0:
            rs.append(b / a - 1)
    return sum(rs) / len(rs) * 100 if rs else None


def score(round_id: str | None = None, data: dict | None = None) -> pd.DataFrame:
    log = load_log()
    if round_id:
        log = log[log["회차"] == round_id]
    if log.empty:
        return log
    uc, ulive = _universe(data)
    closes, now = bench_levels(live=True)
    wk = _wk()
    days = sorted(closes)
    q, _ = core.fetch_quotes(log["종목코드"].dropna().unique().tolist())
    hist = {}
    for c in log["종목코드"].dropna().unique():
        start = log.loc[log["종목코드"] == c, "날짜"].min()
        hist[c] = {r["날짜"]: float(r["종가"]) for r in core.fetch_daily_price_history(c, start, core.today_kst_str())}

    def bret(b0, b1):
        return (wk * (b1[0] / b0[0] - 1) + (1 - wk) * (b1[1] / b0[1] - 1)) * 100

    out = []
    for _, r in log.iterrows():
        c, p0, d0 = r["종목코드"], float(r["추천가"]), r["날짜"]
        b0 = (float(r["벤치K"]), float(r["벤치Q"])) if pd.notna(r["벤치K"]) else closes.get(d0)
        row = {k: r[k] for k in ("회차", "날짜", "순위", "종목명", "구분", "근거", "추천가")}
        px_now = (q.get(c) or {}).get("price")
        if px_now:
            row["지금%"] = (px_now / p0 - 1) * 100
            # 소급 기록이 추천 당일이면 벤치 시작(추천일 종가)이 아직 없다 — 초과는 장 마감 뒤부터
            if b0 and now:
                row["지금초과%p"] = row["지금%"] - bret(b0, now)
            u = _universe_ret(uc, d0, None, ulive)
            if u is not None:
                row["지금초과관심%p"] = row["지금%"] - u
        after = [d for d in days if d > d0]
        for h in HORIZONS:
            if len(after) >= h and b0:
                d = after[h - 1]
                px = hist.get(c, {}).get(d)
                if px:
                    row[f"+{h}일%"] = (px / p0 - 1) * 100
                    row[f"+{h}일초과%p"] = row[f"+{h}일%"] - bret(b0, closes[d])
                    u = _universe_ret(uc, d0, d, ulive)
                    if u is not None:
                        row[f"+{h}일초과관심%p"] = row[f"+{h}일%"] - u
        out.append(row)
    df = pd.DataFrame(out)
    if not round_id:
        df.to_csv(SCORE, index=False, encoding="utf-8-sig")
    return df


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    args = sys.argv[1:]
    if not args or args[0] != "score":
        print(__doc__)
        return 1
    df = score(args[1] if len(args) > 1 else None)
    if df.empty:
        print("기록 없음")
        return 0
    pd.set_option("display.width", 250)
    pd.set_option("display.max_rows", 500)
    print(df.round(2).to_string(index=False))
    for col in ["지금초과%p", "지금초과관심%p"] + [f"+{h}일초과{k}%p" for h in HORIZONS for k in ("", "관심")]:
        if col in df and df[col].notna().any():
            s = df[col].dropna()
            print(f"{col}: {len(s)}건 평균 {s.mean():+.2f}%p · 이긴 비율 {(s > 0).mean() * 100:.0f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
