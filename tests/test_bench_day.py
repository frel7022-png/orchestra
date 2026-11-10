"""혼합지수 당일 — 함수 하나로 같은 숫자(대표 2026-11-10 원-2026-11-10-04 · 05 · 06)."""
import pandas as pd
import pytest

import live_check
import portfolio_core as core


def _iq(rk=0.04, rq=0.04, k=1000.0, q=500.0):
    """현재가 k · q, 전일 대비 rk · rq인 지수 시세."""
    return {"KOSPI": {"price": k, "change": k - k / (1 + rk), "change_pct": rk * 100},
            "KOSDAQ": {"price": q, "change": q - q / (1 + rq), "change_pct": rq * 100}}


def _bq(mult=1.02):
    return {code: {"price": 100.0 * mult, "prev_close": 100.0} for code in core.BIGCAP_CODES.values()}


def _bh(rows):
    return pd.DataFrame(rows, columns=core.BENCH_COLS)


def _bh_row(d, day, src="ingest", wk=0.7):
    return [d, wk, day, day, day, 0.0, src, f"{d} 15:35"]


def test_cum_compounds_not_adds():
    """누적 −10%에 오늘 −2%는 −11.8%지 −12%가 아니다 — 대표 2026-11-10."""
    lv = pd.DataFrame({"날짜": ["2026-01-05", "2026-01-06", "2026-01-07"], "KOSPI": [100.0, 90.0, 88.2], "KOSDAQ": [100.0, 90.0, 88.2]})
    bh = _bh([_bh_row("2026-01-05", 0.0, "seed"), _bh_row("2026-01-06", -0.10), _bh_row("2026-01-07", -0.02)])
    m = core.bench_cum_map(lv, "main", 0.7, "2026-01-05", bh)
    assert m["2026-01-07"] == pytest.approx(-0.118)
    assert m["2026-01-07"] != pytest.approx(-0.12)
    assert core.day_return(pd.Series([-0.10, -0.118])).iloc[1] == pytest.approx(-0.02)


@pytest.mark.parametrize("wk", [0.0, 0.3, 0.71, 1.0])
def test_same_kospi_kosdaq_gives_that_value(wk):
    """코스피 · 코스닥이 같은 날 혼합은 비중이 얼마든 그 값이다."""
    assert core.live_bench_day(wk, _iq(0.0436, 0.0436), _bq())["main"] == pytest.approx(0.0436)


def test_blend_is_weighted_quotes():
    v = core.live_bench_day(0.75, _iq(0.04, 0.02), _bq())
    assert v["main"] == pytest.approx(0.75 * 0.04 + 0.25 * 0.02)
    assert v["ex"] is not None


def test_missing_quote_gives_none_not_zero():
    assert core.live_bench_day(0.7, {}, _bq())["main"] is None
    assert core.live_bench_day(0.7, _iq(), {})["ex"] is None


def _account_fixture():
    dates = ["2026-02-02", "2026-02-03", "2026-02-04", "2026-02-09"]   # 02-05 · 02-06 줄이 빠진 뒤의 오늘
    idx = pd.DataFrame({"날짜": dates, "KOSPI": [100.0, 90.0, 88.2, 95.0], "KOSDAQ": [100.0, 90.0, 88.2, 95.0]})
    tx = pd.DataFrame([{"id": "t1", "날짜": dates[0], "종목명": "A", "구분": "매수", "수량": 1000, "단가": 1000,
                        "실현손익": 0.0, "메모": "", "정산반영": ""}])
    tot = [1_000_000.0, 950_000.0, 940_000.0, 960_000.0]
    ah = pd.DataFrame([{"날짜": d, "총자산": a, "조정자산": a} for d, a in zip(dates, tot)])
    return tx, ah, idx


def test_past_and_cum_unchanged_by_today_value(monkeypatch):
    """오늘 값을 넣어도 지난 날 당일 · 전 구간 누적은 1bp도 안 바뀐다."""
    tx, ah, idx = _account_fixture()
    bh = _bh([_bh_row("2026-02-02", 0.0, "seed"), _bh_row("2026-02-03", -0.10), _bh_row("2026-02-04", -0.02)])
    monkeypatch.setattr(core, "load_bench_history", lambda: bh)
    base = core.compute_index_vs_account(tx, ah, idx, 1_000_000.0, kospi_weight=0.7, bench_kind="main")["me"]
    live = core.compute_index_vs_account(tx, ah, idx, 1_000_000.0, kospi_weight=0.7, bench_kind="main",
                                         today_bench_day=("2026-02-09", 0.0436))["me"]
    pd.testing.assert_series_equal(base["벤치누적"], live["벤치누적"])
    pd.testing.assert_series_equal(base["벤치당일"].iloc[:-1], live["벤치당일"].iloc[:-1])
    assert live["벤치당일"].iloc[2] == pytest.approx(-0.02)   # 하루 등락(옛 diff면 −1.8%)
    assert live["벤치누적"].iloc[2] == pytest.approx(-0.118)


