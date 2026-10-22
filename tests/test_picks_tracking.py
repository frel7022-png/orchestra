"""추천 성적표(picks.py)·픽 추적(tracking.py) 채점 규칙 회귀 테스트 (2026-10-19 대표 확정 규칙).

- 종목별 채점 기간: 그 종목 평소 파도(지그재그 반 파도 중간값)로 단기·중기·장기를 정하고, 기록 순간 고정한다.
- 두 기준: 혼합지수(ex) 대비 초과 + 같은 기간 관심종목 평균 대비 초과.
- 외인이 세게 이탈하면 "틀림"이 아니라 "근거 사라짐".
"""
import math

import pandas as pd
import pytest

import picks
import tracking


def _zigzag(leg: int, n_legs: int, step: float = 0.02) -> list[float]:
    """leg일 오르고 leg일 내리기를 반복하는 종가."""
    c, px = [100.0], 100.0
    for k in range(n_legs):
        sign = 1 if k % 2 == 0 else -1
        for _ in range(leg):
            px *= math.exp(sign * step)
            c.append(px)
    return c


@pytest.mark.parametrize("leg,expected", [(10, (10, 20, 60)), (5, (5, 10, 30))])
def test_stock_horizons_follows_own_wave_length(leg, expected):
    days, why = picks.stock_horizons(_zigzag(leg, 24))
    assert days == expected
    assert "반 파도" in why


def test_stock_horizons_clamped_and_increasing():
    days, _ = picks.stock_horizons(_zigzag(40, 10))
    lo_hi = picks.WAVE_CLAMP
    assert days == (lo_hi[0][1], lo_hi[1][1], lo_hi[2][1])
    assert days[0] < days[1] < days[2]


def test_stock_horizons_falls_back_when_history_short_or_flat():
    assert picks.stock_horizons([100.0] * 30)[0] == picks.HORIZONS_FALLBACK
    assert picks.stock_horizons(_zigzag(10, 2) + [100.0] * 60)[0] == picks.HORIZONS_FALLBACK


def test_record_fixes_horizons_at_record_time(tmp_path, monkeypatch):
    monkeypatch.setattr(picks, "LOG", tmp_path / "picks_log.csv")
    monkeypatch.setattr(picks, "_name2code", lambda: {"가나다": "000001"})
    monkeypatch.setattr(picks.core, "fetch_quotes", lambda codes: ({"000001": {"price": 1000}}, None))
    monkeypatch.setattr(picks, "bench_levels", lambda live=True: ({}, (100.0, 100.0)))
    monkeypatch.setattr(picks, "horizons_for", lambda code, asof: ((7, 14, 42), "근거 한 줄"))
    monkeypatch.setattr(picks.core, "load_transactions", lambda: pd.DataFrame(columns=["종목명", "구분"]))
    monkeypatch.setattr(picks.core, "load_watchlist", lambda: pd.DataFrame({"종목명": ["가나다"], "종목코드": ["000001"]}))
    picks.record("R1", "테스트", [("가나다", "신규", "이유")])
    row = picks.load_log().iloc[0]
    assert row["Bench"] == "Bench"   # 한 번도 안 산 관심종목 — 기록 순간 표시
    assert (int(row["단기일"]), int(row["중기일"]), int(row["장기일"])) == (7, 14, 42)
    assert row["기간근거"] == "근거 한 줄"

    # 그 뒤 종목의 파도가 달라져도 채점은 기록해 둔 기간을 쓴다(다시 재지 않는다)
    def boom(*a, **k):
        raise AssertionError("채점 중에 기간을 다시 재면 안 된다")
    monkeypatch.setattr(picks, "horizons_for", boom)
    assert picks._row_horizons(row) == (7, 14, 42)


def test_row_horizons_falls_back_for_old_rows_without_periods():
    assert picks._row_horizons(pd.Series({"종목명": "옛 기록"})) == picks.HORIZONS_FALLBACK


