import morning_report as mr


def test_market_regime_follows_daepyo_lines():
    # 대표 10-23: +1%↑ 좋음(−3%도) · ±1% 보통(−5%) · −1~−2% 안 좋음(−1.5~−2% 빈칸 포함, 6-A) · −2%↓ 매우 안 좋음
    assert mr.market_regime(1.2)[:2] == ("좋음", -3.0)
    assert mr.market_regime(0.3)[:2] == ("보통", -5.0)
    assert mr.market_regime(-1.0)[:2] == ("안 좋음", -5.0)
    assert mr.market_regime(-1.7)[:2] == ("안 좋음", -5.0)
    assert mr.market_regime(-2.0)[:2] == ("매우 안 좋음", -5.0)
    assert mr.market_regime(None)[1] == -5.0
