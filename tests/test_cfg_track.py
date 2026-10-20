import pandas as pd

import cfg_track


def test_cycle_rows_picks_cycle_containing_tag_day():
    tx = pd.DataFrame([
        ("2026-09-10", "인투셀", "매수", 2, 16490, None),
        ("2026-09-17", "인투셀", "매도", 2, 16870, 692),
        ("2026-10-19", "인투셀", "매수", 1, 15080, None),
        ("2026-10-21", "인투셀", "매수", 1, 14500, None),
    ], columns=["날짜", "종목명", "구분", "수량", "단가", "실현손익"])
    c = cfg_track.cycle_rows(tx, "인투셀", "2026-10-19")
    assert list(c["날짜"]) == ["2026-10-19", "2026-10-21"]  # 9월 사이클은 빠짐


def test_track_one_closed_cycle_result_and_events():
    tx = pd.DataFrame([
        ("2026-10-13", "A", "매수", 10, 1000, None),
        ("2026-10-14", "A", "매수", 10, 900, None),
        ("2026-10-16", "A", "매도", 20, 1000, 1000),
    ], columns=["날짜", "종목명", "구분", "수량", "단가", "실현손익"])
    fh = pd.DataFrame({"종목명": ["A", "A"], "날짜": ["2026-10-13", "2026-10-16"], "외국인보유율": [10.0, 11.5]})
    row, ev = cfg_track.track_one(tx, pd.DataFrame(columns=["종목명"]), fh, "A", "2026-10-13", "")
    assert row["상태"] == "끝" and row["태그뒤_물타기"] == 1
    assert row["결과%"] == round(1000 / 19000 * 100, 2)
    assert row["외인변화pp"] == 1.5
    assert [e["구분"] for e in ev] == ["매수", "매수", "매도"]


def test_sell_lines_reports_full_exit_once(monkeypatch):
    tx = pd.DataFrame([
        ("2026-10-19", "인투셀", "매수", 1, 15080, None),
        ("2026-10-20", "인투셀", "매도", 1, 16780, 1666),
    ], columns=["날짜", "종목명", "구분", "수량", "단가", "실현손익"])
    monkeypatch.setattr(cfg_track.core, "load_transactions", lambda: tx)
    monkeypatch.setattr(cfg_track.core, "load_cfg_tags", lambda: pd.DataFrame({"종목명": ["인투셀"], "태그일": ["2026-10-19"]}))
    df = pd.DataFrame([{"종목명": "인투셀", "상태": "끝", "종료일": "2026-10-20", "결과%": 11.05, "태그가대비%": 11.27}])
    assert cfg_track.sell_lines("2026-10-20", df) == ["외인 보고 산 인투셀 — 사이클 끝(전량), 결과 +11.05% · 태그가 대비 +11.27%"]
    assert cfg_track.sell_lines("2026-10-21", df) == []
