"""portfolio_core.py의 핵심 계산 로직에 대한 회귀 테스트.

이 파일이 지키려는 건 전부 CLAUDE.md에 "실제로 겪은 버그"로 적혀있는 것들이다 —
사람이 매번 눈으로 확인하던 걸 자동화해서, 나중에 코드를 고치다가 같은 실수를
반복해도(예: transactions.csv 델타로 취급, 평단가 단순재평균, 사이클 안 나누고
전체 이력 반영 등) 여기서 바로 잡히게 하는 게 목적.
"""

from datetime import datetime

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


def test_sell_tax_deducts_from_both_cash_and_realized():
    """2026-09-08 수수료 모델: 매수 수수료 0, 매도 시 매도금액 × fee_rate 를
    예수금과 그 건 실현손익 양쪽에서 차감 (CLAUDE.md §6-4). 예: 10만원(=100주@1,000)
    매수 후 100주@1,100(=11만원)에 전량매도, fee_rate 0.2% →
    세금 11만원×0.002 = 220원. 실현손익 = (1,100-1,000)×100 - 220 = 9,780원.
    예수금 = 100만 - 10만(매수, 수수료 0) + 11만 - 220 = 100만 9,780원."""
    holdings = pd.DataFrame(columns=core.HOLD_COLUMNS)
    state = {"cash": 1_000_000.0, "initial": 1_000_000.0, "fee_rate": 0.002}

    holdings, state, _ = core.apply_transaction(holdings, state, "A", "매수", 100, 1000, fee_rate=0.002)
    assert state["cash"] == pytest.approx(900_000)  # 매수엔 수수료 안 붙음

    holdings, state, realized = core.apply_transaction(holdings, state, "A", "매도", 100, 1100, fee_rate=0.002)
    assert realized == pytest.approx(100 * 100 - 110_000 * 0.002)   # 10,000 - 220 = 9,780
    assert state["cash"] == pytest.approx(900_000 + 110_000 - 220)  # 1,009,780


def test_new_holding_change_pct_starts_as_float_not_int(recwarn):
    """신규 종목 매수 시 "등락률" 컬럼이 int(0)로 시작하면, 이후 재생 때마다
    _apply_prior_prices가 실시간 시세의 float 등락률(예: -1.34)을 그 컬럼에 대입하면서
    pandas가 "incompatible dtype" FutureWarning을 던진다(2026-08-31 ingest_daily.py 실행 중
    실제로 발견) — 컬럼이 처음부터 float이어야 한다."""
    holdings = pd.DataFrame(columns=core.HOLD_COLUMNS)
    state = {"cash": 1_000_000, "initial": 1_000_000, "fee_rate": 0.0}
    holdings, state, _ = core.apply_transaction(holdings, state, "테스트종목", "매수", 1, 1000)

    assert holdings["등락률"].dtype == float

    prior = holdings.copy()
    prior.loc[0, "등락률"] = -1.34
    result = core._apply_prior_prices(holdings, prior)

    assert float(result.loc[0, "등락률"]) == pytest.approx(-1.34)
    assert not any("incompatible dtype" in str(w.message) for w in recwarn.list)


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


def test_deposit_row_bumps_cash_only():
    """구분="입금" 행(예탁금 이용료 등)은 예수금만 늘리고 보유종목/실현손익/사이클엔
    영향이 없어야 한다. "출금"은 반대."""
    tx = pd.DataFrame([
        _tx_row("1", "2026-01-02", "A", "매수", 10, 1000),
        _tx_row("2", "2026-01-10", "", "입금", 1, 12345, 메모="예탁금이용료"),
        _tx_row("3", "2026-01-20", "", "출금", 1, 2345, 메모="테스트출금"),
    ])
    holdings, state, tx_out = core.rebuild_portfolio_from_transactions(tx, initial_capital=1_000_000)

    assert list(holdings["종목명"]) == ["A"]                       # 입금/출금이 종목을 만들지 않음
    assert holdings[holdings["종목명"] == "A"].iloc[0]["수량"] == 10
    assert state["cash"] == pytest.approx(1_000_000 - 10 * 1000 + 12345 - 2345)
    dep = tx_out[tx_out["구분"].isin(["입금", "출금"])]
    assert (dep["실현손익"].astype(str).isin(["", "nan", "None"])).all()  # 실현손익 안 붙음
    assert core._all_cycles(tx) == core._all_cycles(tx[tx["구분"] == "매수"])  # 사이클 계산 불변


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


def test_get_holding_trade_summary_all_time_includes_closed_cycles():
    """누적 요약(2026-08-24 신설)은 현재 사이클과 달리 과거에 청산된 1차 사이클의
    매수/매도/실현손익까지 전부 포함해야 한다 — "이 종목으로 지금까지 총 얼마
    벌고 잃었나"를 트래킹하려는 목적이므로."""
    summary = core.get_holding_trade_summary_all_time(_two_cycle_tx(), "A")
    assert summary["buy_count"] == 3   # 1차 사이클 매수 2건 + 2차 사이클 매수 1건
    assert summary["sell_count"] == 1  # 1차 사이클 매도 1건
    assert summary["buy_amount"] == pytest.approx(10000 + 8500 + 5000)
    assert summary["sell_amount"] == pytest.approx(2 * 9500)
    assert summary["realized_pnl"] == pytest.approx(1000)  # 1차 사이클 실현손익 포함돼야 함


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


# ------------------------------------------------------------------ #
# fetch_investor_flow / fetch_market_flow — 네이버 HTML 스크레이핑 파서.
# 실시간 시세 JSON API와 달리 화면용 HTML을 그대로 긁는 거라 더 깨지기 쉬움(2026-08-24
# 도입 당시 CLAUDE.md에도 이렇게 적어둠) — 실제 페이지에서 뽑아낸 구조를 그대로 고정
# fixture로 박아두고, 네이버가 나중에 페이지 구조를 바꾸면 이 테스트가 먼저 잡아내게 함.
# requests.get을 monkeypatch해서 네트워크 없이 파싱 로직만 검증한다.
# ------------------------------------------------------------------ #
class _FakeResp:
    def __init__(self, content=None, text=None):
        self.content = content
        self.text = text

    def raise_for_status(self):
        pass


_INVESTOR_FLOW_HTML = """
<table summary="외국인 기관 순매매 거래량에 관한표이며 날짜별로 정보를 제공합니다." width="680">
<caption>외국인 기관 순매매 거래량</caption>
<tr class="title1"><th>날짜</th><th>종가</th><th>전일비</th><th>등락률</th><th>거래량</th>
<th>기관</th><th>외국인</th><th>보유주수</th><th>보유율</th></tr>
<tr><td colspan="9" height="8"></td></tr>
<tr>
<td width="62" class="tc"><span class="tah p10 gray03">2026.08.21</span></td>
<td width="67" class="num"><span class="tah p11">184,000</span></td>
<td width="67" class="num"><em class="bu_p bu_pdn"><span class="blind">하락</span></em>
<span class="tah p11 nv01">800</span></td>
<td width="67" class="num"><span class="tah p11 nv01">-0.43%</span></td>
<td width="67" class="num"><span class="tah p11">55,426</span></td>
<td width="66" class="num"><span class="tah p11 red01">+17,169</span></td>
<td width="80" class="num"><span class="tah p11 nv01">-19,294</span></td>
<td width="76" class="num"><span class="tah p11">1,947,174</span></td>
<td width="60" class="num"><span class="tah p11">12.93%</span></td>
</tr>
</table>
""".encode("euc-kr")


def test_fetch_investor_flow_parses_real_table_structure(monkeypatch):
    monkeypatch.setattr(core.requests, "get",
                         lambda url, headers=None, timeout=None: _FakeResp(_INVESTOR_FLOW_HTML))
    rows = core.fetch_investor_flow("097950")
    assert len(rows) == 1
    r = rows[0]
    assert r["날짜"] == "2026-08-21"
    assert r["거래량"] == 55426
    assert r["기관순매수"] == 17169
    assert r["외국인순매수"] == -19294
    assert r["외국인보유율"] == 12.93


def test_fetch_investor_flow_returns_empty_on_network_failure(monkeypatch):
    def raise_err(*a, **k):
        raise ConnectionError("boom")
    monkeypatch.setattr(core.requests, "get", raise_err)
    assert core.fetch_investor_flow("097950") == []


_MARKET_VOLUME_HTML = """
<table><tr>
<td class="date">2026.08.24</td><td class="number_1">812.23</td><td class="rate_down">10.29</td>
<td class="number_1">+1.28%</td><td class="number_1">478,302</td><td class="number_1">4,263,302</td>
</tr></table>
""".encode("euc-kr")

_MARKET_FLOW_HTML = """
<table><tr>
<td class="date2">26.08.24</td><td class="rate_down3">-2,382</td><td class="rate_up3">2,161</td>
<td class="rate_up3">292</td>
</tr></table>
""".encode("euc-kr")


