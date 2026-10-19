"""추천 사례집(pick_cases.py) — 특징은 추천일까지 데이터만, 비슷한 사례는 목록에서 두 잣대 따로."""
import pandas as pd

import pick_cases as pc


def _closes(n, start=100.0, step=1.0):
    days = pd.bdate_range("2026-06-01", periods=n).strftime("%Y-%m-%d")
    return [(d, start + step * i) for i, d in enumerate(days)]


def test_features_ignore_data_after_pick_date():
    c = _closes(80)
    d0 = c[69][0]
    flow = pd.DataFrame({"날짜": [d for d, _ in c], "외국인보유율": [10.0 + 0.1 * i for i in range(80)],
                         "거래량": 1000, "외국인순매수": 100, "기관순매수": -50})
    f0, s0 = pc.features(c, flow, d0, shares=1e6)
    f1, s1 = pc.features(c[:70], flow[flow["날짜"] <= d0], d0, shares=1e6)
    assert f0 == f1 and s0 == s1
    assert f0["외인율"] == 10.0 + 0.1 * 69
    assert round(f0["dF20"], 6) == 2.0
    assert round(f0["외인몫10"], 6) == 10.0 and round(f0["기관몫10"], 6) == -5.0
    assert f0["칸20"] == "진행형"
    assert s0[-1] == 1.0 and len(s0) == pc.SHAPE_DAYS


def test_rank_similar_keeps_shape_and_features_separate():
    up = [i / 59 for i in range(60)]
    down = [1 - i / 59 for i in range(60)]
    shapes = pd.DataFrame([{"사례": "A", **{f"s{i}": v + 1 for i, v in enumerate(up)}},
                           {"사례": "B", **{f"s{i}": v + 1 for i, v in enumerate(down)}}])
    idx = pd.DataFrame([{"사례": "A", "종목명": "가", "날짜": "2026-10-01", "r20": -20.0, "dF20": 1.0},
                        {"사례": "B", "종목명": "나", "날짜": "2026-10-02", "r20": 5.0, "dF20": -1.0}])
    by_shape, by_feat = pc.rank_similar({"r20": 6.0, "dF20": -0.9}, [v + 1 for v in up], idx, shapes)
    assert by_shape.index[0] == "A"          # 모양은 A와 같고
    assert by_feat.index[0] == "B"           # 특징은 B와 가깝다 — 섞지 않고 둘 다 보여 준다