def test_score_reports_excess_against_both_benchmarks(monkeypatch):
    days = ["2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07"]
    log = pd.DataFrame([{
        "회차": "R1", "날짜": "2026-10-01", "시각": "10:00", "순위": 1, "종목명": "가나다", "종목코드": "000001",
        "추천가": 1000.0, "구분": "신규", "근거": "이유", "벤치K": 100.0, "벤치Q": 100.0, "메모": "",
        "단기일": 2, "중기일": 3, "장기일": 9, "기간근거": "테스트",
    }])
    monkeypatch.setattr(picks, "load_log", lambda: log)
    bench = {d: (100.0, 100.0) for d in days}
    bench["2026-10-05"] = (102.0, 102.0)      # 단기(2거래일 뒤 = 10-05) 벤치 +2%
    monkeypatch.setattr(picks, "bench_levels", lambda live=True: (bench, (101.0, 101.0)))
    monkeypatch.setattr(picks, "_wk", lambda: 1.0)
    monkeypatch.setattr(picks.core, "fetch_quotes", lambda codes: ({"000001": {"price": 1200}}, None))
    monkeypatch.setattr(picks.core, "fetch_daily_price_history",
                        lambda code, s, e: [{"날짜": "2026-10-05", "종가": 1100.0}])
    ph = pd.DataFrame([
        {"종목코드": "U1", "날짜": "2026-10-01", "종가": 100.0}, {"종목코드": "U1", "날짜": "2026-10-05", "종가": 105.0},
        {"종목코드": "U2", "날짜": "2026-10-01", "종가": 100.0}, {"종목코드": "U2", "날짜": "2026-10-05", "종가": 95.0},
    ])
    df = picks.score("R1", data={"ph": ph, "live": {"U1": 110.0, "U2": 100.0}})
    r = df.iloc[0]
    assert r["단기일"] == 2
    assert r["단기%"] == pytest.approx(10.0)
    assert r["단기초과%p"] == pytest.approx(8.0)       # 10 − 벤치 2
    assert r["단기초과관심%p"] == pytest.approx(10.0)  # 10 − 관심평균 0
    assert r["지금초과%p"] == pytest.approx(20.0 - 1.0)
    assert r["지금초과관심%p"] == pytest.approx(20.0 - 5.0)
    assert "장기%" not in df or pd.isna(r.get("장기%"))   # 아직 안 온 체크포인트는 비워 둔다


def _flow(values, start="2026-09-01"):
    dates = pd.bdate_range(start, periods=len(values)).strftime("%Y-%m-%d")
    return pd.DataFrame({"날짜": dates, "외국인보유율": values})


def test_strong_foreign_exit_is_marked_basis_gone_not_wrong():
    base = [10.0 + (0.01 if i % 2 else -0.01) for i in range(10)]
    rise = [10.0 + 0.5 * k for k in range(1, 11)]      # 픽 뒤 15%까지 모음
    drop = [13.6, 12.3, 11.0]                           # 짧게 크게 반납
    fl = _flow(base + rise + drop)
    st = tracking._foreign_state(fl, fl["날짜"].iloc[0], fl["날짜"].iloc[10])
    assert st["외인상태"] == "근거 사라짐"
    assert tracking._card_state("가나다", fl["날짜"].iloc[10], pd.DataFrame(columns=["종목명", "날짜", "구분", "실현손익"]),
                                set(), st["외인상태"]) == "추적·근거 사라짐"


def test_weak_foreign_dip_is_not_basis_gone():
    base = [10.0 + (0.01 if i % 2 else -0.01) for i in range(10)]
    rise = [10.0 + 0.5 * k for k in range(1, 11)]
    fl = _flow(base + rise + [14.9])
    st = tracking._foreign_state(fl, fl["날짜"].iloc[0], fl["날짜"].iloc[10])
    assert st["외인상태"] != "근거 사라짐"


def _score_fixture(monkeypatch):
    days = ["2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07"]
    log = pd.DataFrame([{
        "회차": "R1", "날짜": "2026-10-01", "시각": "10:00", "순위": 1, "종목명": "가나다", "종목코드": "000001",
        "추천가": 1000.0, "구분": "신규", "근거": "이유", "벤치K": 100.0, "벤치Q": 100.0, "메모": "",
        "단기일": 2, "중기일": 3, "장기일": 9, "기간근거": "테스트",
    }])
    monkeypatch.setattr(picks, "load_log", lambda: log)
    bench = {d: (100.0, 100.0) for d in days}
    bench["2026-10-05"] = (102.0, 102.0)
    monkeypatch.setattr(picks, "bench_levels", lambda live=True: (bench, (101.0, 101.0)))
    monkeypatch.setattr(picks, "_wk", lambda: 1.0)
    monkeypatch.setattr(picks.core, "fetch_quotes", lambda codes: ({"000001": {"price": 1200}}, None))
    monkeypatch.setattr(picks.core, "fetch_daily_price_history",
                        lambda code, s, e: [{"날짜": "2026-10-05", "종가": 1100.0}])
    ph = pd.DataFrame([{"종목코드": "U1", "날짜": "2026-10-01", "종가": 100.0}, {"종목코드": "U1", "날짜": "2026-10-05", "종가": 105.0}])
    return {"ph": ph, "live": {"U1": 110.0}}


