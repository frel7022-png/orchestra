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
