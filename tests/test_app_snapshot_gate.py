"""앱 새로고침의 총자산 · 대형주 스냅샷은 15:30 전에만(대표 2026-11-10 원-2026-11-10-14)."""
from datetime import datetime

import pandas as pd
import pytest

import portfolio_core as core


def _spy(monkeypatch):
    calls = []
    monkeypatch.setattr(core, "snapshot_history", lambda t, a: calls.append(("asset", t)))
    monkeypatch.setattr(core, "snapshot_bigcap_history", lambda p: calls.append(("big", p)))
    monkeypatch.setattr(core, "fetch_bigcap_quotes", lambda: {"삼성전자": 1.0})
    return calls


@pytest.mark.parametrize("h,m", [(9, 5), (15, 29)])
def test_before_close_snapshots(monkeypatch, h, m):
    calls = _spy(monkeypatch)
    assert core.snapshot_live_total_and_bigcap(100.0, 110.0, now=datetime(2026, 11, 10, h, m)) is True
    assert [c[0] for c in calls] == ["asset", "big"]


@pytest.mark.parametrize("h,m", [(15, 30), (15, 40), (20, 0)])
def test_after_close_writes_nothing(monkeypatch, h, m):
    calls = _spy(monkeypatch)
    assert core.snapshot_live_total_and_bigcap(100.0, 110.0, now=datetime(2026, 11, 10, h, m)) is False
    assert calls == []


def test_asset_row_does_not_jump_when_app_opened_after_close(tmp_path, monkeypatch):
    """15:35가 쓴 오늘 총자산 줄이 15:40 뒤 새로고침의 애프터장 값으로 안 바뀐다."""
    f = tmp_path / "asset.csv"
    pd.DataFrame([{"날짜": "2026-11-10", "총자산": 9542197.0, "조정자산": 10637361.0}]).to_csv(f, index=False)
    monkeypatch.setattr(core, "HISTORY_FILE", f)
    monkeypatch.setattr(core, "fetch_bigcap_quotes", lambda: {})
    core.snapshot_live_total_and_bigcap(9679547.0, 10770000.0, now=datetime(2026, 11, 10, 15, 41))
    assert float(pd.read_csv(f).iloc[0]["총자산"]) == 9542197.0
