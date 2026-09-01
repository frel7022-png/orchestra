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
