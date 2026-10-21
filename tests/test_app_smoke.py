"""app.py 전체가 실제로 실행됐을 때 안 죽는지 확인하는 스모크 테스트.

Streamlit 공식 테스트 도구(streamlit.testing.v1.AppTest)로 스크립트를 진짜 실행해서
확인한다 — 화면 클릭까지는 못 하지만(실제 브라우저가 아니므로 CSS/픽셀 정렬은 검증
못 함), 버튼 클릭 시 세션 상태가 의도대로 바뀌고 예외 없이 다시 렌더링되는지는 검증
가능하다. 실행에는 로컬 .streamlit/secrets.toml(빈 파일이어도 됨, CLAUDE.md §6-1-1)이
있어야 하고, 실제 transactions.csv/portfolio_data.csv 데이터를 **임시 폴더 사본으로** 사용한다.

2026-10-21: 앱은 열 때 자동 시세 새로고침을 돌려 asset/sector/index/bigcap_history·portfolio_data·
stock_market_cache·report/capture_anomalies.csv에 오늘 장중 값을 쓴다 — 테스트가 실데이터를 고치고 있었다
(그날만 네 번 되돌림, 메모리 pytest_mutates_live_data_files). 되돌리기를 한 번 빠뜨리면 9/11 사고(장중 값이
히스토리에 굳음)가 테스트에서 난다. 그래서 portfolio_core의 모든 데이터 경로(HERE 아래 Path 상수)를
tmp_path 사본으로 돌려 놓고 실행한다(앱·ui 모듈은 파일을 직접 안 열고 전부 이 상수를 거친다).
"""

import shutil
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import portfolio_core as core

# AppTest.from_file()의 상대경로는 "이 테스트 파일" 기준으로 풀린다(cwd 기준이 아님) —
# 이 파일은 tests/ 안에 있으므로 그냥 "app.py"라고 쓰면 tests/app.py를 찾다가
# FileNotFoundError가 난다(CI에서 실제로 겪음, 2026-08-24). 레포 루트의 app.py를
# 명시적으로 가리킨다.
APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")


@pytest.fixture(autouse=True)
def _data_in_tmp_copy(tmp_path, monkeypatch):
    """portfolio_core의 데이터 파일·폴더 상수를 전부 tmp_path 사본으로 바꾼다 — 실데이터 무접촉."""
    root = core.HERE
    for name, val in list(vars(core).items()):
        if not isinstance(val, Path) or val == root or root not in val.parents:
            continue
        dst = tmp_path / val.relative_to(root)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if val.is_dir():
            shutil.copytree(val, dst, dirs_exist_ok=True)
        elif val.is_file():
            shutil.copy2(val, dst)
        monkeypatch.setattr(core, name, dst)


def test_app_loads_without_exception():
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.run()
    assert not at.exception


def test_watering_button_opens_and_closes_holding_detail():
    """보유종목 카드의 WATERING 칩 → 물타기 그래프 토글이 예외 없이 열리고 닫히는지."""
    at = AppTest.from_file(APP_PATH, default_timeout=30)
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
