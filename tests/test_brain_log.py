"""brain_log.py — 셀 머리 길이, 저장고 표 생성."""
import brain_log as bl


def test_head_len_counts_lines_before_tail_block():
    text = "# 셀\n\n트리거: a\n한 문장: b\n\n## 꼬리표\n저장고: x\n"
    assert bl.head_len(text) == 4
    assert bl.head_len("# 꼬리표 없음\n") is None


def test_storage_table_reads_each_cells_storage_line(tmp_path):
    d = tmp_path / "수급"
    d.mkdir()
    (d / "외인_판단.md").write_text("# 외인\n\n## 꼬리표\n저장고: `a.csv`(`a.py`)\n", encoding="utf-8")
    (d / "빈칸.md").write_text("# 빈\n\n## 꼬리표\n종류: 관점\n", encoding="utf-8")
    t = bl.storage_table(sorted(d.glob("*.md")))
    assert "| `수급/외인_판단` | `a.csv`(`a.py`) |" in t
    assert "빈칸" not in t


def test_links_no_broken_paths_in_city():
    """뇌_초안 안 경로가 끊기지 않았는지 — 빌딩 이사·이름 바꾸기 뒤 반드시 0 (2026-10-20 셀회의)."""
    out, inc, broken = bl.links()
    assert broken == []
    assert all(k in inc for k in out)


def test_code_links_cell_to_code_and_back():
    """셀→코드 문이 살아 있는지 — 줄 번호 문 없음, @뇌 이름표는 코드에 정확히 하나, 심볼은 그 모듈에 있음,
    코드 이름표는 어느 셀이든 가리킴(2026-10-21 위크·새러데이 합의). import 없이 파일만 읽어 실데이터 무접촉."""
    assert bl.code_link_problems() == []


def test_code_link_checker_flags_line_refs_and_missing_tags(tmp_path, monkeypatch):
    brain = tmp_path / "뇌_초안"
    (brain / "일과").mkdir(parents=True)
    (brain / "HISTORY").mkdir()
    (tmp_path / "mod.py").write_text('"""doc"""\ndef f():\n    # @뇌 갈래\n    return 1\nX = 2\n', encoding="utf-8")
    (brain / "일과" / "a.md").write_text("`mod.f` `mod.X` `mod.__doc__` `@뇌 갈래` `mod.py:3` `new1/mod.py:2` `../mod.py:4` `mod.없음` `@뇌 없는표`\n",
                                       encoding="utf-8")
    (brain / "HISTORY" / "h.md").write_text("`mod.py:9`\n", encoding="utf-8")   # 기록 칸은 검사 밖
    monkeypatch.setattr(bl, "HERE", tmp_path)
    monkeypatch.setattr(bl, "BRAIN", brain)
    probs = bl.code_link_problems()
    assert any("줄 번호 문" in p and "mod.py:3" in p for p in probs)
    assert any("심볼 없음" in p and "mod.없음" in p for p in probs)
    assert any("이름표 없음" in p and "없는표" in p for p in probs)
    assert any("new1/mod.py:2" in p for p in probs)   # new1/ 접두도 같은 레포, ../는 레포 밖
    assert len(probs) == 4
    (brain / "일과" / "a.md").write_text("`mod.f`\n", encoding="utf-8")
    assert any("고아 이름표" in p for p in bl.code_link_problems())


def test_oldcheck_mark_then_changed_lines_only(tmp_path):
    """oldcheck: 지문 뒤 새로 생긴 줄만 잡고, 〔새뇌 …〕 표시만 바뀐 줄은 같은 줄로 본다(2026-10-22 위크 회의)."""
    old = tmp_path / "claude.md"
    old.write_text("- 첫 줄\n- 둘째 줄\n", encoding="utf-8")
    st = tmp_path / "state.json"
    bl.old_mark([old], st)
    old.write_text("- 첫 줄 〔새뇌 대기 2026-10-22〕\n- 둘째 줄\n- **새 규칙** `foo_bar_baz`\n- `있는_이름` 쓰기\n", encoding="utf-8")
    ch = bl.old_changed([old], st, city="도시엔 `있는_이름`만 있다")
    assert [(c[1], c[3]) for c in ch] == [(3, False), (4, True)]


def test_oldcheck_open_waits_skip_template_and_closed_rows(tmp_path):
    old = tmp_path / "m.md"
    old.write_text("규칙: `〔새뇌 대기 YYYY-MM-DD〕`를 단다\n바뀐 줄 〔새뇌 대기 2026-10-22〕\n고친 줄 〔새뇌 반영 abc1234〕\n", encoding="utf-8")
    board = tmp_path / "새뇌대기.md"
    board.write_text("| 날짜 | 무엇 | 상태 |\n|---|---|---|\n| 10-22 | a | 새뇌 대기 |\n| 10-22 | b | 〔새뇌 반영 abc1234〕 |\n", encoding="utf-8")
    w = bl.old_open_waits([old], board)
    assert [(f, i) for f, i, _ in w] == [("m.md", 2), ("새뇌대기.md", 3)]


def test_oldcheck_setting_vs_code_literal(tmp_path):
    """세팅 줄의 `모듈.상수` 값이 그 줄에 안 보이면, 옛 MD가 그 상수를 다른 숫자로 말하면 잡는다(코드 = live_check.QUOTE_FAIL_STOP 0.10)."""
    s = tmp_path / "세팅.md"
    s.write_text("- 시세 실패 멈춤 **10% 이상** · `live_check.QUOTE_FAIL_STOP`\n- 틀린 줄 15% · `live_check.QUOTE_FAIL_STOP`\n", encoding="utf-8")
    o = tmp_path / "claude.md"
    o.write_text("QUOTE_FAIL_STOP = 0.2 로 둔다\nQUOTE_FAIL_STOP는 문턱이다 2H\n", encoding="utf-8")
    out = bl.old_setting_mismatch(s, [o])
    assert any(x.startswith("세팅 2행") for x in out) and not any(x.startswith("세팅 1행") for x in out)
    assert sum("claude.md:1" in x for x in out) == 2 and not any("claude.md:2" in x for x in out)   # 세팅 줄 둘이 각각 옛 줄 1을 잡음
