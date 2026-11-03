"""find_city.py — 묶음 가르기(빌딩 이름이 바뀌면 묶음이 사무실로 섞이지 않게)."""
import importlib.util
from pathlib import Path

_P = Path(__file__).resolve().parent.parent / "뇌_초안" / "뇌관리" / "find_city.py"
_spec = importlib.util.spec_from_file_location("find_city", _P)
fc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fc)


def test_group_of_puts_il_building_in_its_own_group():
    # 2026-11-03: 빌딩 이름 "길" → "일" 뒤 일 장이 "사무실" 묶음에 섞이던 것
    assert fc.group_of(Path("일") / "추천.md") == "일"
    assert fc.group_of(Path("지침서.md")) == "지침서·뿌리"
    assert fc.group_of(Path("기록관") / "대표_원문.md") == "기록관"
    assert fc.group_of(Path("데이터센터") / "세팅.md") == "사무실"


def test_every_named_group_folder_exists():
    city = _P.parent.parent
    for _, folder in fc.GROUPS:
        if folder not in (None, "*"):
            assert (city / folder).is_dir(), folder