def test_fetch_market_flow_merges_volume_and_flow_pages(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        return _FakeResp(_MARKET_VOLUME_HTML if "sise_index_day" in url else _MARKET_FLOW_HTML)
    monkeypatch.setattr(core.requests, "get", fake_get)

    rows = core.fetch_market_flow("KOSDAQ")
    assert len(rows) == 1
    r = rows[0]
    assert r["날짜"] == "2026-08-24"
    assert r["거래량"] == 478302
    assert r["개인순매수"] == -2382
    assert r["외국인순매수"] == 2161
    assert r["기관순매수"] == 292


_SISE_JSON_TEXT = """ [['날짜', '시가', '고가', '저가', '종가', '거래량', '외국인소진율'],

["20260828", 262500, 266000, 256000, 257000, 15106746, 46.72],
["20260831", 249000, 260000, 246000, 260000, 18270969, 46.72],
["20260901", 256500, 262500, 254000, 260500, 11036511, 46.72]

]
"""


def test_fetch_daily_price_history_parses_sise_json(monkeypatch):
    monkeypatch.setattr(core.requests, "get",
                         lambda url, headers=None, timeout=None: _FakeResp(text=_SISE_JSON_TEXT))
    rows = core.fetch_daily_price_history("005930", "2026-08-28", "2026-09-01")
    assert rows == [
        {"날짜": "2026-08-28", "종가": 257000.0, "거래량": 15106746},
        {"날짜": "2026-08-31", "종가": 260000.0, "거래량": 18270969},
        {"날짜": "2026-09-01", "종가": 260500.0, "거래량": 11036511},
    ]


def test_fetch_daily_price_history_returns_empty_on_network_failure(monkeypatch):
    def raise_err(*a, **k):
        raise ConnectionError("boom")
    monkeypatch.setattr(core.requests, "get", raise_err)
    assert core.fetch_daily_price_history("005930", "2026-08-28", "2026-09-01") == []


# ------------------------------------------------------------------ #
# resolve_trading_date — GitHub Actions cron이 자정 넘겨 지연 실행되면 today_kst_str()이
# 실제 거래일보다 하루 늦은 날짜를 반환하던 실제 버그(2026-09-01 발견, §6-16) 재발 방지.
# ------------------------------------------------------------------ #
def test_resolve_trading_date_before_market_open_means_previous_day(monkeypatch):
    """cron이 자정 넘겨 새벽에 실행되면(예: 화요일 00:05) 그 데이터는 실제로 전날(월요일)
    종가이므로 "오늘"이 아니라 "어제" 날짜를 반환해야 한다."""
    monkeypatch.setattr(core, "now_kst", lambda: datetime(2026, 9, 1, 0, 5))  # 화요일 새벽
    assert core.resolve_trading_date() == "2026-08-31"


def test_resolve_trading_date_rolls_back_over_weekend(monkeypatch):
    """자정 넘겨 지연된 실행이 월요일 새벽이면, 그 전날인 일요일이 아니라 가장 최근
    평일(금요일)로 보정해야 한다."""
    monkeypatch.setattr(core, "now_kst", lambda: datetime(2026, 8, 24, 0, 30))  # 월요일 새벽
    assert core.resolve_trading_date() == "2026-08-21"  # 금요일


def test_resolve_trading_date_normal_afternoon_run_is_today(monkeypatch):
    """평소대로 장마감 후(16:13 KST) 정상 실행되면 그날 날짜 그대로."""
    monkeypatch.setattr(core, "now_kst", lambda: datetime(2026, 8, 31, 16, 13))  # 월요일 오후
    assert core.resolve_trading_date() == "2026-08-31"


def test_snapshot_history_uses_resolve_trading_date_not_calendar_today(tmp_path, monkeypatch):
    """snapshot_history/snapshot_sector_history가 today_kst_str()이 아니라
    resolve_trading_date()를 써야 한다(2026-09-05 실제로 겪음). 토요일에 새로고침하면
    asset_history는 today_kst_str()으로 "토요일" 날짜에 새 행이 생기는데
    index_history/bigcap_history는 이미 resolve_trading_date()라 "금요일"에 머물러 있어서,
    compute_index_vs_account의 _bench_on()이 두 스냅샷 날짜(금/토)를 같은 index 값(금요일)에
    매칭시켜 벤치당일이 정확히 0으로 나오는 버그가 있었음(§6-17 "당일 혼합지수 0.00%")."""
    hist_file = tmp_path / "asset_history.csv"
    sector_file = tmp_path / "sector_history.csv"
    monkeypatch.setattr(core, "HISTORY_FILE", hist_file)
    monkeypatch.setattr(core, "SECTOR_HISTORY_FILE", sector_file)
    monkeypatch.setattr(core, "now_kst", lambda: datetime(2026, 9, 5, 11, 0))  # 토요일 오전

    core.snapshot_history(1_000_000.0, 1_000_000.0)
    core.snapshot_sector_history({"식품": 50.0, "화학": 50.0})

    assert core.load_history()["날짜"].iloc[-1] == "2026-09-04"  # 토요일이 아니라 직전 거래일(금)
    assert core.load_sector_history()["날짜"].iloc[-1] == "2026-09-04"


# ------------------------------------------------------------------ #
# fetch_dividend_yield / refresh_dividend_yields — 종목별 배당수익률 (2026-09-01 도입,
# ui_portfolio_tab의 보유종목 카드에 종목명 옆 배지로 표시). 배당 없는 종목은 네이버
# 페이지에 "N/A"로 표시되는데, 이걸 파싱 실패가 아니라 "배당수익률 0%"로 취급해야 한다.
# ------------------------------------------------------------------ #
_DIVIDEND_HTML_WITH_VALUE = """
<table>
<tr><th scope="row">동일업종 PER</th><td><em>17.11</em>배</td></tr>
<tr><th scope="row">배당수익률<span class="bar">l</span><span>2025.12</span></th>
<td><em id="_dvr">1.09</em>%</td></tr>
</table>
""".encode("euc-kr")

_DIVIDEND_HTML_NA = """
<table>
<tr><th scope="row">동일업종 PER</th><td><em>-59.34</em>배</td></tr>
<tr><th scope="row">배당수익률</th><td><em>N/A</em></td></tr>
</table>
""".encode("euc-kr")


def test_fetch_dividend_yield_parses_percent_value_and_period(monkeypatch):
    monkeypatch.setattr(core.requests, "get",
                         lambda url, headers=None, timeout=None: _FakeResp(_DIVIDEND_HTML_WITH_VALUE))
    yield_pct, period = core.fetch_dividend_yield("138040")
    assert yield_pct == pytest.approx(1.09)
    assert period == "2025.12"


def test_fetch_dividend_yield_na_means_zero_not_failure(monkeypatch):
    monkeypatch.setattr(core.requests, "get",
                         lambda url, headers=None, timeout=None: _FakeResp(_DIVIDEND_HTML_NA))
    assert core.fetch_dividend_yield("226400") == (0.0, "")


def test_fetch_dividend_yield_returns_none_on_network_failure(monkeypatch):
    def raise_err(*a, **k):
        raise ConnectionError("boom")
    monkeypatch.setattr(core.requests, "get", raise_err)
    assert core.fetch_dividend_yield("138040") is None


def test_refresh_dividend_yields_never_refetches_cached_codes(monkeypatch, tmp_path):
    """배당수익률은 시시각각 바뀌는 값이 아니므로(사용자 판단, 2026-09-01) 한 번 조회한
    종목은 날짜가 바뀌어도 다시 긁지 않아야 한다 — stock_code_cache.csv/
    stock_sector_cache.csv와 같은 "최초 1회만" 캐시(§1-3)."""
    monkeypatch.setattr(core, "DIVIDEND_CACHE_FILE", tmp_path / "dividend_cache.csv")
    calls = []

    def fake_fetch(code):
        calls.append(code)
        return 1.5, "2025.12"
    monkeypatch.setattr(core, "fetch_dividend_yield", fake_fetch)

    result1 = core.refresh_dividend_yields(["138040", "226400"])
    assert result1 == {"138040": 1.5, "226400": 1.5}
    assert calls == ["138040", "226400"]

    calls.clear()
    monkeypatch.setattr(core, "today_kst_str", lambda: "2099-12-31")  # 다른 날짜여도
    result2 = core.refresh_dividend_yields(["138040"])
    assert result2 == {"138040": 1.5}
    assert calls == []  # 캐시에 있으므로 네트워크 요청 자체가 안 나가야 함


def test_refresh_dividend_yields_only_fetches_new_codes(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "DIVIDEND_CACHE_FILE", tmp_path / "dividend_cache.csv")
    calls = []

    def fake_fetch(code):
        calls.append(code)
        return 1.5, "2025.12"
    monkeypatch.setattr(core, "fetch_dividend_yield", fake_fetch)

    core.refresh_dividend_yields(["138040"])
    calls.clear()
    result = core.refresh_dividend_yields(["138040", "226400"])  # 226400만 새 종목
    assert result == {"138040": 1.5, "226400": 1.5}
    assert calls == ["226400"]


# ------------------------------------------------------------------ #
# rebuild_portfolio_incremental — 체크포인트 재생 (2026-08-25 도입).
# 핵심 불변식: 어떤 시나리오든 rebuild_portfolio_from_transactions(전체 재생)과
# 최종 결과(holdings/현금)가 항상 같아야 한다. 체크포인트 파일은 실제 repo 파일을
# 건드리면 안 되므로 tmp_path로 monkeypatch해서 격리한다.
# ------------------------------------------------------------------ #
def _isolate_checkpoint_files(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "CHECKPOINT_HOLDINGS_FILE", tmp_path / "checkpoint_holdings.csv")
    monkeypatch.setattr(core, "CHECKPOINT_STATE_FILE", tmp_path / "checkpoint_state.csv")


def _mixed_history_tx():
    return pd.DataFrame([
        _tx_row("1", "2026-01-02", "A", "매수", 10, 1000),
        _tx_row("2", "2026-01-05", "A", "매도", 4, 1200),
        _tx_row("3", "2026-01-10", "B", "매수", 3, 500),
        _tx_row("4", "2026-01-20", "A", "매수", 5, 900),
        _tx_row("5", "2026-01-25", "B", "매도", 1, 600),
    ])


def test_incremental_matches_full_replay_when_everything_is_old(monkeypatch, tmp_path):
    """모든 거래가 safety_days보다 훨씬 과거라 전부 체크포인트로 접히는 경우."""
    _isolate_checkpoint_files(monkeypatch, tmp_path)
    tx = _mixed_history_tx()

    exp_holdings, exp_state, _ = core.rebuild_portfolio_from_transactions(tx, initial_capital=1_000_000)
    got_holdings, got_state, _ = core.rebuild_portfolio_incremental(
        tx, initial_capital=1_000_000, safety_days=3, today="2026-06-01")

    assert got_state["cash"] == pytest.approx(exp_state["cash"])
    for name in ["A", "B"]:
        exp_row = exp_holdings[exp_holdings["종목명"] == name]
        got_row = got_holdings[got_holdings["종목명"] == name]
        assert len(got_row) == len(exp_row)
        if len(exp_row):
            assert got_row.iloc[0]["수량"] == pytest.approx(exp_row.iloc[0]["수량"])
            assert got_row.iloc[0]["평단가"] == pytest.approx(exp_row.iloc[0]["평단가"])


def test_incremental_matches_full_replay_with_recent_tail(monkeypatch, tmp_path):
    """일부 거래가 safety_days 이내(=아직 체크포인트로 안 접히는 "꼬리" 구간)인 경우."""
    _isolate_checkpoint_files(monkeypatch, tmp_path)
    tx = _mixed_history_tx()

    exp_holdings, exp_state, _ = core.rebuild_portfolio_from_transactions(tx, initial_capital=1_000_000)
    # today를 마지막 거래(01-25) 기준 safety_days=3 이내로 잡아서, 01-25 거래가 "꼬리"로 남게 함.
    got_holdings, got_state, _ = core.rebuild_portfolio_incremental(
        tx, initial_capital=1_000_000, safety_days=3, today="2026-01-26")

    assert got_state["cash"] == pytest.approx(exp_state["cash"])
    for name in ["A", "B"]:
        exp_row = exp_holdings[exp_holdings["종목명"] == name]
        got_row = got_holdings[got_holdings["종목명"] == name]
        assert len(got_row) == len(exp_row)
        if len(exp_row):
            assert got_row.iloc[0]["수량"] == pytest.approx(exp_row.iloc[0]["수량"])
            assert got_row.iloc[0]["평단가"] == pytest.approx(exp_row.iloc[0]["평단가"])


def test_incremental_advances_and_reuses_checkpoint(monkeypatch, tmp_path):
    """첫 호출이 체크포인트 파일을 만들고, 그 다음 호출(거래 추가)도 전체재생과 계속 일치해야 한다 —
    체크포인트 위에 이어붙여 재생하는 경로가 실제로 타지는지 확인."""
    _isolate_checkpoint_files(monkeypatch, tmp_path)
    tx1 = _mixed_history_tx()

    core.rebuild_portfolio_incremental(tx1, initial_capital=1_000_000, safety_days=3, today="2026-02-01")
    assert core.CHECKPOINT_STATE_FILE.exists()
    _, ckpt_state_1, ckpt_date_1 = core.load_checkpoint()
    assert ckpt_date_1 == "2026-01-29"  # 2026-02-01 - 3일

    tx2 = pd.concat([tx1, pd.DataFrame([
        _tx_row("6", "2026-02-10", "A", "매도", 2, 1300),
        _tx_row("7", "2026-02-15", "B", "매수", 2, 550),
    ])], ignore_index=True)

    exp_holdings, exp_state, _ = core.rebuild_portfolio_from_transactions(tx2, initial_capital=1_000_000)
    got_holdings, got_state, _ = core.rebuild_portfolio_incremental(
        tx2, initial_capital=1_000_000, safety_days=3, today="2026-02-20")

    assert got_state["cash"] == pytest.approx(exp_state["cash"])
    for name in ["A", "B"]:
        exp_row = exp_holdings[exp_holdings["종목명"] == name]
        got_row = got_holdings[got_holdings["종목명"] == name]
        assert len(got_row) == len(exp_row)
        if len(exp_row):
            assert got_row.iloc[0]["수량"] == pytest.approx(exp_row.iloc[0]["수량"])
            assert got_row.iloc[0]["평단가"] == pytest.approx(exp_row.iloc[0]["평단가"])

    # 체크포인트가 앞으로 진행됐는지(과거 그대로 멈춰있지 않은지) 확인.
    _, _, ckpt_date_2 = core.load_checkpoint()
    assert ckpt_date_2 > ckpt_date_1


def test_incremental_realized_pnl_stamped_same_as_full_replay(monkeypatch, tmp_path):
    """체크포인트 경로도 매도 거래의 실현손익을 전체재생과 동일하게 tx에 채워야 한다."""
    _isolate_checkpoint_files(monkeypatch, tmp_path)
    tx = _mixed_history_tx()

    _, _, exp_tx = core.rebuild_portfolio_from_transactions(tx, initial_capital=1_000_000)
    _, _, got_tx = core.rebuild_portfolio_incremental(
        tx, initial_capital=1_000_000, safety_days=3, today="2026-01-26")

    exp_realized = exp_tx.set_index("id")["실현손익"]
    got_realized = got_tx.set_index("id")["실현손익"]
    for tid in ["2", "5"]:  # 매도 거래 id
        assert float(got_realized[tid]) == pytest.approx(float(exp_realized[tid]))


def _watchlist_hist_df():
    """get_watchlist_prev_day_ranks 테스트용 가짜 price_history 조인 결과.
    A/B는 최초일(2026-01-01) 이후 2026-01-02에 각각 -6%/+10% 움직였고, C는 +1.67%로
    임계값(3%) 밖, D는 2026-01-02 자체에 기록이 없음(=최근 편입돼 아직 안 쌓인 종목)."""
    rows = [
        {"종목코드": "001", "종목명": "A", "섹터": "", "날짜": "2026-01-01", "종가": 1000.0, "등락률": 0.0},
        {"종목코드": "002", "종목명": "B", "섹터": "", "날짜": "2026-01-01", "종가": 2000.0, "등락률": 0.0},
        {"종목코드": "003", "종목명": "C", "섹터": "", "날짜": "2026-01-01", "종가": 3000.0, "등락률": 0.0},
        {"종목코드": "004", "종목명": "D", "섹터": "", "날짜": "2026-01-01", "종가": 4000.0, "등락률": 0.0},
        {"종목코드": "001", "종목명": "A", "섹터": "", "날짜": "2026-01-02", "종가": 940.0, "등락률": -6.0},
        {"종목코드": "002", "종목명": "B", "섹터": "", "날짜": "2026-01-02", "종가": 2200.0, "등락률": 10.0},
        {"종목코드": "003", "종목명": "C", "섹터": "", "날짜": "2026-01-02", "종가": 3050.0, "등락률": 1.67},
        # D는 2026-01-02 데이터 없음 — 새로 편입돼 cron이 아직 한 번도 못 돈 상태를 흉내냄.
    ]
    return pd.DataFrame(rows)


def test_watchlist_prev_day_ranks_filters_by_threshold_and_direction():
    hist = _watchlist_hist_df()
    down_ranks = core.get_watchlist_prev_day_ranks(hist, "누적", "DOWN", 3.0, "2026-01-03")
    assert down_ranks == {"A": 1}  # C는 1.67%로 임계값 밖, B는 방향(UP)이 다름

    up_ranks = core.get_watchlist_prev_day_ranks(hist, "누적", "UP", 3.0, "2026-01-03")
    assert up_ranks == {"B": 1}


def test_watchlist_prev_day_ranks_ignores_today_and_missing_history():
    hist = _watchlist_hist_df()
    # "오늘"보다 이전 날짜만 써야 한다 — today를 2026-01-02로 주면 그 전날인 2026-01-01만
    # 후보가 되고, 첫날은 등락률 0%라 아무도 임계값을 못 넘는다.
    ranks = core.get_watchlist_prev_day_ranks(hist, "누적", "DOWN", 3.0, "2026-01-02")
    assert ranks == {}

    # D는 prev_date(2026-01-02) 기록이 아예 없으므로 어떤 기준으로도 dict에 나타나지 않는다
    # (=UI에서 NEW로 처리되는 대상).
    down_ranks = core.get_watchlist_prev_day_ranks(hist, "누적", "DOWN", 3.0, "2026-01-03")
    assert "D" not in down_ranks


def test_watchlist_prev_day_ranks_empty_history_returns_empty_dict():
    assert core.get_watchlist_prev_day_ranks(pd.DataFrame(), "누적", "DOWN", 3.0, "2026-01-03") == {}


# ------------------------------------------------------------------ #
# 지수 대비 계좌 (§6-17) — compute_index_vs_account / 물타기 이벤트
# ------------------------------------------------------------------ #
def _idx_hist(rows):
    return pd.DataFrame(rows, columns=["날짜", "KOSPI", "KOSDAQ"])


def test_compute_index_vs_account_delevers_stock_return_by_exposure():
    """예수금이 40%면(주식 60%), 총자산이 +0.35% 움직였을 때 '내 주식만' 수익 Rs는
    ≈ +0.58%로 환산돼 나와야 한다(0.35 / 0.60). 예수금이 눌러주는 걸 되돌리는 계산."""
    tx = pd.DataFrame([_tx_row("t1", "2026-01-05", "A", "매수", 6, 100_000)])
    asset_hist = pd.DataFrame([
        {"날짜": "2026-01-05", "총자산": 1_000_000.0, "조정자산": 1_000_000.0},
        {"날짜": "2026-01-06", "총자산": 1_003_500.0, "조정자산": 1_003_500.0},
    ])
    idx = _idx_hist([["2026-01-05", 100.0, 100.0], ["2026-01-06", 100.0, 100.0]])

    r = core.compute_index_vs_account(tx, asset_hist, idx, initial_capital=1_000_000.0)
    me = r["me"]
    assert list(me["날짜"]) == ["2026-01-05", "2026-01-06"]
    assert me["계좌수익"].iloc[-1] == pytest.approx(0.0035)
    assert me["주식수익"].iloc[-1] == pytest.approx(3_500 / 600_000)  # ≈ 0.005833
    assert me["주식수익"].iloc[0] == 0.0  # anchor일은 0


def test_compute_index_vs_account_removes_buy_flow_from_stock_return():
    """구간 중 추가매수로 주식평가액이 커진 건 '성과'가 아니라 예수금→주식 이동일 뿐이라
    Rs에 섞이면 안 된다. 가격이 그대로면 100k 더 사도 Rs ≈ 0."""
    tx = pd.DataFrame([
        _tx_row("t1", "2026-01-05", "A", "매수", 6, 100_000),
        _tx_row("t2", "2026-01-06", "A", "매수", 1, 100_000),
    ])
    asset_hist = pd.DataFrame([
        {"날짜": "2026-01-05", "총자산": 1_000_000.0, "조정자산": 1_000_000.0},
        {"날짜": "2026-01-06", "총자산": 1_000_000.0, "조정자산": 1_000_000.0},
    ])
    idx = _idx_hist([["2026-01-05", 100.0, 100.0], ["2026-01-06", 100.0, 100.0]])

    r = core.compute_index_vs_account(tx, asset_hist, idx, initial_capital=1_000_000.0)
    assert r["me"]["주식수익"].iloc[-1] == pytest.approx(0.0, abs=1e-9)


def test_compute_index_vs_account_index_is_cumulative_from_anchor():
    asset_hist = pd.DataFrame([
        {"날짜": "2026-01-05", "총자산": 1_000_000.0, "조정자산": 1_000_000.0},
        {"날짜": "2026-01-07", "총자산": 1_000_000.0, "조정자산": 1_000_000.0},
    ])
    idx = _idx_hist([
        ["2026-01-05", 100.0, 200.0],
        ["2026-01-06", 98.0, 200.0],
        ["2026-01-07", 95.0, 210.0],
    ])
    r = core.compute_index_vs_account(pd.DataFrame(columns=["id", "날짜", "종목명", "구분", "수량", "단가", "실현손익", "메모", "정산반영"]),
                                       asset_hist, idx, initial_capital=1_000_000.0)
    ix = r["index"]
    assert ix["코스피"].iloc[0] == 0.0 and ix["코스닥"].iloc[0] == 0.0
    assert ix["코스피"].iloc[-1] == pytest.approx(-0.05)   # 100 → 95
    assert ix["코스닥"].iloc[-1] == pytest.approx(0.05)    # 200 → 210


def test_compute_index_vs_account_latest_has_cum_and_day():
    """latest에 각 선의 (누적, 당일)이 들어와야 하고, 지수 당일은 마지막 거래일 등락,
    내 주식/계좌 당일은 마지막 스냅샷 구간 변화여야 한다."""
    tx = pd.DataFrame([_tx_row("t1", "2026-01-05", "A", "매수", 6, 100_000)])
    asset_hist = pd.DataFrame([
        {"날짜": "2026-01-05", "총자산": 1_000_000.0, "조정자산": 1_000_000.0},
        {"날짜": "2026-01-06", "총자산": 1_003_500.0, "조정자산": 1_003_500.0},
        {"날짜": "2026-01-07", "총자산": 1_000_500.0, "조정자산": 1_000_500.0},
    ])
    idx = _idx_hist([
        ["2026-01-05", 100.0, 200.0],
        ["2026-01-06", 101.0, 200.0],
        ["2026-01-07", 99.0, 210.0],   # 코스피 당일 -1.98%, 코스닥 당일 +5%
    ])
    r = core.compute_index_vs_account(tx, asset_hist, idx, initial_capital=1_000_000.0)
    kc, kd = r["latest"]["코스피"]
    assert kc == pytest.approx(-0.01)          # 100 → 99 누적
    assert kd == pytest.approx(99.0 / 101.0 - 1.0)  # 당일
    qc, qd = r["latest"]["코스닥"]
    assert qd == pytest.approx(0.05)
    _, sday = r["latest"]["주식"]
    _, aday = r["latest"]["계좌"]
    assert aday == pytest.approx((1_000_500.0 - 1_003_500.0) / 1_000_000.0)  # 마지막 구간 계좌수익 변화


def test_compute_index_vs_account_blended_benchmark():
    """kospi_weight를 주면 벤치 = wk·코스피 + (1-wk)·코스닥, 민감도 기준도 '혼합'.
    안 주면 벤치 = 코스피 단독, 기준은 '코스피'."""
    asset_hist = pd.DataFrame([
        {"날짜": "2026-01-05", "총자산": 1_000_000.0, "조정자산": 1_000_000.0},
        {"날짜": "2026-01-07", "총자산": 1_000_000.0, "조정자산": 1_000_000.0},
    ])
    idx = _idx_hist([
        ["2026-01-05", 100.0, 100.0],
        ["2026-01-07", 90.0, 110.0],   # 코스피 -10%, 코스닥 +10%
    ])
    empty_tx = pd.DataFrame(columns=["id", "날짜", "종목명", "구분", "수량", "단가", "실현손익", "메모", "정산반영"])
    r = core.compute_index_vs_account(empty_tx, asset_hist, idx, initial_capital=1_000_000.0,
                                       kospi_weight=0.75)
    bench_cum, _ = r["latest"]["벤치"]
    assert bench_cum == pytest.approx(0.75 * -0.10 + 0.25 * 0.10)  # -0.05
    assert r["me"]["벤치누적"].iloc[-1] == pytest.approx(-0.05)
    assert r["sensitivity_basis"] == "혼합"

    r2 = core.compute_index_vs_account(empty_tx, asset_hist, idx, initial_capital=1_000_000.0)
    assert r2["latest"]["벤치"][0] == pytest.approx(-0.10)   # 코스피 단독
    assert r2["sensitivity_basis"] == "코스피"


def test_capture_down_market():
    """하락일 캡처(2026-09-07 설계): 벤치의 0.5배로만 빠지면 c = 내당일÷벤치당일 = 0.5.
    DC(하락일 c 평균) = 0.5, 방어율(c<1)은 전부 충족, 상승일 없으니 UC는 None."""
    dates = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09",
             "2026-01-12", "2026-01-13"]
    kospi = [100.0, 99.0, 98.0, 97.0, 96.0, 95.0, 94.0]   # 매일 약 -1%
    idx = _idx_hist([[d, k, 100.0] for d, k in zip(dates, kospi)])
    tx = pd.DataFrame([_tx_row("t1", dates[0], "A", "매수", 1000, 1000)])  # 예수금 0 → 계좌==주식
    asset = [1_000_000.0 * (1 + 0.5 * (k / 100.0 - 1.0)) for k in kospi]
    asset_hist = pd.DataFrame([{"날짜": d, "총자산": a, "조정자산": a} for d, a in zip(dates, asset)])
    r = core.compute_index_vs_account(tx, asset_hist, idx, initial_capital=1_000_000.0, kospi_weight=1.0)
    ca = r["cap"]["acct"]
    assert ca["dc"] == pytest.approx(0.5, abs=1e-9)
    assert ca["uc"] is None
    assert ca["era"] == (6, 6)          # 하락 6구간 전부 c=0.5 < 1
    assert ca["pct"] == (0, 0)
    assert ca["today"] == pytest.approx(0.5) and ca["today_bucket"] == "하락"
    assert r["n"] == {"down": 6, "up": 0, "even": 0}
    assert r["cap"]["stock"]["dc"] == pytest.approx(0.5, abs=5e-3)   # Rs는 복리라 근사
    assert list(r["me"]["바구니"]) == [""] + ["하락"] * 6
    assert pd.isna(r["me"]["캡처계좌"].iloc[0])
    assert r["sensitivity_basis"] == "혼합"