def test_side_by_side_view_adds_columns_without_changing_recorded_scores(monkeypatch):
    """나란히 보기(2026-10-20 대표): 고정 길이 칸만 더하고, 기존 채점 칸 값은 그대로 — 판정 칸은 없다."""
    data = _score_fixture(monkeypatch)
    monkeypatch.setattr(picks, "VIEW_DAYS", ())
    before = picks.score("R1", data=data)
    monkeypatch.setattr(picks, "VIEW_DAYS", (2, 30))
    after = picks.score("R1", data=data)
    for c in before.columns:
        if c == "경과일":
            continue
        a, b = before.iloc[0][c], after.iloc[0][c]
        assert (pd.isna(a) and pd.isna(b)) or a == b, c
    assert after.iloc[0]["+2일초과%p"] == pytest.approx(8.0)   # 단기(2일)와 같은 날 → 같은 값
    view = picks.side_by_side(after)
    row = view.iloc[0]
    assert row["+30일"] == picks.NOT_YET
    assert row["장기"].startswith(picks.NOT_YET) and "(9일)" in row["장기"]
    assert list(view.columns)[-1] == "지금(체크포인트 아님)"
    assert not any("판정" in c or "이긴" in c for c in view.columns)


def test_stress_bench_daily_uses_stored_ingest_day_not_today_wk():
    # 폭락일 당일 = live_check·앱과 같은 날짜 고정(ingest로 남긴 날은 저장값) — 10-22 밤
    import stress_days as sd
    closes = {"2026-10-23": (100.0, 100.0), "2026-10-24": (98.0, 90.0), "2026-10-27": (99.0, 99.0)}
    bh = pd.DataFrame({"날짜": ["2026-10-24"], "wk": [0.9], "벤치_당일": [-0.028], "벤치ex_당일": [-0.028],
                       "코스피ex_당일": [-0.02], "SH_당일": [0.0], "출처": ["ingest"], "기록시각": [""]})
    bd = sd.bench_daily(closes, 0.5, bench_hist=bh)
    assert bd["2026-10-24"] == pytest.approx(-2.8)                         # 저장값(그날 wk 0.9), 지금 wk 0.5 선형(−6%) 아님
    assert bd["2026-10-27"] == pytest.approx((0.5 * (99 / 98 - 1) + 0.5 * (99 / 90 - 1)) * 100)   # 파일에 없는 날만 지금 wk


def test_bench_ret_fn_chains_stored_days_after_seed():
    # tracking·stress_days·picks가 같이 쓰는 구간 함수 — seed 뒤는 bench_history 하루 등락 복리
    import picks
    closes = {"2026-10-22": (100.0, 100.0), "2026-10-23": (101.0, 103.0), "2026-10-24": (102.0, 103.0)}
    bh = pd.DataFrame({"날짜": ["2026-10-22", "2026-10-23", "2026-10-24"], "wk": [0.7, 0.6, 0.8],
                       "벤치_당일": [0.0, 0.0, 0.0], "벤치ex_당일": [0.0, 0.015, 0.01],
                       "코스피ex_당일": [0.0, 0.0, 0.0], "SH_당일": [0.0, 0.0, 0.0],
                       "출처": ["seed", "ingest", "ingest"], "기록시각": ["", "", ""]})
    bret = picks.bench_ret_fn(closes, None, 0.5, bench_hist=bh, today="2026-10-27")
    assert bret(closes["2026-10-22"], closes["2026-10-24"], "2026-10-22", "2026-10-24") == pytest.approx((1.015 * 1.01 - 1) * 100)
