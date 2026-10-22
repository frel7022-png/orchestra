"""관측소 바다(sea.py) — 바다 구간·시장 외인 합·관심종목 가운데 값 대비 상대 외인."""
import pytest
import pandas as pd

import sea


def _fx(vals):
    return pd.DataFrame({"날짜": [f"2026-08-{i + 1:02d}" for i in range(len(vals))], "원달러": vals})


def test_fx_leg_starts_at_last_turn_and_big_trend_at_extreme():
    up = [1400 + i * 10 for i in range(10)]          # 1400 → 1490 (꼭짓점)
    down = [1490 - i * 12 for i in range(1, 12)]      # 1478 → 1358
    fx = _fx(up + down)
    leg = sea.fx_leg(fx)
    assert leg["방향"] == -1
    assert leg["시작"] == "2026-08-10" and leg["시작값"] == 1490
    assert leg["큰흐름"]["시작"] == "2026-08-10" and not leg["큰흐름"]["자료끝"]


def test_market_foreign_excludes_start_day():
    mf = pd.DataFrame({"시장": ["KOSPI"] * 3, "날짜": ["2026-08-01", "2026-08-02", "2026-08-03"],
                       "외국인순매수": [-100, -50, 20]})
    assert sea.market_foreign(mf, "2026-08-01", "2026-08-03") == {"KOSPI": -30.0}


def test_relative_foreign_subtracts_universe_median_and_falls_back_to_first_row():
    rows = []
    for code, name, vals in [("A", "가", [10, 9, 8]), ("B", "나", [5, 5, 5]), ("C", "다", [20, 19.5, 19])]:
        for d, v in zip(["2026-08-01", "2026-08-02", "2026-08-03"], vals):
            rows.append({"종목코드": code, "종목명": name, "날짜": d, "외국인보유율": v})
    rows.append({"종목코드": "D", "종목명": "라", "날짜": "2026-08-02", "외국인보유율": 3.0})   # 커버리지가 늦게 시작
    rows.append({"종목코드": "D", "종목명": "라", "날짜": "2026-08-03", "외국인보유율": 4.0})
    df, info = sea.relative_foreign(pd.DataFrame(rows), "2026-08-01")
    by = df.set_index("종목명")
    assert info["외인중앙"] == -0.5                          # dF: 가 −2, 나 0, 다 −1, 라 +1 → 가운데 −0.5
    assert by.loc["나", "상대외인"] == 0.5 and bool(by.loc["나", "버팀"])   # 바다가 빠지는데 그대로 = 버팀
    assert by.loc["라", "기준일"] == "2026-08-02" and by.loc["라", "dF"] == 1.0
    assert not bool(by.loc["가", "버팀"])


def test_index_moves_blend_uses_date_fixed_bench_history_not_linear_today_wk():
    # 혼합지수는 live_check·앱과 같은 날짜 고정 정의(bench_cum_map) — 지난 날은 bench_history 저장값, 오늘만 지금 wk(10-22 밤)
    idx = pd.DataFrame({"날짜": ["2026-10-01", "2026-10-02", "2026-10-05"],
                        "KOSPI": [100.0, 110.0, 110.0], "KOSDAQ": [100.0, 90.0, 99.0]})
    big = pd.DataFrame(columns=["날짜", "삼성전자", "삼성전자우", "SK하이닉스"])
    bh = pd.DataFrame({"날짜": ["2026-10-02"], "벤치_당일": [0.02], "벤치ex_당일": [0.02]})   # 그날 wk로 저장된 하루 +2%
    m = sea.index_moves(idx, big, 0.5, "2026-10-01", bench_hist=bh)
    # 10-02 = 저장값 +2% · 10-05 = 지금 wk 0.5로 코스피 0%·코스닥 +10% → +5% → (1.02 × 1.05 − 1)
    assert m["혼합(일반)"] == pytest.approx((1.02 * 1.05 - 1) * 100)
    assert m["혼합(일반)"] != pytest.approx(0.5 * 10 + 0.5 * -1)          # 옛 선형(지금 wk로 구간 전체)과 다르다
    assert m["코스피"] == pytest.approx(10.0) and m["코스닥"] == pytest.approx(-1.0)
    # 시작일 뒤 구간만: 10-02 기준이면 10-05 하루(+5%)
    assert sea.index_moves(idx, big, 0.5, "2026-10-02", bench_hist=bh)["혼합(일반)"] == pytest.approx(5.0)


def test_tide_labels():
    assert sea.tide({"방향": -1}, {"KOSPI": -10.0}).startswith("역풍")
    assert sea.tide({"방향": 1}, {"KOSPI": 10.0}).startswith("순풍")
    assert sea.tide({"방향": -1}, {"KOSPI": 10.0}).startswith("엇갈림")


def test_universe_center_gives_both_mean_and_median_and_skips_nan():
    import portfolio_core as core
    c = core.universe_center([1.0, 2.0, 9.0, float("nan"), None])
    assert c == {"평균": 4.0, "가운데": 2.0, "n": 3}
    assert core.universe_center([])["가운데"] is None


def test_eye_card_lays_out_facts_per_window_without_verdict():
    """눈 한 장(2026-10-20): 길이마다 주가·바다·외인 사실만, 판정·점수 칸 없음, 짧은 자료는 전체 길이로."""
    import eye_card
    days = [f"2026-09-{d:02d}" for d in range(1, 31)][:25]
    ph = pd.DataFrame([{"종목코드": c, "종목명": n, "날짜": d, "종가": 100.0 + (i if c == "A" else 0)}
                       for c, n in (("A", "가"), ("B", "나")) for i, d in enumerate(days)])
    fh = pd.DataFrame([{"종목코드": c, "종목명": n, "날짜": d, "외국인보유율": (10.0 + i * 0.1) if c == "A" else 5.0}
                       for c, n in (("A", "가"), ("B", "나")) for i, d in enumerate(days)])
    mf = pd.DataFrame([{"시장": "KOSPI", "날짜": d, "외국인순매수": -10} for d in days])
    idx = pd.DataFrame({"날짜": days, "KOSPI": [100.0] * 25, "KOSDAQ": [100.0] * 25})
    c = eye_card.card("가", data={"ph": ph, "fh": fh, "mf": mf, "live": {}},
                      snap={"idx": idx, "bigdf": pd.DataFrame(), "wk": 1.0})
    labels = [r["기간"] for r in c["rows"]]
    assert labels == ["10일", "전체(24일)"]                       # 30·60일은 자료가 모자라 빠지고 전체가 붙는다
    r10 = c["rows"][0]
    assert r10["주가%"] == pytest.approx((124 / 114 - 1) * 100)
    assert r10["외인pp"] == pytest.approx(1.0)
    assert not any(k in r10 for k in ("판정", "점수", "이긴"))
