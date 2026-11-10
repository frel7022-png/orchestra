"""Today's Take 어제 대비(원)의 어제 총자산(대표 2026-11-10 원-2026-11-10-16 · 17)."""
import pandas as pd
import pytest

import portfolio_core as core

TODAY = "2026-11-10"   # 화요일 — 직전 거래일 11-09


def _hist(*rows):
    return pd.DataFrame([{"날짜": d, "총자산": v} for d, v in rows], columns=["날짜", "총자산"])


def _hold(chg=(10.0, -10.0)):
    return pd.DataFrame({"종목명": ["A", "B"], "수량": [10, 5], "현재가": [110.0, 90.0], "등락률": list(chg)})


def test_normal_day_uses_row_to_the_won():
    h = _hist(("2026-11-06", 9000.0), ("2026-11-09", 9416207.836))
    assert core.day_change_base(h, _hold(), 1000.0, 5000.0, TODAY) == (9416207.836, {"missing": [], "row": "2026-11-09"})


def test_normal_day_ignores_failed_quotes():
    """평소 날은 시세가 필요 없어 표시도 안 한다."""
    h = _hist(("2026-11-09", 100.0))
    assert core.day_change_base(h, _hold((None, None)), 1000.0, 5000.0, TODAY, failed=["A"])[1]["missing"] == []


def test_today_row_is_ignored_on_normal_day():
    h = _hist(("2026-11-09", 100.0), (TODAY, 999.0))
    assert core.day_change_base(h, _hold(), 1000.0, 5000.0, TODAY)[0] == 100.0


def test_missing_yesterday_uses_quote_prev_close():
    h = _hist(("2026-11-06", 9000.0))   # 11-09 줄 없음
    v, info = core.day_change_base(h, _hold(), 1000.0, 5000.0, TODAY)
    assert v == pytest.approx(10 * 100 + 5 * 100 + 1000) and info["missing"] == []


def test_nan_quote_falls_back_and_names_the_stock():
    h = _hist(("2026-11-06", 9000.0))
    assert core.day_change_base(h, _hold((10.0, None)), 1000.0, 5000.0, TODAY) == (9000.0, {"missing": ["B"], "row": "2026-11-06"})


def test_refresh_failure_is_reported_even_if_old_pct_remains():
    h = _hist(("2026-11-06", 9000.0))
    assert core.day_change_base(h, _hold(), 1000.0, 5000.0, TODAY, failed=["A"]) == (9000.0, {"missing": ["A"], "row": "2026-11-06"})


def test_no_history_uses_initial():
    assert core.day_change_base(_hist(), _hold(), 1000.0, 5000.0, TODAY)[0] == 5000.0


def test_monday_prev_day_is_friday():
    assert core.prev_krx_day("2026-11-09") == "2026-11-06"
