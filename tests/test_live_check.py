import pandas as pd

import live_check


def _h(rows):
    return pd.DataFrame([{"종목명": n, "종목코드": c, "수량": q, "현재가": p} for n, c, q, p in rows])


def test_quote_fail_share_weights_by_holding_value():
    # A 900원어치(받음) · B 100원어치(실패) → 실패 비율 10% = 문턱(대표 확정 10-22) 이상
    h = _h([("A", "000001", 9, 100), ("B", "000002", 1, 100)])
    failed, share = live_check.quote_fail_share(h, {"000001": {"price": 100, "change_pct": 1.0}})
    assert failed == ["B"]
    assert share == 0.1 and share >= live_check.QUOTE_FAIL_STOP


def test_quote_fail_share_below_threshold_keeps_going():
    # 실패 종목이 평가금액의 5%뿐이면 문턱 아래 — 표시만 하고 계속
    h = _h([("A", "000001", 19, 100), ("B", "000002", 1, 100)])
    failed, share = live_check.quote_fail_share(h, {"000001": {"price": 100, "change_pct": 0.0}})
    assert failed == ["B"] and share < live_check.QUOTE_FAIL_STOP
    # 등락 0.0도 "받은 것"이다(보합) — 실패와 구분
    assert live_check.quote_fail_share(h, {"000001": {"change_pct": 0.0}, "000002": {"change_pct": 0.0}})[0] == []