def test_capture_up_and_down_buckets():
    """하락일·상승일 캡처(DC/UC = Σ내당일/Σ벤치당일)와 방어율(c<1)/승률(c>=1) 카운트."""
    dates = ["2026-02-02", "2026-02-03", "2026-02-04", "2026-02-05", "2026-02-06"]
    kospi = [100.0, 98.0, 96.0, 100.0, 104.0]   # 누적 0/-2%/-4%/0/+4% → 당일 -2,-2,+4,+4
    idx = _idx_hist([[d, k, 100.0] for d, k in zip(dates, kospi)])
    tx = pd.DataFrame([_tx_row("t1", dates[0], "A", "매수", 1000, 1000)])  # 예수금 0 → 계좌==주식
    tot = [1_000_000.0, 990_000.0, 975_000.0, 995_000.0, 1_025_000.0]
    #  계좌수익: 0/-1%/-2.5%/-0.5%/+2.5%  →  당일 -1%,-1.5%,+2%,+3%
    #  하락 Σ내/Σ벤치 = (-0.01-0.015)/(-0.02-0.02) = 0.625 ; 상승 = (0.02+0.03)/(0.04+0.04) = 0.625
    asset_hist = pd.DataFrame([{"날짜": d, "총자산": a, "조정자산": a} for d, a in zip(dates, tot)])
    r = core.compute_index_vs_account(tx, asset_hist, idx, initial_capital=1_000_000.0, kospi_weight=1.0)
    ca = r["cap"]["acct"]
    assert ca["dc"] == pytest.approx((-0.025) / (-0.04))   # 0.625
    assert ca["uc"] == pytest.approx(0.05 / 0.08)          # 0.625
    assert ca["era"] == (2, 2)          # 하락 일별 c 둘 다 < 1
    assert ca["pct"] == (0, 2)          # 상승 일별 c 둘 다 < 1 → 승리 0
    assert ca["today"] == pytest.approx(0.75) and ca["today_bucket"] == "상승"
    assert r["n"] == {"down": 2, "up": 2, "even": 0}
    assert list(r["me"]["바구니"]) == ["", "하락", "하락", "상승", "상승"]
    assert r["me"]["캡처계좌"].dropna().round(6).tolist() == [0.5, 0.75, 0.5, 0.75]


