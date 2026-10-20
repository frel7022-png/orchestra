import pandas as pd

import cfg_ask


def _tx(rows):
    return pd.DataFrame(rows, columns=["날짜", "종목명", "구분", "수량"])


def test_new_buys_only_first_buy_of_cycle():
    tx = _tx([
        ("2026-10-10", "A", "매수", 1),
        ("2026-10-12", "A", "매수", 1),   # 물타기 — 새 사이클 아님
        ("2026-10-05", "B", "매수", 1),
        ("2026-10-06", "B", "매도", 1),
        ("2026-10-12", "B", "매수", 1),   # 전량매도 뒤 재진입 — 새 사이클
        ("2026-10-12", "C", "매수", 2),   # 처음
    ])
    assert sorted(cfg_ask.new_buys(tx, "2026-10-12")) == ["B", "C"]


def test_report_hits_uses_last_n_reports_up_to_day(tmp_path, monkeypatch):
    d = tmp_path / "action"
    d.mkdir()
    for day, body in [("2026-10-01", "인투셀"), ("2026-10-13", "x"), ("2026-10-14", "인투셀"),
                      ("2026-10-15", "y"), ("2026-10-16", "인투셀")]:
        (d / f"{day}.html").write_text(body, encoding="utf-8")
    monkeypatch.setattr(cfg_ask, "REPORT_DIRS", {"액션": d})
    # 10-15까지 최근 3개 = 10-13·14·15 → 10-14만, 10-01(오래됨)·10-16(이후)은 안 잡힘
    assert cfg_ask.report_hits("인투셀", "2026-10-15") == ["액션 10-14"]