def test_missing_close_day_does_not_shift_today(monkeypatch):
    """15:35가 빠져 직전 행이 며칠 전이어도 오늘 당일은 시세의 하루치다."""
    tx, ah, idx = _account_fixture()
    bh = _bh([_bh_row("2026-02-02", 0.0, "seed"), _bh_row("2026-02-03", -0.10), _bh_row("2026-02-04", -0.02)])
    monkeypatch.setattr(core, "load_bench_history", lambda: bh)
    two_days = core.compute_index_vs_account(tx, ah, idx, 1_000_000.0, kospi_weight=0.7, bench_kind="main")
    assert two_days["latest"]["벤치"][1] == pytest.approx(95.0 / 88.2 - 1, abs=1e-9)
    r = core.compute_index_vs_account(tx, ah, idx, 1_000_000.0, kospi_weight=0.7, bench_kind="main",
                                      today_bench_day=("2026-02-09", 0.0436))
    assert r["latest"]["벤치"][1] == pytest.approx(0.0436)
    assert r["me"]["바구니"].iloc[-1] == "상승"


def test_today_value_ignores_stored_bench_row_and_uses_krx_close(monkeypatch):
    """15:35 기록이 있어도 오늘 당일은 시세로 잰다 · 대형주 현재가는 저장된 오늘 KRX 종가로(현재가 NXT 안 섞임)."""
    d = "2026-02-04"
    monkeypatch.setattr(core, "today_kst_str", lambda: d)
    monkeypatch.setattr(core, "load_bench_history", lambda: _bh([_bh_row(d, -0.02)]))
    monkeypatch.setattr(core, "load_bigcap_history", lambda: pd.DataFrame(columns=["날짜", *core.BIGCAP_CODES]))
    nxt = core.today_bench_day("ex", 0.7, _iq(), _bq(1.05))
    assert core.today_bench_day("main", 0.7, _iq(), _bq())[1] == pytest.approx(0.04)
    monkeypatch.setattr(core, "load_bigcap_history", lambda: pd.DataFrame([{"날짜": d, **{n: 102.0 for n in core.BIGCAP_CODES}}]))
    krx = core.today_bench_day("ex", 0.7, _iq(), _bq(1.05))
    assert krx[1] == pytest.approx(core.live_bench_day(0.7, _iq(), _bq(1.02))["ex"])
    assert krx[1] != pytest.approx(nxt[1])


def test_live_my_day_is_quote_based_and_skips_failed():
    h = pd.DataFrame([{"수량": 10, "현재가": 110.0, "등락률": 10.0}, {"수량": 10, "현재가": 200.0, "등락률": None}])
    m = core.live_my_day(h, 1000.0)
    assert m["stock"] == pytest.approx(0.10)
    assert m["won"] == pytest.approx(100.0)
    assert m["acct"] == pytest.approx((1100 + 1000) / (1000 + 1000) - 1)


def test_lump_ingest_days_keep_their_own_days_and_today_is_quote(monkeypatch):
    """매매일지를 며칠치 몰아 넣어 줄이 건너뛰어도 지난 구간은 그대로, 오늘 내 주식 · 계좌는 시세 하루치다."""
    tx, ah, idx = _account_fixture()
    bh = _bh([_bh_row("2026-02-02", 0.0, "seed"), _bh_row("2026-02-03", -0.10), _bh_row("2026-02-04", -0.02)])
    monkeypatch.setattr(core, "load_bench_history", lambda: bh)
    base = core.compute_index_vs_account(tx, ah, idx, 1_000_000.0, kospi_weight=0.7, bench_kind="main")["me"]
    r = core.compute_index_vs_account(tx, ah, idx, 1_000_000.0, kospi_weight=0.7, bench_kind="main",
                                      today_bench_day=("2026-02-09", 0.0436), today_my_day=("2026-02-09", 0.015, 0.012))
    me = r["me"]
    pd.testing.assert_series_equal(base["주식당일"].iloc[:-1], me["주식당일"].iloc[:-1])
    pd.testing.assert_series_equal(base["계좌당일"].iloc[:-1], me["계좌당일"].iloc[:-1])
    pd.testing.assert_series_equal(base["주식수익"], me["주식수익"])
    assert r["latest"]["주식"][1] == pytest.approx(0.015) and r["latest"]["계좌"][1] == pytest.approx(0.012)
    assert r["cap"]["stock"]["today"] == pytest.approx(0.015 / 0.0436)


