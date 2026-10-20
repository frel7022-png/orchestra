import pandas as pd
import pytest

import ledger


@pytest.fixture(autouse=True)
def tmp_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "OUT", tmp_path)
    monkeypatch.setattr(ledger, "INDEX", tmp_path / "index.csv")
    monkeypatch.setattr(ledger, "DAYS", tmp_path / "days.csv")


def _r(name, src="Fishing", rank=1):
    return {"출처": src, "코드": "000001", "종목명": name, "순위": rank, "한줄": "x"}


def test_function_entry_freezes_when_out_of_rank_and_resumes():
    ledger.daily("2026-10-20", [_r("A"), _r("B")])
    idx = ledger.daily("2026-10-21", [_r("A", "Basecamp")])
    a = idx.set_index("종목명")
    assert a.loc["B", "상태"] == "active" and a.loc["B", "마지막"] == "2026-10-20" and a.loc["B", "오늘순위"] == ""
    assert a.loc["A", "출처"] == "Fishing·Basecamp" and a.loc["A", "순위일수"] == 2
    ledger.off("B", "이유 사라짐", today="2026-10-23")
    idx = ledger.daily("2026-10-24", [_r("B")]).set_index("종목명")
    assert idx.loc["B", "상태"] == "active"  # 다시 순위에 오면 이어 감


def test_daepyo_entry_needs_daepyo_to_turn_off():
    ledger.add("C", "대표가 보라고 함", today="2026-10-20")
    with pytest.raises(SystemExit):
        ledger.off("C", "세션 판단")
    ledger.off("C", "대표가 빼라 함", by_daepyo=True)
    assert pd.read_csv(ledger.INDEX).set_index("종목명").loc["C", "상태"] == "off"


def test_same_day_rerun_replaces_that_day():
    ledger.daily("2026-10-20", [_r("A")])
    ledger.daily("2026-10-20", [_r("A"), _r("A", "CFG")])
    assert len(pd.read_csv(ledger.DAYS)) == 2
