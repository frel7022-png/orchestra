"""portfolio_core.py의 핵심 계산 로직에 대한 회귀 테스트.

이 파일이 지키려는 건 전부 CLAUDE.md에 "실제로 겪은 버그"로 적혀있는 것들이다 —
사람이 매번 눈으로 확인하던 걸 자동화해서, 나중에 코드를 고치다가 같은 실수를
반복해도(예: transactions.csv 델타로 취급, 평단가 단순재평균, 사이클 안 나누고
전체 이력 반영 등) 여기서 바로 잡히게 하는 게 목적.
"""

import pandas as pd
import pytest

import portfolio_core as core


def _tx_row(id_, 날짜, 종목명, 구분, 수량, 단가, 실현손익="", 메모="", 정산반영=True):
    return {
        "id": id_, "날짜": 날짜, "종목명": 종목명, "구분": 구분,
        "수량": 수량, "단가": 단가, "실현손익": 실현손익,
        "메모": 메모, "정산반영": 정산반영,
    }


# ------------------------------------------------------------------ #
# apply_transaction — 평단가 계산 (CLAUDE.md §6-10: "2주@1000원 매수 후 1주 매도,
# 다시 1주@900원 매수하면 평단가는 950원이지 966원이 아니다")
# ------------------------------------------------------------------ #
def test_apply_transaction_avg_price_after_partial_sell_and_rebuy():
    holdings = pd.DataFrame(columns=core.HOLD_COLUMNS)
    state = {"cash": 1_000_000, "initial": 1_000_000, "fee_rate": 0.0}

    holdings, state, _ = core.apply_transaction(holdings, state, "테스트종목", "매수", 2, 1000)
    holdings, state, realized = core.apply_transaction(holdings, state, "테스트종목", "매도", 1, 1100)
    holdings, state, _ = core.apply_transaction(holdings, state, "테스트종목", "매수", 1, 900)

    row = holdings[holdings["종목명"] == "테스트종목"].iloc[0]
    assert row["수량"] == 2
    assert row["평단가"] == pytest.approx(950)
    assert realized == pytest.approx(100)  # (1100-1000)*1


def test_apply_transaction_full_sell_removes_holding():
    holdings = pd.DataFrame(columns=core.HOLD_COLUMNS)
    state = {"cash": 1_000_000, "initial": 1_000_000, "fee_rate": 0.0}

    holdings, state, _ = core.apply_transaction(holdings, state, "A", "매수", 5, 1000)
    holdings, state, realized = core.apply_transaction(holdings, state, "A", "매도", 5, 1200)

    assert holdings.empty
    assert realized == pytest.approx(1000)  # (1200-1000)*5


# ------------------------------------------------------------------ #
# rebuild_portfolio_from_transactions — 거래 재생(replay)
# ------------------------------------------------------------------ #
def test_rebuild_portfolio_basic_cash_and_holdings():
    tx = pd.DataFrame([
        _tx_row("1", "2026-01-02", "A", "매수", 10, 1000),
        _tx_row("2", "2026-01-03", "A", "매도", 4, 1200),
    ])
    holdings, state, _ = core.rebuild_portfolio_from_transactions(tx, initial_capital=1_000_000)

    row = holdings[holdings["종목명"] == "A"].iloc[0]
    assert row["수량"] == 6
    assert row["평단가"] == pytest.approx(1000)
    assert state["cash"] == pytest.approx(1_000_000 - 10 * 1000 + 4 * 1200)


