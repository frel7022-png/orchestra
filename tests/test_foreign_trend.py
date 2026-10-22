"""외인 추이(foreign_trend.py) — Link 리포트 칸 (대표 확정 2026-10-22)."""
import pandas as pd

import foreign_trend as ft


def _fh(series: dict, names: dict | None = None, start="2026-07-01") -> pd.DataFrame:
    """series: {종목코드: [외인 비중 …]} — 날짜는 거래일처럼 하루씩."""
    n = len(next(iter(series.values())))
    dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range(start, periods=n)]
    rows = []
    for code, vals in series.items():
        for d, v in zip(dates, vals):
            if v is not None:
                rows.append({"종목코드": code, "종목명": (names or {}).get(code, code), "날짜": d, "외국인보유율": v})
    return pd.DataFrame(rows)


def test_window_changes_baseline_is_mean_and_head_has_mean_and_median():
    flat = [10.0] * 30
    fh = _fh({"A": flat[:-1] + [11.0],      # 5일 +10%
              "B": flat,                    # 0
              "C": flat[:-1] + [9.7]})      # −3%
    df, head = ft.window_changes(fh, None, 5)
    assert head["종목수"] == 3
    assert abs(head["평균rel"] - (10 + 0 - 3) / 3) < 1e-9
    assert abs(head["중앙rel"] - 0) < 1e-9
    a = df.set_index("종목코드").loc["A"]
    assert abs(a["dev_rel"] - (10 - head["평균rel"])) < 1e-9      # 기준선 = 평균
    assert head["오름"] == 1 and head["내림"] == 1 and head["그대로"] == 1


def test_cumulative_window_starts_at_price_tracking_origin():
    fh = _fh({"A": [5.0] * 5 + [10.0] * 20 + [12.0]})
    ph = pd.DataFrame({"종목코드": ["A"], "날짜": [sorted(fh["날짜"])[5]], "종가": [1.0]})
    df, _ = ft.window_changes(fh, ph, None)
    r = df.iloc[0]
    assert r["시작"] == 10.0 and r["끝"] == 12.0          # 가격추적 시작일 전 5.0은 안 씀


def test_zero_or_missing_foreign_weight_is_excluded():
    fh = _fh({"A": [0.0] * 10 + [1.0], "B": [10.0] * 11, "C": [None] * 5 + [10.0] * 5 + [None]})
    df, head = ft.window_changes(fh, None, 5)
    assert set(df["종목코드"]) == {"B"}                   # A 시작 0, C 최신일 없음


def test_basic_layer_ranks_by_raw_change_so_small_bases_can_top():
    fh = _fh({"small": [1.0] * 10 + [1.3],                 # +30% · +0.3%p
              "big": [30.0] * 10 + [33.0],                 # +10% · +3%p
              "flat": [10.0] * 11})
    df, _ = ft.window_changes(fh, None, 5)
    up, _ = ft.basic_layer(df)
    assert list(up["종목코드"])[0] == "small"


def test_deep_layer_floor_drops_near_zero_and_ranks_by_pp():
    series = {"big": [30.0] * 10 + [33.0], "small": [1.0] * 10 + [1.3], "zero": [0.45] * 11,
              "mid1": [10.0] * 10 + [10.5], "mid2": [10.0] * 10 + [9.4]}
    fh = _fh(series)
    df, head = ft.window_changes(fh, None, 5)
    up, down = ft.deep_layer(df, head["중앙절대pp"])
    assert list(up["종목코드"])[0] == "big"                 # 영향력 = %p
    assert "zero" not in set(up.loc[~up["가까운순"], "종목코드"]) | set(down.loc[~down["가까운순"], "종목코드"])
    # 바닥 밑은 "가까운 순"으로만 채워진다
    assert up["가까운순"].tolist() == sorted(up["가까운순"].tolist())


def test_sigma_is_a_flag_not_a_filter():
    noisy = [10.0 + (0.8 if i % 2 else -0.8) for i in range(30)] + [12.0]
    calm = [10.0 + (0.02 if i % 2 else -0.02) for i in range(30)] + [10.6]
    fh = _fh({"noisy": noisy, "calm": calm, **{f"flat{i}": [10.0] * 31 for i in range(5)}})
    df, head = ft.window_changes(fh, None, 5)
    up, _ = ft.deep_layer(df, head["중앙절대pp"])
    assert "noisy" in set(up["종목코드"])                   # σ에 걸려도 빠지지 않음
    d = df.set_index("종목코드")
    assert not d.loc["noisy", "평소와다름"] and d.loc["calm", "평소와다름"]


def test_extreme_exits_one_day_and_five_day():
    fh = _fh({"oneday": [10.0] * 10 + [8.9],                          # 하루 −11%
              "fiveday": [10.0] * 6 + [9.8, 9.6, 9.4, 9.2, 8.95],     # 5일 −10.5%, 하루는 작음
              "ok": [10.0] * 10 + [9.5]})
    ex = ft.extreme_exits(fh)
    kinds = set(zip(ex["종목코드"], ex["종류"]))
    assert ("oneday", "하루") in kinds
    assert ("fiveday", f"{ft.WATCH_DAYS}거래일") in kinds and ("fiveday", "하루") not in kinds
    assert "ok" not in set(ex["종목코드"])


def test_window_starts_for_vertical_lines():
    fh = _fh({"A": [10.0] * 70})
    starts = ft.window_starts(fh)
    dates = sorted(fh["날짜"].unique())
    assert starts == [dates[-1 - 5], dates[-1 - 20], dates[-1 - 60]]


def test_flat_history_gives_no_sigma_flag():
    """평소 거의 안 움직여 σ_f = 0인 종목(10-22 대한화섬 0.45→0.45)은 σ배가 비고 "평소와 다름"이 안 붙는다."""
    fh = _fh({"still": [0.45] * 31, "A": [10.0] * 30 + [11.0], "B": [10.0] * 30 + [9.0]})
    df, _ = ft.window_changes(fh, None, 5)
    s = df.set_index("종목코드").loc["still"]
    assert pd.isna(s["z"]) and not s["평소와다름"]


def test_link_report_section_renders_and_notes_unmeasurable_window():
    """Link 리포트 칸 — 창이 모자라면(60거래일) 빼지 않고 "아직 못 잼" 한 줄, 숫자 칸·극단 이탈 칸이 나온다."""
    import daily_link_report as dlr
    fh = _fh({"A": [10.0] * 25 + [11.5], "B": [20.0] * 25 + [18.0], "C": [5.0] * 26})
    ph = fh.rename(columns={"외국인보유율": "종가"})[["종목코드", "종목명", "날짜", "종가"]]
    html_ = dlr.foreign_trend_section(sorted(fh["날짜"])[-1], ph, fh, {}, {"A"}, set())
    assert "외인 추이 — 관심종목 전체 평균 대비" in html_
    assert "60거래일</b> — 아직 못 잼" in html_
    assert "5거래일</b>" in html_ and "누적</b>" in html_
    assert "극단 이탈 — 트래킹" in html_ and "B" in html_   # B 20 → 18 = −10%
