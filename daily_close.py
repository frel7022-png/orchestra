"""
하루 종가로 시장 쪽 · 계좌 쪽 히스토리를 따로 찍는다(2026-10-29 대표 원-2026-10-29-17·18).

    「시장은 시장대로 판단하면 돼. … 내 매매일지는 내 계좌, 자산에 영향을 미치는 거고.
      … 언제 매매일지를 넣든 말이야.」

두 쪽:
    - 시장 쪽 — index_history(코스피·코스닥) · bigcap_history(삼성·삼성우·하이닉스) · bench_history(혼합지수
      하루 등락). 매매일지와 상관없이 그날 확정 종가로 찍는다. 이미 있는 날은 덮지 않는다(그날은 그날로 끝 —
      bench는 원래 지난 날짜를 잠근다, 2026-10-22 대표).
    - 계좌 쪽 — asset_history · sector_history. "그날까지의 보유"(portfolio_core.holdings_as_of) × 그날 종가.
      매매일지가 안 들어온 날은 마지막으로 아는 보유로 찍고, 나중에 그 앞 날짜 매매일지가 들어오면
      ingest_daily가 그날부터 히스토리 마지막 날까지 이 함수로 다시 덮는다(account_days_from).

부르는 곳:
    - db_fetch_daily_prices.py --krx-close(평일 15:35 예약 Orchestra_KRXClose) 끝 → close_day(오늘).
      찍은 다섯 파일만 바로 commit·push한다(push_histories — 2026-10-30 위크 편지 B2, origin 대표 「모두 위크 추천대로」).
      실패하면 logs/daily_close_fail.log에 한 줄 → 다음 날 출근 점검(check_daily_db.py)이 잡는다.
    - ingest_daily.py — 매매일지 반영 뒤 계좌 쪽만 다시(시장 쪽은 빠진 날만 채움).
    - 손으로: python daily_close.py [YYYY-MM-DD]   (기본 = 오늘, 휴장일이면 아무것도 안 함)
"""

import subprocess
import sys
from pathlib import Path

import portfolio_core as core

HERE = Path(__file__).resolve().parent
FAIL_LOG = HERE / "logs" / "daily_close_fail.log"
HISTORY_FILES = ("asset_history.csv", "sector_history.csv", "index_history.csv", "bigcap_history.csv", "bench_history.csv")


def log_failure(day: str, msg: str) -> None:
    """15:35 일이 실패하면 한 줄 남긴다 — 무인이라 아무도 못 보니 다음 출근 점검이 이 파일을 읽는다."""
    FAIL_LOG.parent.mkdir(exist_ok=True)
    with FAIL_LOG.open("a", encoding="utf-8") as f:
        f.write(f"{day} {core.now_kst():%H:%M} {' '.join(msg.split())}\n")


def push_histories(day: str) -> bool:
    """찍은 히스토리 다섯만 commit하고 push(작업 중인 다른 변경은 안 섞음 — `git commit -- 파일`).
    바뀐 게 없으면 True. 실패는 log_failure로 남기고 False."""
    def git(*args):
        return subprocess.run(["git", *args], cwd=HERE, capture_output=True, text=True, encoding="utf-8")
    files = [f for f in HISTORY_FILES if (HERE / f).exists()]
    if not git("status", "--porcelain", "--", *files).stdout.strip():
        print("[push] 히스토리 바뀐 것 없음")
        return True
    c = git("commit", "-m", f"KRXClose {day}: 15:35 히스토리 다섯(시장 쪽 · 계좌 쪽, KRX 종가)\n\n"
            "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>", "--", *files)
    if c.returncode:
        log_failure(day, f"commit 실패: {c.stderr.strip() or c.stdout.strip()}")
        return False
    # pull · rebase는 안 한다 — 15:35에 다른 창이 장을 고치는 중이면 autostash가 그 변경을 건드리고, rebase가
    # 아직 안 올린 다른 커밋 id를 바꾼다(b8 검산 10-30). push만 시도하고 거절되면 다음 아침 출근 점검이 처리.
    p = git("push")
    if p.returncode:
        log_failure(day, f"push 실패(로컬 커밋은 남음 — 출근 점검에서 pull 뒤 push): {p.stderr.strip()}")
        return False
    print("[push] 히스토리 다섯 push 완료")
    return True


