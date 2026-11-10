"""대형주 전날 줄을 다음 날 09:25에 KRX 전일 종가로(대표 2026-11-10 원-2026-11-10-08 · 14)."""
import pandas as pd
import pytest

import daily_close as dc
import portfolio_core as core

DAY = "2026-11-09"
NAMES = list(core.BIGCAP_CODES)


def _quotes(v=100.0):
    return {c: {"price": v * 1.03, "prev_close": v} for c in core.BIGCAP_CODES.values()}


def _setup(monkeypatch, row=None, bench_last=DAY):
    written, benched = [], []
    big = pd.DataFrame([{"날짜": DAY, **{n: row for n in NAMES}}]) if row is not None else pd.DataFrame(columns=["날짜", *NAMES])
    bh = pd.DataFrame([{"날짜": bench_last, "wk": 0.72}])
    monkeypatch.setattr(core, "load_bigcap_history", lambda: big)
    monkeypatch.setattr(core, "load_bench_history", lambda: bh)
    monkeypatch.setattr(core, "snapshot_bigcap_history", lambda p, on_date=None: written.append((on_date, p)))
    monkeypatch.setattr(core, "snapshot_bench_history", lambda d, w: benched.append((d, w)) or "overwrite")
    return written, benched


def test_different_row_is_fixed_and_last_bench_reshot(monkeypatch):
    written, benched = _setup(monkeypatch, row=98.0)
    out = dc.fix_prev_bigcap(DAY, _quotes(100.0), 0.15)
    assert out["status"] == "fixed"
    assert written == [(DAY, {n: 100.0 for n in NAMES})]
    assert benched == [(DAY, 0.72)]


def test_same_row_writes_nothing(monkeypatch):
    written, benched = _setup(monkeypatch, row=100.2)
    assert dc.fix_prev_bigcap(DAY, _quotes(100.0), 0.15)["status"] == "same"
    assert not written and not benched


def test_big_gap_is_suspect_not_fixed(monkeypatch):
    written, benched = _setup(monkeypatch, row=60.0)
    assert dc.fix_prev_bigcap(DAY, _quotes(100.0), 0.15)["status"] == "suspect"
    assert not written and not benched


def test_missing_row_is_filled_with_krx(monkeypatch):
    written, _ = _setup(monkeypatch, row=None, bench_last="2026-11-06")
    assert dc.fix_prev_bigcap(DAY, _quotes(100.0), 0.15)["status"] == "filled"
    assert written == [(DAY, {n: 100.0 for n in NAMES})]


def test_older_bench_row_stays_locked(monkeypatch):
    _, benched = _setup(monkeypatch, row=98.0, bench_last="2026-11-10")
    dc.fix_prev_bigcap(DAY, _quotes(100.0), 0.15)
    assert not benched


def test_no_quote_writes_nothing(monkeypatch):
    written, _ = _setup(monkeypatch, row=98.0)
    assert dc.fix_prev_bigcap(DAY, {}, 0.15)["status"] == "no_quote"
    assert not written


def test_backfill_does_not_overwrite_existing_krx_row(monkeypatch):
    """15:35가 빠진 날을 메울 때 09:25에 쓴 KRX 줄을 일별시세로 덮지 않는다."""
    written, _ = _setup(monkeypatch, row=100.0)
    monkeypatch.setattr(core, "today_kst_str", lambda: "2026-11-10")
    monkeypatch.setattr(core, "fetch_index_quotes", lambda: {})
    monkeypatch.setattr(core, "fetch_bigcap_quotes", lambda: {n: 111.0 for n in NAMES})
    monkeypatch.setattr(core, "confirmed_close_or_live", lambda c, d, f: 111.0)
    monkeypatch.setattr(core, "snapshot_index_history", lambda *a, **k: None)
    monkeypatch.setattr(core, "wk_from_holdings", lambda df: None)
    dc.snapshot_market_closes(DAY, pd.DataFrame())
    assert not written
