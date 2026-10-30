"""daily_close — 시장 쪽 · 계좌 쪽을 따로 찍는다(2026-10-29 대표 원-2026-10-29-17·18).

① 매매일지 없는 날도 계좌 줄(asset·sector)이 마지막 보유 × 그날 종가로 생긴다.
② 사흘치를 늦게 넣으면 그 날부터 뒤 날들만 다시 계산되고 앞 날은 그대로다.
③ 시장 줄(index_history 등)은 계좌 다시 계산이 건드리지 않는다 — 비어 있을 때만 채운다.
실데이터는 건드리지 않는다: 히스토리 파일 경로를 tmp로 돌리고, 종가 조회는 가짜 표로 바꾼다.
"""
import pandas as pd
import pytest

import daily_close as dc
import portfolio_core as core

PRICES = {  # 종목명 → 날짜 → 종가
    "가": {"2026-10-26": 100.0, "2026-10-27": 110.0, "2026-10-28": 120.0, "2026-10-29": 130.0},
    "나": {"2026-10-26": 50.0, "2026-10-27": 50.0, "2026-10-28": 60.0, "2026-10-29": 70.0},
}
INITIAL = 1000.0


def _tx(rows):
    return pd.DataFrame([{"id": f"t{i}", "날짜": d, "종목명": n, "구분": k, "수량": q, "단가": p,
                          "실현손익": "", "메모": core.DAILY_IMPORT_TAG, "정산반영": True}
                         for i, (d, n, k, q, p) in enumerate(rows)], columns=core.TX_COLUMNS)


@pytest.fixture
def city(tmp_path, monkeypatch):
    for name in ("HISTORY_FILE", "SECTOR_HISTORY_FILE", "INDEX_HISTORY_FILE", "BIGCAP_HISTORY_FILE",
                 "BENCH_HISTORY_FILE", "HOLDINGS_FILE"):
        monkeypatch.setattr(core, name, tmp_path / f"{name}.csv")
    monkeypatch.setattr(core, "load_code_cache", lambda: {})
    monkeypatch.setattr(core, "load_sector_cache", lambda: {})

    def fake_close(df, cash, d):
        snap = df.copy()
        snap["현재가"] = [PRICES[n][d] for n in snap["종목명"]]
        return core.compute_metrics(snap, cash)

    monkeypatch.setattr(core, "compute_metrics_at_close", fake_close)
    monkeypatch.setattr(dc, "krx_closes", lambda d: {})   # Supabase 안 부름
    market_calls = []
    monkeypatch.setattr(dc, "snapshot_market_closes", lambda d, df: market_calls.append(d))
    return market_calls


def _asset(d):
    h = core.load_history()
    return float(h.loc[h["날짜"].astype(str) == d, "총자산"].iloc[0])


def test_no_journal_day_gets_account_row_from_last_holdings(city):
    tx = _tx([("2026-10-26", "가", "매수", 2, 100.0)])          # 10-27 매매일지는 없음
    dc.value_account_days(["2026-10-27"], tx=tx, state={"initial": INITIAL, "fee_rate": 0.0})
    assert _asset("2026-10-27") == pytest.approx(INITIAL - 200 + 2 * 110)
    sec = core.load_sector_history()
    assert set(sec["날짜"].astype(str)) == {"2026-10-27"}


def test_late_journal_recomputes_only_from_its_date(city):
    st = {"initial": INITIAL, "fee_rate": 0.0}
    old = _tx([("2026-10-26", "가", "매수", 2, 100.0)])
    dc.value_account_days(["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29"], tx=old, state=st)
    before_26 = _asset("2026-10-26")
    # 10-27 매매일지가 사흘 늦게 들어옴(나 3주 매수) → 10-27 · 28 · 29만 다시
    new = _tx([("2026-10-26", "가", "매수", 2, 100.0), ("2026-10-27", "나", "매수", 3, 50.0)])
    days = dc.account_days_from("2026-10-27")
    assert days == ["2026-10-27", "2026-10-28", "2026-10-29"]
    dc.value_account_days(days, tx=new, state=st)
    assert _asset("2026-10-26") == before_26
    for d in days:
        cash = INITIAL - 200 - 150
        assert _asset(d) == pytest.approx(cash + 2 * PRICES["가"][d] + 3 * PRICES["나"][d])


def test_account_recompute_leaves_market_rows_alone(city):
    core.snapshot_index_history(2500.0, 700.0, on_date="2026-10-27")
    before = core.load_index_history().copy()
    tx = _tx([("2026-10-26", "가", "매수", 1, 100.0)])
    out = dc.value_account_days(dc.account_days_from("2026-10-26"), tx=tx,
                                state={"initial": INITIAL, "fee_rate": 0.0})
    pd.testing.assert_frame_equal(core.load_index_history(), before)
    assert dc.ensure_market_day("2026-10-27", out["2026-10-27"][0]) is False   # 이미 있으면 안 찍음
    assert dc.ensure_market_day("2026-10-26", out["2026-10-26"][0]) is True    # 빈 지난 날은 채움
    assert city == ["2026-10-26"]