def test_app_path_equals_live_check(monkeypatch):
    """앱 경로(today_bench_day)와 live_check 값이 같다 · 전날 대형주는 시세의 KRX 전일 종가."""
    today, prev, anchor = "2026-02-05", "2026-02-04", "2026-02-02"
    days = [anchor, "2026-02-03", prev, today]
    k = {anchor: 100.0, "2026-02-03": 101.0, prev: 96.15384615, today: 100.0}
    q = {anchor: 50.0, "2026-02-03": 50.5, prev: 49.01960784, today: 50.0}
    # 일별시세의 전날 대형주 값은 NXT 섞인 값(95) — 시세의 전일 종가(100)와 다르다
    def daily(code, s, e):
        if code in ("KOSPI", "KOSDAQ"):
            src = k if code == "KOSPI" else q
            return [{"날짜": d, "종가": src[d]} for d in days[:-1]]
        return [{"날짜": d, "종가": 95.0 if d == prev else 100.0} for d in days[:-1]]
    bh = _bh([_bh_row(anchor, 0.0, "seed"), _bh_row("2026-02-03", 0.01), _bh_row(prev, -0.04)])
    iq = _iq(0.04, 0.02, 100.0, 50.0)
    bd = _bq(1.03)
    monkeypatch.setattr(core, "fetch_daily_price_history", daily)
    monkeypatch.setattr(core, "fetch_index_quotes", lambda: iq)
    monkeypatch.setattr(core, "fetch_bigcap_detail", lambda: bd)
    monkeypatch.setattr(core, "today_kst_str", lambda: today)
    monkeypatch.setattr(core, "market_traded_on", lambda d: True)
    monkeypatch.setattr(core, "load_bench_history", lambda: bh)
    monkeypatch.setattr(core, "load_bigcap_history", lambda: pd.DataFrame(columns=["날짜", *core.BIGCAP_CODES]))
    monkeypatch.setattr(core, "load_index_history", lambda: pd.DataFrame({"날짜": days[:-1], "KOSPI": [k[d] for d in days[:-1]], "KOSDAQ": [q[d] for d in days[:-1]]}))
    monkeypatch.setattr(core, "load_state", lambda: {"cash": 0.0, "initial": 1000.0})
    monkeypatch.setattr(core, "load_history", lambda: pd.DataFrame([{"날짜": prev, "총자산": 1000.0, "조정자산": 1000.0}]))
    monkeypatch.setattr(core, "load_transactions", lambda: pd.DataFrame(columns=["id", "날짜", "종목명", "구분", "수량", "단가", "실현손익", "메모", "정산반영"]))
    monkeypatch.setattr(core, "load_market_cache", lambda: {"A": "KOSPI", "B": "KOSDAQ"})
    h = pd.DataFrame([{"종목코드": "1", "종목명": "A", "수량": 75, "현재가": 10.0, "평단가": 10.0},
                      {"종목코드": "2", "종목명": "B", "수량": 25, "현재가": 10.0, "평단가": 10.0}])
    quotes = {"1": {"price": 10.0, "change_pct": 1.0}, "2": {"price": 10.0, "change_pct": 1.0}}
    snap = live_check.live_snapshot(holdings=h, quotes=quotes)
    app_main = core.today_bench_day("main", 0.75, iq, bd)
    app_ex = core.today_bench_day("ex", 0.75, iq, bd)
    assert snap["main"][1] == pytest.approx(app_main[1])
    assert snap["ex"][1] == pytest.approx(app_ex[1])
    assert snap["main"][1] == pytest.approx(0.75 * 0.04 + 0.25 * 0.02)
    assert float(snap["bigdf"].set_index("날짜").loc[prev, "삼성전자"]) == pytest.approx(100.0)   # NXT 95가 아니라 KRX 전일
