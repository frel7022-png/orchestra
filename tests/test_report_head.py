"""리포트 머리 글자 — 전날 대비 기준일 · 마지막 매매(대표 2026-11-10 원-2026-11-10-10)."""
from pathlib import Path

import pandas as pd

import live_check

ROOT = Path(__file__).resolve().parent.parent


def _tx(rows):
    return pd.DataFrame(rows, columns=["날짜", "구분"])


def test_last_trade_is_last_buy_or_sell_on_no_trade_days():
    assert live_check.last_trade_day(_tx([["2026-11-03", "매수"], ["2026-11-05", "매도"]])) == "2026-11-05"


def test_deposit_only_day_is_not_a_trade():
    assert live_check.last_trade_day(_tx([["2026-11-05", "매수"], ["2026-11-06", "입금"]])) == "2026-11-05"


def test_no_trades():
    assert live_check.last_trade_day(_tx([])) == "-"
    assert live_check.last_trade_day(_tx([["2026-11-06", "입금"]])) == "-"


def test_header_text_in_all_three_places():
    for f in ("morning_report.py", "live_check.py", "friend_brief.py"):
        t = (ROOT / f).read_text(encoding="utf-8")
        assert "전날 대비 기준일" in t and "마지막 매매" in t, f
        assert "반영분\")" not in t.replace("매매일지 반영분 기준", ""), f