def test_capture_dc_is_ratio_of_sums_not_mean_of_daily_ratios():
    """DC/UC 누적 = Σ내당일/Σ벤치당일 (일별 비율 평균 아님 — 벤치 작은 날에 안 튐, 2026-09-07).
    하락 2일: 일별 c = 0.8, 0.5 → mean=0.65 이지만 Σ/Σ = -0.024/-0.045 ≈ 0.5333."""
    dates = ["2026-05-04", "2026-05-05", "2026-05-06"]
    kospi = [100.0, 99.5, 95.5]   # 누적 0/-0.5%/-4.5% → 당일 -0.5%, -4.0%
    idx = _idx_hist([[d, k, 100.0] for d, k in zip(dates, kospi)])
    tx = pd.DataFrame([_tx_row("t1", dates[0], "A", "매수", 1000, 1000)])
    tot = [1_000_000.0, 996_000.0, 976_000.0]   # 계좌수익 0/-0.4%/-2.4% → 당일 -0.4%, -2.0%
    asset_hist = pd.DataFrame([{"날짜": d, "총자산": a, "조정자산": a} for d, a in zip(dates, tot)])
    r = core.compute_index_vs_account(tx, asset_hist, idx, initial_capital=1_000_000.0, kospi_weight=1.0)
    ca = r["cap"]["acct"]
    assert list(r["me"]["바구니"]) == ["", "하락", "하락"]
    assert r["me"]["캡처계좌"].dropna().round(6).tolist() == [0.8, 0.5]   # 일별 c
    assert ca["dc"] == pytest.approx((-0.004 + -0.02) / (-0.005 + -0.04))  # 0.5333, NOT 0.65
    assert abs(ca["dc"] - 0.65) > 0.1


