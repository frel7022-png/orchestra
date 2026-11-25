"""auto_morning_report.commit_outputs — 리포트 산출물만 커밋하고, 다른 창이 스테이지만 해 둔 파일은 안 섞는다(2026-11-25 고를 것 5)."""
import subprocess

import auto_morning_report as amr


def _git(repo):
    def run(*args):
        return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8")
    return run


def test_commit_outputs_leaves_other_staged_files(tmp_path):
    g = _git(tmp_path)
    g("init", "-q")
    g("config", "user.email", "t@example.com")
    g("config", "user.name", "t")
    (tmp_path / "base.txt").write_text("0")
    g("add", "base.txt")
    g("commit", "-q", "-m", "base")
    for d in amr.OUTPUTS:
        (tmp_path / d).mkdir()
        (tmp_path / d / "r.html").write_text("report")
    (tmp_path / "other.csv").write_text("x")
    g("add", "other.csv")                      # 다른 창이 스테이지만 해 둔 파일
    c = amr.commit_outputs("auto", run=g)
    assert c is not None and c.returncode == 0
    committed = g("show", "--name-only", "--format=", "HEAD").stdout.split()
    assert sorted(committed) == sorted(f"{d}/r.html" for d in amr.OUTPUTS)
    assert "other.csv" in g("diff", "--cached", "--name-only").stdout   # 여전히 스테이지돼 있음
    assert amr.commit_outputs("again", run=g) is None                  # 리포트 변경 없으면 None
