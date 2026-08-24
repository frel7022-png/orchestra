"""app.py 전체가 실제로 실행됐을 때 안 죽는지 확인하는 스모크 테스트.

Streamlit 공식 테스트 도구(streamlit.testing.v1.AppTest)로 스크립트를 진짜 실행해서
확인한다 — 화면 클릭까지는 못 하지만(실제 브라우저가 아니므로 CSS/픽셀 정렬은 검증
못 함), 버튼 클릭 시 세션 상태가 의도대로 바뀌고 예외 없이 다시 렌더링되는지는 검증
가능하다. 실행에는 로컬 .streamlit/secrets.toml(빈 파일이어도 됨, CLAUDE.md §6-1-1)이
있어야 하고, 실제 transactions.csv/portfolio_data.csv 데이터를 그대로 사용한다.
"""

from streamlit.testing.v1 import AppTest


def test_app_loads_without_exception():
    at = AppTest.from_file("app.py", default_timeout=30)
    at.run()
    assert not at.exception


def test_watering_button_opens_and_closes_holding_detail():
    """보유종목 카드의 WATERING 칩 → 물타기 그래프 토글이 예외 없이 열리고 닫히는지."""
    at = AppTest.from_file("app.py", default_timeout=30)
    at.run()

    watering_buttons = [b for b in at.button if b.key and b.key.startswith("watering_")]
    if not watering_buttons:
        return  # 보유종목이 하나도 없는 상태라면 검증할 대상이 없음 — 스킵

    key = watering_buttons[0].key

    [b for b in at.button if b.key == key][0].click().run()
    assert not at.exception
    assert at.session_state["holding_detail_open"] is not None

    [b for b in at.button if b.key == key][0].click().run()
    assert not at.exception
    assert at.session_state["holding_detail_open"] is None