def test_capture_even_bucket_uses_excess_return():
    """|벤치당일| <= 0.1%인 날은 even 바구니 — 캡처 비율 대신 초과수익(내당일 − 벤치당일)으로
    집계, even 승률 = 초과수익 >= +0.1% 비율. even_anomalies에도 기록."""
    dates = ["2026-04-01", "2026-04-02", "2026-04-03", "2026-04-06"]
    kospi = [100.0, 100.05, 100.10, 98.0]   # 당일 +0.05%, +0.05%, -2.1%
    idx = _idx_hist([[d, k, 100.0] for d, k in zip(dates, kospi)])
    tx = pd.DataFrame([_tx_row("t1", dates[0], "A", "매수", 1000, 1000)])
    tot = [1_000_000.0, 1_003_000.0, 999_000.0, 990_000.0]
    #  계좌수익: 0/+0.3%/-0.1%/-1.0%  →  당일 +0.3%, -0.4%, -0.9%
    asset_hist = pd.DataFrame([{"날짜": d, "총자산": a, "조정자산": a} for d, a in zip(dates, tot)])
    r = core.compute_index_vs_account(tx, asset_hist, idx, initial_capital=1_000_000.0, kospi_weight=1.0)
    me = r["me"]
    assert list(me["바구니"]) == ["", "even", "even", "하락"]
    ca = r["cap"]["acct"]
    # even 초과수익: i1 = 0.003 − 0.0005 = 0.0025 ; i2 = −0.004 − 0.0005 = −0.0045
    assert ca["even"] == pytest.approx((0.0025 + (-0.0045)) / 2)
    assert ca["evr"] == (1, 2)          # i1(+0.25%) >= 0.1% 충족, i2 미충족
    assert ca["dc"] is not None         # 하락일 1개
    assert r["n"] == {"down": 1, "up": 0, "even": 2}
    assert [a["날짜"] for a in r["even_anomalies"]] == ["2026-04-02", "2026-04-03"]
    assert r["even_anomalies"][0]["초과_계좌"] == pytest.approx(0.0025)


def test_market_cache_roundtrip(tmp_path, monkeypatch):
    f = tmp_path / "mkt.csv"
    monkeypatch.setattr(core, "MARKET_CACHE_FILE", f)
    core.update_market_cache({"A": "KOSPI", "B": "KOSDAQ", "C": "이상한값"})
    got = core.load_market_cache()
    assert got == {"A": "KOSPI", "B": "KOSDAQ"}  # 유효하지 않은 값은 저장 안 됨


# ------------------------------------------------------------------ #
# Volume/Foreigner 정렬·순위 (§6-12, 2026-09-02 — Fishing식 누적/전일 × DOWN/UP)
# ------------------------------------------------------------------ #
def _flow_df(rows):
    """rows: [(종목코드, 날짜, 거래량, 외국인보유율)] → load_investor_flow_db 형태."""
    return pd.DataFrame(
        [{"종목코드": c, "종목명": f"종목{c}", "섹터": "미분류", "날짜": d,
          "거래량": v, "기관순매수": 0, "외국인순매수": 0, "외국인보유율": fp}
         for c, d, v, fp in rows],
        columns=["종목코드", "종목명", "섹터", "날짜", "거래량", "기관순매수", "외국인순매수", "외국인보유율"],
    )


def test_rank_flow_flags_direction_and_order():
    # A: 오늘 보유율이 평균보다 크게 위(UP), B: 크게 아래(DOWN), C: 살짝 위
    hist = _flow_df([
        ("A", "2026-01-05", 100, 10.0), ("A", "2026-01-06", 100, 15.0),
        ("B", "2026-01-05", 100, 20.0), ("B", "2026-01-06", 100, 14.0),
        ("C", "2026-01-05", 100, 10.0), ("C", "2026-01-06", 100, 10.6),
    ])
    flags = core.compute_foreign_flags(hist)
    key = core.FLOW_BASIS_KEY["foreign"]["누적"]  # vs평균pp

    up = core.rank_flow_flags(flags, key, "UP")
    assert [f["종목명"] for f in up] == ["종목A", "종목C"]  # A가 더 크게 위 → 1위
    down = core.rank_flow_flags(flags, key, "DOWN")
    assert [f["종목명"] for f in down] == ["종목B"]         # 위로 간 A·C는 DOWN에서 제외


def test_get_flow_prev_day_ranks_uses_day_before_today():
    # 3일치. today=1/07 → prev_date=1/06까지만으로 순위 계산.
    hist = _flow_df([
        ("A", "2026-01-05", 100, 10.0), ("A", "2026-01-06", 100, 13.0), ("A", "2026-01-07", 100, 30.0),
        ("B", "2026-01-05", 100, 10.0), ("B", "2026-01-06", 100, 16.0), ("B", "2026-01-07", 100, 10.5),
    ])
    pr = core.get_flow_prev_day_ranks(hist, "foreign", "누적", "UP", "2026-01-07")
    # 1/06 기준: B가 평균 대비 더 크게 위(+4pp vs A +2pp) → B 1위, A 2위
    assert pr == {"종목B": 1, "종목A": 2}
    # 오늘(1/07)까지 다 쓰면 A가 폭등해서 1위 → prev와 달라야 함(=▲▼ 표시 근거)
    now = core.rank_flow_flags(core.compute_foreign_flags(hist),
                                core.FLOW_BASIS_KEY["foreign"]["누적"], "UP")
    assert now[0]["종목명"] == "종목A"


# --- 포리너 프로젝트(포프) 초안: study_foreign_buy_forward_returns --- #
def _price_df(rows):
    """rows: [(종목코드, 날짜, 종가)] → load_watchlist_history_db 형태."""
    return pd.DataFrame(
        [{"종목코드": c, "종목명": f"종목{c}", "섹터": "미분류", "날짜": d, "종가": p, "등락률": 0.0}
         for c, d, p in rows],
        columns=["종목코드", "종목명", "섹터", "날짜", "종가", "등락률"],
    )