def test_rebuild_portfolio_same_date_replays_in_original_row_order():
    """같은 날짜 안에서는 tx 안의 원래 행 순서대로(입력순) 재생돼야 한다(§1-1) —
    매수/매도가 같은 날짜에 섞여 있으면 순서에 따라 평단가/실현손익이 달라지므로,
    "같은 날짜는 그냥 다시 정렬해버려도 된다"는 식으로 실수하면 안 된다.
    시나리오: 매수1000 → 매도1200 → 매수900 이면 평단가 900, 실현손익 200.
    순서를 매수1000 → 매수900 → 매도1200 으로 바꾸면 평단가 950, 실현손익 250 —
    같은 세 거래라도 입력 순서가 결과를 바꾼다는 걸 보장한다."""
    tx_a = pd.DataFrame([
        _tx_row("1", "2026-01-05", "A", "매수", 1, 1000),
        _tx_row("2", "2026-01-05", "A", "매도", 1, 1200),
        _tx_row("3", "2026-01-05", "A", "매수", 1, 900),
    ])
    holdings_a, _, _ = core.rebuild_portfolio_from_transactions(tx_a, initial_capital=1_000_000)
    row_a = holdings_a[holdings_a["종목명"] == "A"].iloc[0]
    assert row_a["평단가"] == pytest.approx(900)

    tx_b = pd.DataFrame([
        _tx_row("1", "2026-01-05", "A", "매수", 1, 1000),
        _tx_row("2", "2026-01-05", "A", "매수", 1, 900),
        _tx_row("3", "2026-01-05", "A", "매도", 1, 1200),
    ])
    holdings_b, _, _ = core.rebuild_portfolio_from_transactions(tx_b, initial_capital=1_000_000)
    row_b = holdings_b[holdings_b["종목명"] == "A"].iloc[0]
    assert row_b["평단가"] == pytest.approx(950)


# ------------------------------------------------------------------ #
# "현재 보유 사이클"만 반영 (2026-08-24 도입) — 전량매도로 끝난 이전 사이클이
# 최초진입가/평단가/그래프에 섞여 들어가던 버그의 재발 방지.
# ------------------------------------------------------------------ #
def _two_cycle_tx():
    return pd.DataFrame([
        _tx_row("1", "2026-01-01", "A", "매수", 1, 10000),
        _tx_row("2", "2026-01-02", "A", "매수", 1, 8500),
        _tx_row("3", "2026-01-03", "A", "매도", 2, 9500, 실현손익=1000),  # 1차 사이클 종료(보유 0)
        _tx_row("4", "2026-02-01", "A", "매수", 1, 5000),               # 2차 사이클 시작
    ])


def test_current_cycle_transactions_excludes_closed_cycle():
    cyc = core._current_cycle_transactions(_two_cycle_tx(), "A")
    assert len(cyc) == 1
    assert cyc.iloc[0]["id"] == "4"
    assert cyc.iloc[0]["단가"] == 5000


def test_current_cycle_transactions_no_prior_cycle_returns_everything():
    tx = pd.DataFrame([
        _tx_row("1", "2026-01-01", "A", "매수", 1, 10000),
        _tx_row("2", "2026-01-02", "A", "매수", 1, 8500),
    ])
    cyc = core._current_cycle_transactions(tx, "A")
    assert len(cyc) == 2


def test_current_cycle_transactions_fully_exited_returns_empty():
    """마지막 거래가 전량매도라 지금 보유수량이 0이면(=완전히 손 뗀 종목),
    현재 사이클이라 부를 게 없으므로 빈 결과."""
    tx = pd.DataFrame([
        _tx_row("1", "2026-01-01", "A", "매수", 1, 10000),
        _tx_row("2", "2026-01-02", "A", "매도", 1, 11000, 실현손익=1000),
    ])
    cyc = core._current_cycle_transactions(tx, "A")
    assert cyc.empty


def test_get_holding_trade_summary_current_cycle_only():
    summary = core.get_holding_trade_summary(_two_cycle_tx(), "A")
    assert summary["buy_count"] == 1
    assert summary["sell_count"] == 0
    assert summary["buy_amount"] == pytest.approx(5000)
    assert summary["realized_pnl"] == pytest.approx(0)  # 1차 사이클 실현손익은 제외돼야 함


def test_get_holding_trade_points_current_cycle_only():
    points = core.get_holding_trade_points(_two_cycle_tx(), "A")
    assert len(points) == 1
    assert points.iloc[0]["구분"] == "매수"
    assert points.iloc[0]["단가"] == 5000


# ------------------------------------------------------------------ #
# get_holding_avg_price_path — 계단식 평단가 (2026-08-24 도입)
# ------------------------------------------------------------------ #
def test_avg_price_path_steps_only_on_buys():
    tx = pd.DataFrame([
        _tx_row("1", "2026-03-01", "B", "매수", 2, 1000),
        _tx_row("2", "2026-03-05", "B", "매도", 1, 1100, 실현손익=100),  # 평단가에 영향 없음
        _tx_row("3", "2026-03-10", "B", "매수", 1, 900),
    ])
    path = core.get_holding_avg_price_path(tx, "B")

    assert list(path["날짜"]) == ["2026-03-01", "2026-03-10"]
    assert path.iloc[0]["평단가"] == pytest.approx(1000)
    assert path.iloc[1]["평단가"] == pytest.approx(950)  # (1*1000 + 1*900) / 2