def snapshot_market_closes(trade_date: str, df) -> None:
    """그날 확정 종가로 index_history·bigcap_history·bench_history를 찍는다(df = 그날 종가 기준 보유,
    혼합지수 wk용). 거래 없는 날 ingest가 그냥 끝나면 세 파일이 그날에서 멈춰, 배포판 '당일'이 며칠치로
    잡혔다(2026-10-29: 10-27 무거래·10-28 미반영으로 10-26에서 멈춤). 2026-10-29 ingest_daily.py에서 옮김."""
    _close_on = core.confirmed_close_or_live

    try:
        iq = core.fetch_index_quotes() or {}
        kospi = _close_on("KOSPI", trade_date, (iq.get("KOSPI") or {}).get("price"))
        kosdaq = _close_on("KOSDAQ", trade_date, (iq.get("KOSDAQ") or {}).get("price"))
        if kospi and kosdaq:
            core.snapshot_index_history(kospi, kosdaq, on_date=trade_date)
            print(f"[지수] {trade_date} 코스피 {kospi:,.2f} · 코스닥 {kosdaq:,.2f} index_history 반영")
    except Exception as e:
        print(f"[경고] index_history 갱신 실패(무시): {e}")

    # SamHynix extracted(§6-19)도 같은 이유로 lock-step. bigcap_history가 index_history보다
    # 하루라도 비면 synthetic_kospi_ex_bigcap이 '여러 날치 대형주 수익률'을 'KOSPI 하루치'에서
    # 빼서 ex 지수가 폭주한다(2026-09-08 실제로 -10%까지 튐).
    try:
        bq = core.fetch_bigcap_quotes() or {}
        closes = {n: _close_on(core.BIGCAP_CODES[n], trade_date, bq.get(n)) for n in core.BIGCAP_CODES}
        if all(closes.get(n) for n in core.BIGCAP_CODES):
            core.snapshot_bigcap_history(closes, on_date=trade_date)
            print(f"[대형주] {trade_date} " + " · ".join(f"{n} {closes[n]:,.0f}" for n in core.BIGCAP_CODES)
                  + " bigcap_history 반영")
    except Exception as e:
        print(f"[경고] bigcap_history 갱신 실패(무시): {e}")

    # 혼합지수 날짜 고정(2026-10-22 대표 "그날은 그날로 끝난다"): 그날 확정 종가 기준 wk와 그날 계수로
    # 하루 등락을 bench_history에 한 줄. 지난 날짜·seed는 안 덮고, 같은 날 재반영만 덮는다.
    try:
        _wk_close = core.wk_from_holdings(df)
        _st = core.snapshot_bench_history(trade_date, _wk_close)
        print(f"[혼합지수] {trade_date} wk {_wk_close:.3f} bench_history {_st}" if _wk_close is not None
              else f"[혼합지수] {trade_date} wk 못 잼 — bench_history 건너뜀")
    except Exception as e:
        print(f"[경고] bench_history 갱신 실패(무시 — 그날 벤치는 다음 화면에서 지금 wk로 재짐): {e}")


def _index_dates() -> set:
    idx = core.load_index_history()
    return set(idx["날짜"].astype(str)) if not idx.empty else set()


def ensure_market_day(day: str, df) -> bool:
    """시장 쪽을 그날 한 번 찍는다 — 이미 index_history에 그날이 있으면 건드리지 않는다(시장 줄은
    매매일지와 상관없이 따로 간다). 오늘은 안 찍는다 — 오늘 시장 줄은 15:35 close_day가 확정 종가로
    찍는다(장중에 먼저 찍으면 장중값이 남는다) — 15:35를 넘겼는데 아직 없으면(예약을 놓친 날) 찍는다.
    찍었으면 True."""
    today = core.today_kst_str()
    now = core.now_kst()
    if day in _index_dates() or day > today or (day == today and (now.hour, now.minute) < (15, 35)):
        return False
    snapshot_market_closes(day, df)
    return True


# 이 날부터 Supabase price_history 종가 = KRX 15:30 종가(15:35 record_krx_close + 다음 날 fix_prev_close).
# 그 전 줄은 NXT 끝값이라 쓰지 않는다(데이터_지도 "price_history 종가 경계" — 지난 줄은 다시 쓰지 않음).
KRX_CLOSE_FROM = "2026-10-23"


def krx_closes(day: str) -> dict:
    """그날 KRX 종가 {종목코드: 종가} — Supabase price_history에서. 대표 「15:30에 나오는 종가」(원-2026-10-29-17).
    네이버 일별시세(compute_metrics_at_close의 첫 소스)는 지난 날도 NXT 끝값(20:00)이라 KRX와 다르다
    (2026-10-29 실측: 보유 79 중 10-27 75 · 10-28 67 · 10-29 69종목이 다름). 못 받으면 {}."""
    if str(day) < KRX_CLOSE_FROM:
        return {}
    try:
        import db_fetch_daily_prices as db
        rows = db.rest_get(f"price_history?select=stock_code,close_price&trade_date=eq.{day}") or []
        return {str(r["stock_code"]): float(r["close_price"]) for r in rows if r.get("close_price")}
    except Exception as e:
        print(f"[경고] {day} KRX 종가(price_history) 못 받음 — 네이버 일별시세로: {e}")
        return {}


