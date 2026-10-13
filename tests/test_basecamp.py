"""Basecamp(§6-35) 점수 — 빠지다 평지에서 거래량이 꾸준히 터지는 모양이 위로 오는지."""
import pandas as pd

import basecamp


def _days(n):
    return [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2026-08-03", periods=n)]


def _stock(code, name, closes, vols):
    d = _days(len(closes))
    ph = pd.DataFrame({"종목코드": code, "종목명": name, "날짜": d, "종가": closes})
    fh = pd.DataFrame({"종목코드": code, "날짜": d, "거래량": vols})
    return ph, fh


def _decline_then_flat(base_vol_mult):
    # 10일 평탄(100) → 10일 동안 100→75 하락 → 7일 평지(75 근처)
    closes = [100] * 10 + [100 - 2.5 * i for i in range(1, 11)] + [75, 75.5, 74.8, 75.2, 75, 74.9, 75.1]
    vols = [1000] * 10 + [1500] * 10 + [1000 * base_vol_mult] * 7
    return closes, vols


def test_heavy_volume_base_ranks_above_quiet_base():
    c1, v1 = _decline_then_flat(3)
    c2, v2 = _decline_then_flat(1)
    p1, f1 = _stock("000001", "매집", c1, v1)
    p2, f2 = _stock("000002", "조용", c2, v2)
    out = basecamp.scan(pd.concat([p1, p2]), pd.concat([f1, f2]), top_n=None)
    assert [x["종목명"] for x in out] == ["매집", "조용"]
    top = out[0]
    assert abs(top["거래량배수"] - 3.0) < 0.01          # 기준 = 하락 시작 전(1000), 하락 중 1500이 안 섞임
    assert top["대량일수"] >= 7          # 평지 7일은 전부 3배(하락 마지막 날이 같은 가격대면 평지에 붙을 수 있음)
    assert -27 < top["하락률"] < -23


def test_spike_peak_is_not_a_decline():
    # 평탄 50 → 하루 만에 +30% 급등 → 되돌림 후 평지: 뉴스 급등 고점이라 후보에서 빠져야 함
    closes = [50] * 12 + [65, 60, 57, 55, 53] + [52, 52.3, 51.8, 52.1, 52, 51.9, 52.2]
    vols = [1000] * 12 + [90000, 20000, 9000, 6000, 5000] + [4000] * 7
    ph, fh = _stock("000003", "급등후", closes, vols)
    assert basecamp.scan(ph, fh, top_n=None) == []


def test_status_uses_live_price_against_base_range():
    c, v = _decline_then_flat(3)
    ph, fh = _stock("000001", "매집", c, v)
    assert basecamp.scan(ph, fh, live_quotes={"000001": 80.0})[0]["상태"] == "돌파"
    assert basecamp.scan(ph, fh, live_quotes={"000001": 75.0})[0]["상태"] == "다지는 중"
    assert basecamp.scan(ph, fh, live_quotes={"000001": 70.0})[0]["상태"] == "이탈"


def test_base_days_accumulate_while_price_holds_the_band():
    c, v = _decline_then_flat(3)
    ph, fh = _stock("000001", "매집", c, v)
    n0 = basecamp.scan(ph, fh)[0]["평지일수"]
    ph2, fh2 = _stock("000001", "매집", c + [75.0, 74.9, 75.2], v + [3000] * 3)   # 3일 더 버팀
    assert basecamp.scan(ph2, fh2)[0]["평지일수"] == n0 + 3


def test_absorber_marks_foreign_when_foreigners_net_buy_in_base():
    c, v = _decline_then_flat(3)
    ph, fh = _stock("000001", "매집", c, v)
    n = len(c)
    fh["외국인순매수"] = [0] * (n - 7) + [500] * 7
    fh["기관순매수"] = [0] * n
    fh["외국인보유율"] = [10.0] * (n - 7) + [10.0 + 0.1 * i for i in range(1, 8)]
    top = basecamp.scan(ph, fh)[0]
    assert top["받는손"] == "외인" and top["외인pp"] > 0
    fh["외국인순매수"] = [0] * (n - 7) + [-500] * 7
    fh["외국인보유율"] = [10.0] * n
    fh["기관순매수"] = [0] * (n - 7) + [400] * 7
    assert basecamp.scan(ph, fh)[0]["받는손"] == "기관"


def test_today_row_is_excluded_from_base():
    c, v = _decline_then_flat(3)
    ph, fh = _stock("000001", "매집", c + [60.0], v + [99999])   # 오늘(장중) 폭락 행
    today = ph["날짜"].iloc[-1]
    out = basecamp.scan(ph, fh, today=today)
    assert out and out[0]["평지끝"] < today
