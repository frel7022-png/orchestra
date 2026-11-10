"""리포트 머리 글자 — 전날 대비 기준일 · 마지막 매매(대표 2026-11-10 원-2026-11-10-10)."""
from pathlib import Path

import pandas as pd
import pytest

import live_check

ROOT = Path(__file__).resolve().parent.parent


def _tx(rows):
    return pd.DataFrame(rows, columns=["날짜", "구분"])


def test_base_day_is_previous_trading_day_not_last_saved_row():
    # 앱을 09:30 전에 새로고침해 오늘 계좌 줄이 생겨도 기준일은 앞 거래일
    assert live_check.base_day_note("2026-11-10", "2026-11-11") == ("2026-11-10", "")
    assert live_check.base_day_note("2026-11-10", "2026-11-10") == ("2026-11-10", "")
    # 15:35를 놓쳐 그날 줄이 없으면 어느 줄에서 이었는지 밝힌다
    assert live_check.base_day_note("2026-11-10", "2026-11-09") == ("2026-11-10", "11-10 계좌 줄 없음 · 11-09 줄로 이음")
    assert live_check.base_day_note(None, "2026-11-09") == ("2026-11-09", "")


def test_market_series_draws_price_one_day_past_foreign():
    import pandas as pd
    import foreign_trend as ft
    days = ["2026-11-03", "2026-11-04", "2026-11-05"]
    fh = pd.DataFrame({"날짜": days, "종목코드": ["A"] * 3, "종목명": ["가"] * 3, "외국인보유율": [1.0, 1.1, 1.2]})
    ph = pd.DataFrame({"날짜": days, "종목코드": ["A"] * 3, "종목명": ["가"] * 3, "종가": [100.0, 110.0, 120.0]})
    ps, fs = ft.market_series(fh, ph, {"A": 130.0}, "2026-11-06")
    assert fs["날짜"].iloc[-1] == "2026-11-05" and ps["날짜"].iloc[-1] == "2026-11-06"
    assert ps["종가"].iloc[-1] == pytest.approx(130.0)
    ps0, _ = ft.market_series(fh, ph)
    assert ps0["날짜"].iloc[-1] == "2026-11-05"


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