def metrics_at_krx_close(h, cash: float, day: str):
    """compute_metrics_at_close와 같은 반환. 그날 KRX 종가가 있는 종목은 그 값, 없는 종목만 원래 길
    (네이버 일별시세 → 실시간 → 저장가)로."""
    krx = krx_closes(day)
    codes = h["종목코드"].astype(str)
    if krx and len(h) and codes.isin(krx.keys()).all():
        snap = h.copy()
        snap["현재가"] = codes.map(krx).values
        return core.compute_metrics(snap, cash)
    df = core.compute_metrics_at_close(h, cash, day)[0]
    if krx:
        hit = df["종목코드"].astype(str).isin(krx.keys())
        df.loc[hit, "현재가"] = df.loc[hit, "종목코드"].astype(str).map(krx)
    return core.compute_metrics(df[core.HOLD_COLUMNS], cash)


def value_account_days(days, tx=None, state=None) -> dict:
    """날짜마다 그날까지의 보유 × 그날 종가(KRX 우선)로 asset_history · sector_history 그날 줄을 덮어 쓴다.
    반환 {날짜: (df, 보유평가액, 총자산, 미실현손실)} — df는 그날 종가 기준 보유(혼합지수 wk용)."""
    tx = core.load_transactions() if tx is None else tx
    st = core.load_state() if state is None else state
    prior = core.load_holdings()
    out = {}
    for d in sorted(set(str(x) for x in days)):
        h, cash = core.holdings_as_of(tx, st.get("initial", 10_000_000.0), st.get("fee_rate", 0.0), d,
                                      prior_holdings=prior)
        df, stock_val, total, ul = metrics_at_krx_close(h, cash, d)
        core.snapshot_history(total, total + ul, on_date=d)
        core.snapshot_sector_history(core.compute_sector_weights(df), on_date=d)
        out[d] = (df, stock_val, total, ul)
        print(f"[계좌] {d} 총자산 {total:,.0f}원(보유 평가 {stock_val:,.0f} · 예수금 {cash:,.0f}) asset·sector 반영")
    return out


def account_days_from(day: str) -> list:
    """매매일지가 day 날짜로 들어왔을 때 계좌 쪽을 다시 계산할 날들 — day + asset_history·index_history에
    이미 있는 day 뒤 날짜 전부(그 뒤 날들의 보유가 바뀌었으니). 시장 줄은 건드리지 않는다.
    단 KRX_CLOSE_FROM(2026-10-23) 앞 날은 그날 하나만 — 10-22까지 줄은 픽스(대표 원-2026-10-29-20)라
    그 사이 날들을 NXT 값으로 조용히 다시 덮지 않는다(어떻게 읽을지는 대표 몫 — 경고만 찍는다)."""
    hist = core.load_history()
    dates = set(hist["날짜"].astype(str)) if not hist.empty else set()
    dates |= _index_dates()
    day = str(day)
    later = {d for d in dates if d > day}
    if day < KRX_CLOSE_FROM:
        skipped = sorted(d for d in later if d < KRX_CLOSE_FROM)
        if skipped:
            print(f"[경고] {day}는 KRX 경계({KRX_CLOSE_FROM}) 앞 — 그 사이 {', '.join(skipped)} 계좌 줄은 픽스라 안 덮음"
                  f"(보유가 바뀌었으면 대표에게 — 원-2026-10-29-20)")
        later = {d for d in later if d >= KRX_CLOSE_FROM}
    return sorted({day} | later)


def missing_trading_days(upto: str) -> list:
    """asset_history 마지막 날 다음 ~ upto 사이, 코스피가 실제로 열린 날(일별시세에 있는 날) 중
    asset_history에 없는 날. 15:35를 놓친 날(PC 꺼짐)을 다음 실행 때 메우려고."""
    hist = core.load_history()
    if hist.empty:
        return []
    last = str(hist["날짜"].astype(str).max())
    if last >= upto:
        return []
    try:
        rows = core.fetch_daily_price_history("KOSPI", last, upto) or []
    except Exception:
        return []
    return sorted({r["날짜"] for r in rows if last < r.get("날짜", "") <= upto})


def close_day(day: str | None = None) -> dict:
    """그날 장 마감 값 매기기 — 빠진 날부터 그날까지 시장 쪽(없을 때만) · 계좌 쪽(덮어씀)."""
    day = day or core.today_kst_str()
    if core.is_krx_closed(day):
        print(f"{day} 휴장일 — 값 매기기 안 함")
        return {}
    days = sorted(set(missing_trading_days(day)) | {day})
    out = value_account_days(days)
    for d in days:
        if d == day:
            # 그날 마감(15:35)은 그날 시장 줄의 확정 순간이다 — 낮에 무엇이 먼저 찍었어도 덮는다
            # (bench는 마지막 줄과 같은 날이면 덮고, 지난 날짜는 잠긴 채 — snapshot_bench_history).
            snapshot_market_closes(d, out[d][0])
        else:
            ensure_market_day(d, out[d][0])
    al = core.check_history_alignment(day)
    print("[정합성 OK]" if al["aligned"] and al["target_ok"] else f"[⚠️ 정합성] {al['maxes']}")
    return out


if __name__ == "__main__":
    close_day(sys.argv[1] if len(sys.argv) > 1 else None)