def test_fop_detects_event_and_computes_forward_market_excess():
    dates = ["2026-08-20", "2026-08-21", "2026-08-22", "2026-08-23", "2026-08-24", "2026-08-25"]
    # A: 08-22에 외인보유율 +0.9%p 급등(전일 기준) → 이벤트. 이후 주가 100→100→103→106.
    # B: 노이즈(보유율 거의 안 움직임), 주가 고정.
    flow = _flow_df(
        [("A", d, 1000, 10.0 + (0.9 if d == "2026-08-22" else 0.0)) for d in dates]
        + [("B", d, 1000, 12.0 + 0.01 * i) for i, d in enumerate(dates)]
    )
    apx = [100, 101, 100, 100, 103, 106]
    price = _price_df(
        [("A", d, apx[i]) for i, d in enumerate(dates)]
        + [("B", d, 50) for d in dates]
    )
    idx = pd.DataFrame({"날짜": dates, "KOSPI": [2000, 2000, 2000, 2000, 2010, 2010],
                        "KOSDAQ": [800] * 6})
    market_map = {"종목A": "KOSPI", "종목B": "KOSDAQ"}

    r = core.study_foreign_buy_forward_returns(flow, price, idx, market_map,
                                              basis="전일", threshold_pp=0.3, min_history=0)
    assert r["n_events"] == 1
    ev = r["recent_events"][0]
    assert ev["종목명"] == "종목A" and ev["날짜"] == "2026-08-22"
    # T+2 = 08-24: A 100→103(+3%), 코스피 2000→2010(+0.5%) → 시장초과 +2.5%
    assert r["horizons"][2]["n"] == 1
    assert r["horizons"][2]["exc_mean"] == pytest.approx(0.025, abs=1e-6)
    # 대조군(전체 종목·전체일)도 같이 잡힘 → edge_mean 계산 가능
    assert r["horizons"][2]["base_n"] >= 1
    assert r["horizons"][2]["edge_mean"] is not None


def test_fop_average_basis_uses_expanding_mean_like_screener():
    # 보유율 10,10,10,11 → 평균 기준 마지막날 delta = 11 - mean(10,10,10,11)=10.25 → +0.75%p
    dates = ["2026-08-20", "2026-08-21", "2026-08-22", "2026-08-23", "2026-08-24"]
    flow = _flow_df([("A", d, 1000, v) for d, v in zip(dates, [10, 10, 10, 11, 11])])
    price = _price_df([("A", d, 100) for d in dates])
    idx = pd.DataFrame({"날짜": dates, "KOSPI": [2000] * 5, "KOSDAQ": [800] * 5})

    hi = core.study_foreign_buy_forward_returns(flow, price, idx, {"종목A": "KOSPI"},
                                               basis="평균", threshold_pp=0.7, min_history=0)
    lo = core.study_foreign_buy_forward_returns(flow, price, idx, {"종목A": "KOSPI"},
                                               basis="평균", threshold_pp=0.8, min_history=0)
    assert hi["n_events"] == 1   # +0.75%p ≥ 0.7 문턱
    assert lo["n_events"] == 0   # +0.75%p < 0.8 문턱


def test_fop_min_history_skips_warmup_events():
    # 8일치. A는 매일 보유율 +1%p씩 계단 상승 → 전일 기준 매일 이벤트 후보.
    dates = [f"2026-08-{20 + i:02d}" for i in range(8)]
    flow = _flow_df([("A", d, 1000, 10.0 + i) for i, d in enumerate(dates)])
    price = _price_df([("A", d, 100) for d in dates])
    idx = pd.DataFrame({"날짜": dates, "KOSPI": [2000] * 8, "KOSDAQ": [800] * 8})

    warm5 = core.study_foreign_buy_forward_returns(flow, price, idx, {"종목A": "KOSPI"},
                                                  basis="전일", threshold_pp=0.5, min_history=5)
    warm0 = core.study_foreign_buy_forward_returns(flow, price, idx, {"종목A": "KOSPI"},
                                                  basis="전일", threshold_pp=0.5, min_history=0)
    # min_history=0이면 2번째 날부터 매일(7건), =5면 6번째 관측(i=5)부터만(i=5,6,7 → 3건)
    assert warm0["n_events"] == 7
    assert warm5["n_events"] == 3


def test_fop_empty_inputs_return_zero_events():
    empty_flow = _flow_df([])
    empty_price = _price_df([])
    r = core.study_foreign_buy_forward_returns(empty_flow, empty_price,
                                              pd.DataFrame(columns=["날짜", "KOSPI", "KOSDAQ"]), {})
    assert r["n_events"] == 0
    assert r["recent_events"] == []
    assert all(h["n"] == 0 for h in r["horizons"].values())


# --- SamHynix extracted (§6-19): synthetic_kospi_ex_bigcap --- #
def _bigcap_df(rows):
    """rows: [(날짜, 삼성전자, 삼성전자우, SK하이닉스)] → load_bigcap_history 형태."""
    return pd.DataFrame(rows, columns=["날짜", "삼성전자", "삼성전자우", "SK하이닉스"])


def test_synthetic_kospi_ex_bigcap_anchor_and_fallback():
    idx = pd.DataFrame({"날짜": ["2026-08-14", "2026-08-18"],
                        "KOSPI": [6900.0, 6800.0], "KOSDAQ": [800.0, 790.0]})
    # bigcap 데이터 없음 → 원본 그대로
    out = core.synthetic_kospi_ex_bigcap(idx, core.load_bigcap_history().iloc[0:0])
    assert list(out["KOSPI"]) == [6900.0, 6800.0]
    # 있어도 첫날 레벨은 원본 KOSPI 첫날 값에서 출발(누적 앵커 동일)
    bg = _bigcap_df([("2026-08-14", 250000, 188000, 1600000),
                     ("2026-08-18", 250000, 188000, 1600000)])
    out2 = core.synthetic_kospi_ex_bigcap(idx, bg)
    assert out2["KOSPI"].iloc[0] == 6900.0


def test_synthetic_kospi_ex_bigcap_flat_bigcaps_amplify_the_rest():
    # KOSPI +2%인데 삼성·삼성우·하이닉스는 그대로 → 나머지(=지수 ex-반도체)는 2%보다 더 올라야 함
    idx = pd.DataFrame({"날짜": ["2026-08-14", "2026-08-18"],
                        "KOSPI": [6000.0, 6120.0], "KOSDAQ": [800.0, 800.0]})
    bg = _bigcap_df([("2026-08-14", 250000, 188000, 1600000),
                     ("2026-08-18", 250000, 188000, 1600000)])
    out = core.synthetic_kospi_ex_bigcap(idx, bg)
    assert out["KOSPI"].iloc[1] > 6120.0


def test_synthetic_kospi_ex_bigcap_identical_moves_leave_ex_index_unchanged():
    # 3종목이 전부 KOSPI와 똑같이 +5% → 그 부분집합을 빼도 나머지 수익률은 그대로 +5%
    idx = pd.DataFrame({"날짜": ["2026-08-14", "2026-08-18"],
                        "KOSPI": [6000.0, 6300.0], "KOSDAQ": [800.0, 800.0]})
    bg = _bigcap_df([("2026-08-14", 200000, 180000, 1600000),
                     ("2026-08-18", 210000, 189000, 1680000)])  # 전부 ×1.05
    out = core.synthetic_kospi_ex_bigcap(idx, bg)
    assert out["KOSPI"].iloc[1] == pytest.approx(6300.0, rel=1e-6)


def test_synthetic_kospi_ex_bigcap_missing_middle_day_does_not_blow_up():
    """bigcap_history에 중간 날짜(2026-08-19)가 빠지면, 예전엔 그 다음 날 대형주의
    '이틀치 수익률'을 KOSPI '하루치'에서 빼서 ex 지수가 폭주했다(2026-09-08 실제로 -10%).
    이제 대형주 수익률 구간과 KOSPI 수익률 구간이 정확히 일치할 때만 조정하고,
    한쪽 날짜가 비면 그 구간은 r_ex = r_k(코스피와 동일)로 둔다."""
    idx = pd.DataFrame({"날짜": ["2026-08-14", "2026-08-19", "2026-08-20"],
                        "KOSPI": [6000.0, 6060.0, 6090.0], "KOSDAQ": [800.0, 800.0, 800.0]})
    # 8/19 대형주 종가 없음. 8/14 → 8/20 사이 대형주가 크게 올랐어도(하루치로 오해되면 폭주)
    bg = _bigcap_df([("2026-08-14", 200000, 180000, 1600000),
                     ("2026-08-20", 260000, 234000, 2080000)])  # 전부 ×1.30
    out = core.synthetic_kospi_ex_bigcap(idx, bg)
    lv = list(out["KOSPI"])
    # 8/19 구간: bigcap 8/19 없음 → r_ex = r_k → 6000*1.01 = 6060
    assert lv[1] == pytest.approx(6060.0, rel=1e-6)
    # 8/20 구간: 직전(8/19) bigcap 없음 → 역시 r_ex = r_k → 6060*(6090/6060) = 6090 (폭주 안 함)
    assert lv[2] == pytest.approx(6090.0, rel=1e-6)


# --- SamsungHynix (§6-26): synthetic_kospi_sh_only --- #
def test_synthetic_kospi_sh_only_anchor_and_fallback():
    idx = pd.DataFrame({"날짜": ["2026-08-14", "2026-08-18"],
                        "KOSPI": [6900.0, 6800.0], "KOSDAQ": [800.0, 790.0]})
    # bigcap 없음 → 원본 그대로
    out = core.synthetic_kospi_sh_only(idx, core.load_bigcap_history().iloc[0:0])
    assert list(out["KOSPI"]) == [6900.0, 6800.0]
    # 있으면 첫날 레벨 = 원본 KOSPI 첫날 값(누적 앵커 동일)
    bg = _bigcap_df([("2026-08-14", 250000, 188000, 1600000),
                     ("2026-08-18", 250000, 188000, 1600000)])
    out2 = core.synthetic_kospi_sh_only(idx, bg)
    assert out2["KOSPI"].iloc[0] == 6900.0
    # 3종목 그대로면 SH 지수도 그대로(수익률 0)
    assert out2["KOSPI"].iloc[1] == pytest.approx(6900.0, rel=1e-9)


