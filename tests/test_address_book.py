"""brain_log.py — 동사무소 최소판: 주소록 ↔ 실제 파일, 본문 속 끊긴 경로(U-027). 대표 2026-10-27(원-2026-10-27-10)."""
import brain_log as bl


def test_address_book_matches_city():
    """주소록(뇌관리/주소록.csv)이 지금 도시의 `주민:` 줄과 같고, 번호·이름이 겹치지 않고, 경로가 다 있다."""
    assert bl.address_problems() == []


def test_no_broken_text_paths_in_city():
    """본문 글자 속 `빌딩/사무실`이 이사 뒤 끊기지 않았는지 — 백틱·.md 없는 경로도(U-027)."""
    assert bl.text_path_problems() == []


def _city(tmp_path):
    root = tmp_path / "뇌_초안"
    (root / "데이터센터").mkdir(parents=True)
    (root / "뇌관리").mkdir()
    (root / "HISTORY").mkdir()
    (root / "데이터센터" / "세팅.md").write_text("# 세팅\n\n## 꼬리표\n주민: C-0001 세팅\n", encoding="utf-8")
    (root / "대기.md").write_text("# 대기\n주민: C-0002 대기\n", encoding="utf-8")
    (root / "HISTORY" / "2026-10-27.md").write_text("주민: C-0001 세팅\n", encoding="utf-8")   # HISTORY는 안 셈
    return root


def test_address_book_is_scraped_from_resident_lines(tmp_path):
    root = _city(tmp_path)
    assert bl.residents(root) == [("C-0001", "세팅", "데이터센터/세팅.md"), ("C-0002", "대기", "대기.md")]
    book = root / "뇌관리" / "주소록.csv"
    assert any("주소록 없음" in x for x in bl.address_problems(root, book))
    book.write_text(bl.address_book_text(root), encoding="utf-8")
    assert bl.address_problems(root, book) == []


def test_address_problems_flag_move_and_duplicates(tmp_path):
    root = _city(tmp_path)
    book = root / "뇌관리" / "주소록.csv"
    book.write_text(bl.address_book_text(root), encoding="utf-8")
    # 이사: 파일을 옮기면 주소록이 낡았다고 알린다(다시 만들면 풀림)
    (root / "데이터센터" / "세팅.md").rename(root / "뇌관리" / "세팅.md")
    probs = bl.address_problems(root, book)
    assert any("주소록 경로가 없음" in x for x in probs) and any("다름" in x for x in probs)
    book.write_text(bl.address_book_text(root), encoding="utf-8")
    assert bl.address_problems(root, book) == []
    # 번호 겹침
    (root / "뇌관리" / "다른.md").write_text("# 다른\n주민: C-0001 다른\n", encoding="utf-8")
    assert any("번호 겹침" in x for x in bl.address_problems(root, book))


def test_text_path_check_flags_moved_office_but_not_history_or_particles(tmp_path):
    root = _city(tmp_path)
    (root / "데이터센터" / "꺼내는_창구.md").write_text(
        "# 창구\n"
        "- 숫자는 데이터센터/세팅 한 곳\n"                     # 산 경로
        "- 데이터센터/세팅에서 문턱을 본다\n"                   # 조사가 붙어도 산 것
        "- 옛 이름: 데이터센터/현행값_집\n"                     # 옛 이름 줄은 뺌
        "- 숫자 집은 데이터센터/현행값_집\n"                    # 끊김
        "\n## 이력\n- 데이터센터/옛_사무실에서 옮김\n",         # 이력 칸은 뺌
        encoding="utf-8")
    assert bl.text_path_problems(root) == [("데이터센터/꺼내는_창구.md", 5, "데이터센터/현행값_집")]
