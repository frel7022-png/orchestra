"""사건 종목 표시 — 표에서는 안 빼고 표시만(대표 2026-11-10 원-2026-11-10-11)."""
import copy

import numpy as np
import pandas as pd

import basecamp
import morning_report as mr

DAYS = [f"2026-10-{d:02d}" for d in (19, 20, 21, 22, 23, 26, 27, 28, 29, 30)]


def _ph(code, name, closes):
    return pd.DataFrame({"종목코드": code, "종목명": name, "날짜": DAYS, "종가": closes})


def _fh(code, vols):
    return pd.DataFrame({"종목코드": code, "날짜": DAYS, "거래량": vols})


def _index():
    ph = pd.concat([_ph("A", "엘앤씨", [60000.0] * 5 + [24900.0] * 5),
                    _ph("H", "해태", [5000.0] * 5 + [6000.0, 6100.0, 6200.0, 6300.0, 6400.0]),
                    _ph("N", "보통", [100.0, 101.0, 102.0, 101.0, 100.0, 99.0, 98.0, 99.0, 100.0, 101.0])])
    v = [100.0] * 10
    fh = pd.concat([_fh("A", v), _fh("H", [100.0] + [0.0] * 4 + [100.0] * 5), _fh("N", v)])
    return basecamp.event_index(ph, fh, "2026-10-31")


def test_rights_cut_found_in_window():
    assert "권리락·분할 10-26" in basecamp.events_since(_index(), "A", "2026-10-19")


def test_event_on_start_day_is_outside_window():
    """기준일 당일의 하루 변화는 잰 구간 밖이다."""
    assert basecamp.events_since(_index(), "A", "2026-10-26") == ""
    assert basecamp.events_since(_index(), "A", "2026-10-27") == ""


def test_trading_resume_found():
    assert "거래 재개 10-26" in basecamp.events_since(_index(), "H", "2026-10-19")


def test_clean_stock_has_no_tag():
    assert basecamp.events_since(_index(), "N", "2026-10-19") == ""


def test_tagging_keeps_order_and_rows():
    rows = [{"코드": c, "매도일": "2026-10-19", "pct": p} for c, p in (("A", -58.0), ("N", -3.0), ("H", 1.0))]
    res = {"updown": rows, "watering": [], "fishing": [], "meritz": []}
    before = copy.deepcopy(rows)
    mr.add_event_tags(res, _index())
    assert [{k: v for k, v in x.items() if k != "사건"} for x in res["updown"]] == before
    assert res["updown"][0]["사건"] and not res["updown"][1]["사건"]
    assert "사건: " in mr.bc_tags(res["updown"][0]) and mr.bc_tags(res["updown"][1]) == ""


def test_basecamp_tags_unchanged():
    d = DAYS
    c, v = np.array([60000.0] * 5 + [24900.0] * 5), np.array([100.0] * 10)
    assert basecamp._tags(d, c, v, 7, d)[0] == "권리락·분할 10-26 (-58%)"