def test_synthetic_kospi_sh_only_identical_returns_ignore_weights():
    # 3종목이 전부 정확히 +5% → 가중치가 뭐든 SH 지수 = (1+0.05) 복리, KOSPI 움직임과 무관
    idx = pd.DataFrame({"날짜": ["2026-08-14", "2026-08-18", "2026-08-19"],
                        "KOSPI": [6000.0, 5800.0, 6400.0],  # KOSPI는 딴 방향으로 움직여도
                        "KOSDAQ": [800.0, 800.0, 800.0]})
    bg = _bigcap_df([("2026-08-14", 200000, 180000, 1600000),
                     ("2026-08-18", 210000, 189000, 1680000),   # ×1.05
                     ("2026-08-19", 220500, 198450, 1764000)])  # ×1.05 다시
    out = core.synthetic_kospi_sh_only(idx, bg)
    assert out["KOSPI"].iloc[1] == pytest.approx(6000.0 * 1.05, rel=1e-6)
    assert out["KOSPI"].iloc[2] == pytest.approx(6000.0 * 1.05 * 1.05, rel=1e-6)


def test_synthetic_kospi_sh_only_prev_day_cap_weights():
    # 삼성전자만 +10%, 나머지 flat → r_SH ≈ (전일 삼성전자 시총비중) × 0.10
    s_sh, s_pr, s_hy = core._BIGCAP_SHARES["삼성전자"], core._BIGCAP_SHARES["삼성전자우"], core._BIGCAP_SHARES["SK하이닉스"]
    p_sh, p_pr, p_hy = 250000.0, 190000.0, 1600000.0
    w_sh = (s_sh * p_sh) / (s_sh * p_sh + s_pr * p_pr + s_hy * p_hy)
    idx = pd.DataFrame({"날짜": ["2026-08-14", "2026-08-18"],
                        "KOSPI": [6000.0, 6000.0], "KOSDAQ": [800.0, 800.0]})
    bg = _bigcap_df([("2026-08-14", p_sh, p_pr, p_hy),
                     ("2026-08-18", p_sh * 1.10, p_pr, p_hy)])
    out = core.synthetic_kospi_sh_only(idx, bg)
    assert out["KOSPI"].iloc[1] == pytest.approx(6000.0 * (1 + w_sh * 0.10), rel=1e-6)


def test_synthetic_kospi_sh_only_missing_middle_day_does_not_blow_up():
    idx = pd.DataFrame({"날짜": ["2026-08-14", "2026-08-19", "2026-08-20"],
                        "KOSPI": [6000.0, 6060.0, 6090.0], "KOSDAQ": [800.0, 800.0, 800.0]})
    bg = _bigcap_df([("2026-08-14", 200000, 180000, 1600000),
                     ("2026-08-20", 260000, 234000, 2080000)])  # 8/19 없음, 전부 ×1.30
    out = core.synthetic_kospi_sh_only(idx, bg)
    lv = list(out["KOSPI"])
    assert lv[1] == pytest.approx(6060.0, rel=1e-6)   # 8/19 bigcap 없음 → r_SH = r_k
    assert lv[2] == pytest.approx(6090.0, rel=1e-6)   # 직전(8/19) 없음 → r_SH = r_k, 폭주 안 함


# --- check_history_alignment (§6-2): ingest 끝 정합성 자동 체크 --- #
def test_check_history_alignment_flags_misaligned(monkeypatch, tmp_path):
    a = tmp_path / "asset_history.csv"; s = tmp_path / "sector_history.csv"
    i = tmp_path / "index_history.csv"; b = tmp_path / "bigcap_history.csv"
    pd.DataFrame({"날짜": ["2026-09-08", "2026-09-09", "2026-09-10"], "총자산": [1, 2, 3]}).to_csv(a, index=False)
    pd.DataFrame({"날짜": ["2026-09-08", "2026-09-10"], "섹터그룹": ["식품", "식품"], "비중": [1, 1]}).to_csv(s, index=False)
    pd.DataFrame({"날짜": ["2026-09-08", "2026-09-09"], "KOSPI": [1, 2], "KOSDAQ": [1, 2]}).to_csv(i, index=False)  # 뒤처짐
    pd.DataFrame({"날짜": ["2026-09-08", "2026-09-09", "2026-09-10"], "삼성전자": [1, 2, 3]}).to_csv(b, index=False)
    monkeypatch.setattr(core, "HISTORY_FILE", a)
    monkeypatch.setattr(core, "SECTOR_HISTORY_FILE", s)
    monkeypatch.setattr(core, "INDEX_HISTORY_FILE", i)
    monkeypatch.setattr(core, "BIGCAP_HISTORY_FILE", b)

    r = core.check_history_alignment("2026-09-10")
    assert r["aligned"] is False
    assert r["behind"] == ["index_history"]           # 9/9에서 멈춤
    assert r["latest"] == "2026-09-10"
    assert r["target_ok"] is False                    # index_history에 9/10 없음

    # index_history를 9/10까지 채우면 정합성 OK
    pd.DataFrame({"날짜": ["2026-09-08", "2026-09-09", "2026-09-10"],
                  "KOSPI": [1, 2, 3], "KOSDAQ": [1, 2, 3]}).to_csv(i, index=False)
    r2 = core.check_history_alignment("2026-09-10")
    assert r2["aligned"] is True and r2["behind"] == [] and r2["target_ok"] is True


# ------------------------------------------------------------------ #
# compute_pnl_actions (§6-20) — 실현손익을 FA/MO/MA 매매 스타일로 해부
# ------------------------------------------------------------------ #
def test_pnl_actions_buckets_and_watering():
    """FA = 1매수·부분매도 없음·전량청산, MA = 2+매수·부분매도 없음·전량청산,
    MO = 부분매도 1회라도 있으면(우선순위 최상, 청산 여부 무관). Watering 상세도 검증."""
    tx = pd.DataFrame([
        # A: FA (buy once, sell all)
        _tx_row("a1", "2026-01-05", "A", "매수", 10, 100),
        _tx_row("a2", "2026-01-10", "A", "매도", 10, 110, 실현손익=100),
        # B: MA (물타기 2매수 → 한 방에 전량청산, 부분매도 없음)
        _tx_row("b1", "2026-01-05", "B", "매수", 10, 100),
        _tx_row("b2", "2026-01-06", "B", "매수", 10, 80),
        _tx_row("b3", "2026-01-12", "B", "매도", 20, 95, 실현손익=200),
        # C: MO (부분매도 후 아직 보유 중)
        _tx_row("c1", "2026-01-05", "C", "매수", 10, 100),
        _tx_row("c2", "2026-01-11", "C", "매도", 4, 120, 실현손익=50),
        # D: MO (부분매도 후 전량청산 — 그래도 MO)
        _tx_row("d1", "2026-01-05", "D", "매수", 10, 100),
        _tx_row("d2", "2026-01-09", "D", "매도", 5, 110, 실현손익=30),
        _tx_row("d3", "2026-01-13", "D", "매도", 5, 115, 실현손익=40),
        # E: Watering (2매수, 매도 없음, 보유 중)
        _tx_row("e1", "2026-01-05", "E", "매수", 10, 100),
        _tx_row("e2", "2026-01-07", "E", "매수", 10, 60),
    ])
    holdings = pd.DataFrame([
        {"종목명": "C", "종목코드": "003", "섹터": "", "수량": 6, "평단가": 100, "현재가": 90,
         "등락률": 0, "업데이트시각": ""},
        {"종목명": "E", "종목코드": "005", "섹터": "", "수량": 20, "평단가": 80, "현재가": 70,
         "등락률": 0, "업데이트시각": ""},
    ])
    r = core.compute_pnl_actions(tx, holdings)
    assert r["total"] == pytest.approx(420)
    assert r["baskets"]["FA"]["realized"] == pytest.approx(100)
    assert r["baskets"]["FA"]["n_cycle"] == 1 and r["baskets"]["FA"]["amt_total"] == pytest.approx(1000)
    assert r["baskets"]["FA"]["avg_pct"] == pytest.approx(10.0)          # 100 / 1000
    assert r["baskets"]["MA"]["realized"] == pytest.approx(200)
    assert r["baskets"]["MA"]["amt_total"] == pytest.approx(1800)        # 1000 + 800
    assert r["baskets"]["MO"]["realized"] == pytest.approx(120)          # C 50 + D 70
    assert r["baskets"]["MO"]["n_cycle"] == 2
    assert (r["baskets"]["MO"]["closed"], r["baskets"]["MO"]["open"]) == (1, 1)  # D 청산, C 진행
    st = r["status"]
    assert st["n_total"] == 5
    assert st["FA"] == (1, 5) and st["MA"] == (1, 5) and st["MO"] == (2, 5)
    assert st["holds"] == (1, 2) and st["watering"] == (1, 2)           # C=holds, E=watering
    assert st["holds_pl_pct"] == pytest.approx(-10.0)                    # 540/600 - 1
    wd = r["watering"]
    assert wd["n_stock"] == 1 and wd["n_extra_buys"] == 1
    assert wd["seed_first"] == pytest.approx(1000)                       # 10 × 100 (첫 매수)
    assert wd["seed_now"] == pytest.approx(1600)                         # 20 × 80 (평단 × 현재수량)
    assert wd["seed_mult"] == pytest.approx(1.6)
    assert wd["pl_avg_pct"] == pytest.approx(-12.5)                      # 1400 / 1600 - 1
    assert wd["pl_first_pct"] == pytest.approx(-30.0)                    # 1400 / 2000 - 1
    assert wd["absorbed_pp"] == pytest.approx(17.5)                      # -12.5 - (-30)