def test_avg_price_path_matches_holdings_avg_after_rebuild():
    """get_holding_avg_price_path의 마지막 값은 rebuild_portfolio_from_transactions가
    계산한 실제 평단가와 항상 일치해야 한다(2026-08-24, 실제 보유종목 15개로 수치
    검증했던 걸 회귀 테스트로 고정)."""
    tx = _two_cycle_tx()
    holdings, _, _ = core.rebuild_portfolio_from_transactions(tx, initial_capital=1_000_000)
    holding_avg = float(holdings.loc[holdings["종목명"] == "A", "평단가"].iloc[0])

    path = core.get_holding_avg_price_path(tx, "A")
    assert path.iloc[-1]["평단가"] == pytest.approx(holding_avg)


# ------------------------------------------------------------------ #
# import_daily_trades — "같은 날짜는 델타가 아니라 그날 전체 누적" (§1-2).
# 같은 날짜를 다시 반영해도 누적되면 안 된다 — 실제로 겪은 버그(네이버 1주가
# 3주로 뻥튀기됨)의 재발 방지.
# ------------------------------------------------------------------ #
def test_import_daily_trades_same_date_replaces_not_appends():
    parsed = pd.DataFrame([
        {"종목명": "NAVER", "매수평균가": 200000, "매수수량": 1,
         "매도평균가": 0, "매도수량": 0, "실현손익_증권사": 0},
    ])
    tx = pd.DataFrame(columns=core.TX_COLUMNS)

    tx, added1, replaced1 = core.import_daily_trades(parsed, tx, "2026-01-05")
    assert added1 == 1 and replaced1 == 0

    # 같은 날짜를 다시 반영(재다운로드해서 다시 올린 상황을 흉내) — 누적되면 안 됨
    tx, added2, replaced2 = core.import_daily_trades(parsed, tx, "2026-01-05")
    assert added2 == 1 and replaced2 == 1

    naver_rows = tx[tx["종목명"] == "NAVER"]
    assert len(naver_rows) == 1
    assert naver_rows.iloc[0]["수량"] == 1


def test_import_daily_trades_other_dates_untouched():
    parsed = pd.DataFrame([
        {"종목명": "A", "매수평균가": 1000, "매수수량": 1,
         "매도평균가": 0, "매도수량": 0, "실현손익_증권사": 0},
    ])
    tx = pd.DataFrame([_tx_row("existing", "2026-01-01", "B", "매수", 1, 500,
                                메모=core.DAILY_IMPORT_TAG)])
    tx, added, replaced = core.import_daily_trades(parsed, tx, "2026-01-05")
    assert replaced == 0
    assert len(tx[tx["종목명"] == "B"]) == 1  # 다른 날짜 거래는 그대로


# ------------------------------------------------------------------ #
# parse_daily_trade_csv — 컬럼명이 아니라 열 위치로 파싱 (§1-6)
# ------------------------------------------------------------------ #
def test_parse_daily_trade_csv_parses_by_column_position_not_header_name():
    csv_text = (
        "c0,c1,c2,c3,c4,c5,c6,c7,c8,c9,c10,c11,c12\n"
        "meta,평균가,,,,수량,,,평균가,수량,,,실현손익\n"   # 서브헤더 행 — 건너뛰어야 함
        "1,CJ제일제당,097950,,,195700,2,,0,0,,,0\n"
    )
    raw = csv_text.encode("cp949")
    parsed = core.parse_daily_trade_csv(raw)

    assert len(parsed) == 1
    row = parsed.iloc[0]
    assert row["종목명"] == "CJ제일제당"
    assert row["매수평균가"] == 195700
    assert row["매수수량"] == 2
    assert row["매도수량"] == 0


def test_parse_daily_trade_csv_rejects_truncated_format():
    """열 개수가 예상보다 적으면(내보내기가 잘린 경우 등) 조용히 잘못 파싱하지 말고
    명시적으로 에러를 내야 한다(§1-6: "파싱 실패가 아니라 사용자가 받은 파일 자체가
    불완전했던" 사례가 실제로 있었음)."""
    raw = "c0,c1,c2\nx,y,z\n1,2,3\n".encode("cp949")
    with pytest.raises(ValueError):
        core.parse_daily_trade_csv(raw)