def test_holdings_as_of_matches_full_replay_cut_at_day(city):
    tx = _tx([("2026-10-26", "가", "매수", 2, 100.0), ("2026-10-27", "나", "매수", 3, 50.0),
              ("2026-10-28", "가", "매도", 1, 120.0)])
    for d in ("2026-10-26", "2026-10-27", "2026-10-28"):
        h, cash = core.holdings_as_of(tx, INITIAL, 0.002, d)
        h2, st2, _ = core.rebuild_portfolio_from_transactions(tx[tx["날짜"] <= d], INITIAL, 0.002)
        assert cash == pytest.approx(st2["cash"])
        assert dict(zip(h["종목명"], h["수량"])) == dict(zip(h2["종목명"], h2["수량"]))


def test_krx_close_preferred_over_naver_daily(city, monkeypatch):
    """KRX 종가(price_history)가 있으면 그 값으로 잰다 — 네이버 일별시세는 NXT 끝값이라(원-2026-10-29-17 「15:30 종가」)."""
    monkeypatch.setattr(core, "load_code_cache", lambda: {"가": "000001"})
    monkeypatch.setattr(dc, "krx_closes", lambda d: {"000001": 105.0})
    tx = _tx([("2026-10-26", "가", "매수", 2, 100.0)])
    dc.value_account_days(["2026-10-27"], tx=tx, state={"initial": INITIAL, "fee_rate": 0.0})
    assert _asset("2026-10-27") == pytest.approx(INITIAL - 200 + 2 * 105)


def test_ensure_market_day_waits_for_1535_on_today(city, monkeypatch):
    """오늘 시장 줄은 15:35 전엔 안 찍는다(장중값이 남지 않게) — 15:35를 넘겼는데 비었으면 찍는다."""
    from datetime import datetime
    monkeypatch.setattr(core, "today_kst_str", lambda: "2026-10-29")
    monkeypatch.setattr(core, "now_kst", lambda: datetime(2026, 10, 29, 15, 34))
    assert dc.ensure_market_day("2026-10-29", None) is False
    monkeypatch.setattr(core, "now_kst", lambda: datetime(2026, 10, 29, 15, 36))
    assert dc.ensure_market_day("2026-10-29", None) is True
    assert city == ["2026-10-29"]


def test_account_days_from_keeps_pre_krx_rows_fixed(city):
    """10-22 이전 날짜 매매일지는 그날 하나 + KRX 경계(10-23) 뒤 날들만 — 그 사이 픽스 줄은 안 덮는다."""
    for d in ("2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23"):
        core.snapshot_history(1.0, 1.0, on_date=d)
    assert dc.account_days_from("2026-10-20") == ["2026-10-20", "2026-10-23"]


# ④ 15:35 push 실패는 조용히 묻히지 않는다(2026-10-30 위크 편지 B2) — 실패 로그에 한 줄, 커밋은 히스토리 파일만.
class _R:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def _fake_git(monkeypatch, tmp_path, fail_on=None):
    calls = []
    for f in dc.HISTORY_FILES:
        (tmp_path / f).write_text("날짜\n", encoding="utf-8")
    monkeypatch.setattr(dc, "HERE", tmp_path)
    monkeypatch.setattr(dc, "FAIL_LOG", tmp_path / "logs" / "daily_close_fail.log")

    def run(args, **kw):
        calls.append(args[1:])
        if args[1] == "status":
            return _R(out=" M asset_history.csv\n")
        return _R(rc=1, err="거절") if args[1] == fail_on else _R()
    monkeypatch.setattr(dc.subprocess, "run", run)
    return calls


def test_push_histories_commits_only_history_files(monkeypatch, tmp_path):
    calls = _fake_git(monkeypatch, tmp_path)
    assert dc.push_histories("2026-10-30") is True
    commit = next(c for c in calls if c[0] == "commit")
    assert commit[commit.index("--") + 1:] == list(dc.HISTORY_FILES)
    assert [c[0] for c in calls][-1] == "push" and not any(c[0] == "pull" for c in calls)
    assert not dc.FAIL_LOG.exists()


def test_push_histories_failure_writes_fail_log(monkeypatch, tmp_path):
    _fake_git(monkeypatch, tmp_path, fail_on="push")
    assert dc.push_histories("2026-10-30") is False
    line = dc.FAIL_LOG.read_text(encoding="utf-8").strip()
    assert line.startswith("2026-10-30 ") and "push 실패" in line and "거절" in line