def test_compute_index_vs_account_caps_me_to_index_coverage():
    """index_hist가 asset_hist보다 뒤처지면(매매일지 반영으로 asset엔 오늘 행이 생겼는데
    index_history엔 아직 없음) 그 앞선 asset 행의 벤치당일이 0으로 계산돼 "혼합지수 당일
    0.00%"·"오늘 even일" 버그가 났음(2026-09-07). 두 히스토리 공통 커버 마지막 날까지만 써야 한다."""
    dates = ["2026-01-05", "2026-01-06", "2026-01-07"]
    kospi = [100.0, 102.0, 104.0]
    idx = _idx_hist([[d, k, 100.0] for d, k in zip(dates[:2], kospi[:2])])  # index는 1/6까지만
    tx = pd.DataFrame([_tx_row("t1", dates[0], "A", "매수", 1000, 1000)])
    tot = [1_000_000.0, 1_010_000.0, 1_025_000.0]                          # asset은 1/7까지
    asset_hist = pd.DataFrame([{"날짜": d, "총자산": a, "조정자산": a} for d, a in zip(dates, tot)])
    r = core.compute_index_vs_account(tx, asset_hist, idx, initial_capital=1_000_000.0, kospi_weight=1.0)
    assert list(r["me"]["날짜"]) == ["2026-01-05", "2026-01-06"]           # 1/7 잘림
    assert r["latest"]["벤치"][1] != pytest.approx(0.0)                     # 벤치당일이 0(가짜 even)이 아님
    assert r["me"]["바구니"].iloc[-1] == "상승"                            # 1/6은 진짜 상승일
    assert r["n"]["even"] == 0


def test_compute_index_vs_account_fund_line(monkeypatch, tmp_path):
    """§6-21: fund_nav_hist를 주면 index에 '펀드' 컬럼(anchor 대비 누적), latest['펀드']에
    (누적, 당일)이 붙는다. 안 주면 컬럼 없음(SamHynix 패널이 이 경로)."""
    asset_hist = pd.DataFrame([{"날짜": d, "총자산": 1_000_000.0, "조정자산": 1_000_000.0}
                               for d in ["2026-01-05", "2026-01-06", "2026-01-07"]])
    idx = _idx_hist([["2026-01-05", 100.0, 200.0], ["2026-01-06", 101.0, 201.0],
                     ["2026-01-07", 102.0, 202.0]])
    empty_tx = pd.DataFrame(columns=["id", "날짜", "종목명", "구분", "수량", "단가", "실현손익", "메모", "정산반영"])
    fund = pd.DataFrame({"날짜": ["2026-01-05", "2026-01-06", "2026-01-07"],
                         "기준가": [2000.0, 2100.0, 1980.0]})

    r = core.compute_index_vs_account(empty_tx, asset_hist, idx, 1_000_000.0, fund_nav_hist=fund)
    assert "펀드" in r["index"].columns
    assert r["index"]["펀드"].iloc[0] == pytest.approx(0.0)
    assert r["index"]["펀드"].iloc[-1] == pytest.approx(1980.0 / 2000.0 - 1.0)   # -1%
    assert r["latest"]["펀드"][0] == pytest.approx(-0.01)
    assert r["latest"]["펀드"][1] == pytest.approx(1980.0 / 2100.0 - 1.0)        # 당일 = 직전 기준가 대비

    r2 = core.compute_index_vs_account(empty_tx, asset_hist, idx, 1_000_000.0)
    assert "펀드" not in r2["index"].columns
    assert "펀드" not in r2["latest"]


def test_compute_vip_vs_orchestra_rebases_orchestra_to_anchor(monkeypatch, tmp_path):
    """§6-21: VIP는 idx_cum['펀드'](이미 anchor=0), Orchestra는 me['계좌수익']을 첫 스냅샷
    대비로 재기준화 → 둘 다 anchor일 0에서 출발. 색은 UI가 정하고 여기선 값만."""
    dates = ["2026-01-05", "2026-01-06", "2026-01-07"]
    # 8/14(anchor)에 계좌가 이미 +2% 였다가 오늘 +3% → 리베이스하면 anchor 대비 ≈ +0.98%
    tot = [1_020_000.0, 1_010_000.0, 1_030_000.0]
    asset_hist = pd.DataFrame([{"날짜": d, "총자산": a, "조정자산": a} for d, a in zip(dates, tot)])
    idx = _idx_hist([[d, 100.0, 200.0] for d in dates])
    empty_tx = pd.DataFrame(columns=["id", "날짜", "종목명", "구분", "수량", "단가", "실현손익", "메모", "정산반영"])
    fund = pd.DataFrame({"날짜": dates, "기준가": [2000.0, 2020.0, 1990.0]})

    iva = core.compute_index_vs_account(empty_tx, asset_hist, idx, 1_000_000.0, fund_nav_hist=fund)
    vo = core.compute_vip_vs_orchestra(iva)
    assert vo["vip_line"][0][1] == pytest.approx(0.0)
    assert vo["orch_line"][0][1] == pytest.approx(0.0)                     # anchor에서 0
    assert vo["vip"][0] == pytest.approx(1990.0 / 2000.0 - 1.0)            # -0.5%
    # Orchestra 누적 = (1+0.03)/(1+0.02) - 1
    assert vo["orch"][0] == pytest.approx((1 + 0.03) / (1 + 0.02) - 1.0, rel=1e-6)

    # 펀드 데이터 없으면 {}
    assert core.compute_vip_vs_orchestra(
        core.compute_index_vs_account(empty_tx, asset_hist, idx, 1_000_000.0)) == {}


def test_compute_vip_vs_orchestra_peer_latest_overrides_other_account(monkeypatch, tmp_path):
    """§6-21 런타임 채널: self_key='orchestration'일 때 Orchestra(상대) 표 값·선 마지막 점은
    peer_latest(Supabase account_snapshot)에서 온다. both_accounts.csv보다 최신 날짜면 이어붙고,
    없거나 과거면 both_accounts.csv 폴백."""
    dates = ["2026-01-05", "2026-01-06", "2026-01-07"]
    tot = [1_000_000.0, 1_005_000.0, 1_002_000.0]
    asset_hist = pd.DataFrame([{"날짜": d, "총자산": a, "조정자산": a} for d, a in zip(dates, tot)])
    idx = _idx_hist([[d, 100.0, 200.0] for d in dates])
    empty_tx = pd.DataFrame(columns=["id", "날짜", "종목명", "구분", "수량", "단가", "실현손익", "메모", "정산반영"])
    fund = pd.DataFrame({"날짜": dates, "기준가": [2000.0, 2010.0, 1990.0]})
    iva = core.compute_index_vs_account(empty_tx, asset_hist, idx, 1_000_000.0, fund_nav_hist=fund)
    ba = pd.DataFrame({"날짜": dates, "orchestra": [0.0, -0.004, -0.0025],
                       "orchestration": [0.0, 0.003, 0.005]})

    # peer가 both_accounts보다 하루 최신 → Orchestra 표·선 마지막 점이 peer 값
    peer = {"trade_date": "2026-01-08", "cum": -0.0061, "day": -0.0036}
    vo = core.compute_vip_vs_orchestra(iva, ba, self_key="orchestration", peer_latest=peer)
    assert vo["orch"] == (pytest.approx(-0.0061), pytest.approx(-0.0036))
    assert vo["orch_line"][-1] == ("2026-01-08", pytest.approx(-0.0061))
    assert vo["orch_line"][-2] == ("2026-01-07", pytest.approx(-0.0025))   # csv 히스토리 보존
    # Orchestration(자기)은 라이브 me 그대로
    assert vo["orchn"][0] == pytest.approx((1 + 0.002) / (1 + 0.0) - 1.0, rel=1e-6)

    # peer 없으면 both_accounts.csv 폴백
    vo2 = core.compute_vip_vs_orchestra(iva, ba, self_key="orchestration", peer_latest=None)
    assert vo2["orch"][0] == pytest.approx(-0.0025)
    assert vo2["orch_line"][-1] == ("2026-01-07", pytest.approx(-0.0025))


def test_snapshot_fund_nav_history_overwrites_same_date(monkeypatch, tmp_path):
    f = tmp_path / "fund_nav_history.csv"
    monkeypatch.setattr(core, "FUND_NAV_HISTORY_FILE", f)
    core.snapshot_fund_nav_history(2000.0, on_date="2026-01-05")
    core.snapshot_fund_nav_history(2010.0, on_date="2026-01-06")
    core.snapshot_fund_nav_history(1995.0, on_date="2026-01-05")   # 같은 날짜 → 덮어씀
    out = core.load_fund_nav_history()
    assert list(out["날짜"]) == ["2026-01-05", "2026-01-06"]
    assert float(out[out["날짜"] == "2026-01-05"]["기준가"].iloc[0]) == 1995.0
