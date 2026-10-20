"""뇌(셀) 기록 도구 — 손으로 쓰는 건 셀 칸 + 커밋 `셀:` 줄 두 곳뿐, 나머지는 여기서 뽑는다 (규율 v0.9 §16, 2026-10-19).

    python brain_log.py log [시작일] [끝일]   # 커밋 메시지의 `셀:` 줄 모음 — 셀 이력·하루 일기·주간 "뇌 변경 보고"의 원천
    python brain_log.py storage              # 셀마다의 `저장고:` 줄(원본)을 모아 셀관리.md 저장고 표를 다시 만든다
    python brain_log.py heads                # 셀 머리(제목 ~ "## 꼬리표" 전) 줄 수 — 15 넘으면 표시
    python brain_log.py links                # 셀 사이 길(다음 셀 신호) 표 → 셀관리.md + 뇌_초안 안 끊긴 경로 검사(2026-10-20 셀회의)
"""
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
BRAIN = HERE / "뇌_초안"
MAP = BRAIN / "셀관리.md"
HEAD_MAX = 15
START, END = "<!-- 저장고 표: brain_log.py storage가 만든다 -->", "<!-- 저장고 표 끝 -->"


def cell_files() -> list[Path]:
    return sorted(p for p in BRAIN.glob("*/*.md") if not p.name.startswith("_") and p.parent.name != "기록관")   # 기록관은 셀이 아니라 원문 보관


def cell_lines(since: str | None = None, until: str | None = None) -> list[tuple[str, str, str]]:
    """[(날짜, 커밋, `셀:` 줄)] — 커밋 메시지 본문에서 `셀:`로 시작하는 줄."""
    cmd = ["git", "-C", str(HERE), "log", "--format=%x1e%ad%x1f%h%x1f%B", "--date=short"]
    if since:
        cmd.append(f"--since={since} 00:00")  # 날짜만 주면 git이 지금 시각을 붙인다
    if until:
        cmd.append(f"--until={until} 23:59")
    out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8").stdout
    rows = []
    for rec in out.split("\x1e"):
        if not rec.strip():
            continue
        d, h, body = rec.split("\x1f", 2)
        rows += [(d.strip(), h, ln.strip()) for ln in body.splitlines() if ln.strip().startswith("셀:")]
    return rows


def head_len(text: str) -> int | None:
    ls = text.split("\n")
    return ls.index("## 꼬리표") - 1 if "## 꼬리표" in ls else None


def storage_table(files: list[Path]) -> str:
    out = ["| 셀 | 저장고(셀의 `저장고:` 줄 그대로) |", "|---|---|"]
    for p in files:
        m = re.search(r"^저장고: (.+)$", p.read_text(encoding="utf-8"), re.M)
        if m:
            out.append(f"| `{p.parent.name}/{p.stem}` | {m.group(1)} |")
    return "\n".join(out)


def write_storage() -> None:
    s = MAP.read_text(encoding="utf-8")
    block = f"{START}\n{storage_table(cell_files())}\n{END}"
    if START in s:
        s = re.sub(re.escape(START) + r".*?" + re.escape(END), lambda _: block, s, flags=re.S)
    else:
        s = s.replace("## 저장고", "## 저장고\n" + block + "\n", 1)
    MAP.write_text(s, encoding="utf-8")


LSTART, LEND = "<!-- 길 표: brain_log.py links가 만든다 -->", "<!-- 길 표 끝 -->"
REF = re.compile(r"`((?:\.\./)?(?:뇌_초안/)?(?:[^`/\s]+/)?[^`/\s]+\.md)`")


def _cell_key(p: Path) -> str:
    return f"{p.parent.name}/{p.stem}"


def _sections(text: str) -> dict[str, list[str]]:
    out, cur = {}, None
    for ln in text.split("\n"):
        if ln.startswith("## "):
            cur = ln[3:].strip()
            out[cur] = []
        elif cur:
            out[cur].append(ln)
    return out


def _resolve(ref: str, here: Path) -> Path:
    if ref.startswith("뇌_초안/"):
        return BRAIN / ref[len("뇌_초안/"):]
    return (here.parent / ref).resolve()


def links() -> tuple[dict, dict, list]:
    """셀마다 "다음 셀로 가는 신호" 칸이 가리키는 셀(나감)·그 반대(들어옴), 그리고 뇌_초안 안의 끊긴 경로.
    끊긴 경로 = `…/이름.md` 꼴로 적혔는데 그 파일이 없는 것(셀·게시판·줄기 전부 검사)."""
    cells = cell_files()
    keys = {_cell_key(p) for p in cells}
    out = {}
    for p in cells:
        sig = sum((v for k, v in _sections(p.read_text(encoding="utf-8")).items() if k.startswith("다음 셀")), [])
        hits = set()
        for ln in sig:
            for m in REF.finditer(ln):
                t = _resolve(m.group(1), p)
                k = f"{t.parent.name}/{t.stem}"
                if k in keys and k != _cell_key(p):
                    hits.add(k)
        out[_cell_key(p)] = hits
    inc = {k: {a for a, v in out.items() if k in v} for k in keys}
    broken = []
    for p in sorted(BRAIN.rglob("*.md")):
        for i, ln in enumerate(p.read_text(encoding="utf-8").split("\n"), 1):
            for m in REF.finditer(ln):
                ref = m.group(1)
                head = ref.split("/")[0]
                if "/" not in ref or not (head in ("..", "뇌_초안") or (BRAIN / head).is_dir()):
                    continue   # 폴더 없는 이름(`claude.md`·`_게시판.md`)이나 도시 밖 경로(`Week/…`)는 검사 대상 아님
                if not _resolve(ref, p).exists():
                    broken.append((str(p.relative_to(BRAIN)), i, ref))
    return out, inc, broken


def links_table(out: dict, inc: dict) -> str:
    rows = ["| 셀 | 나감(신호 칸) | 들어옴(신호 칸) |", "|---|---|---|"]
    for k in sorted(out):
        rows.append(f"| `{k}` | {', '.join(sorted(out[k])) or '**없음**'} | {', '.join(sorted(inc[k])) or '**없음**'} |")
    return "\n".join(rows)


def write_links(out: dict, inc: dict) -> None:
    s = MAP.read_text(encoding="utf-8")
    block = f"{LSTART}\n{links_table(out, inc)}\n{LEND}"
    if LSTART in s:
        s = re.sub(re.escape(LSTART) + r".*?" + re.escape(LEND), lambda _: block, s, flags=re.S)
    else:
        s = s.replace("## 저장고", "## 길 (다음 셀 신호 — 뉴런)\n" + block + "\n\n## 저장고", 1)
    MAP.write_text(s, encoding="utf-8")


def main() -> int:
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    a = sys.argv[1:]
    if a and a[0] == "log":
        for d, h, ln in cell_lines(a[1] if len(a) > 1 else None, a[2] if len(a) > 2 else None):
            print(f"{d} {h}  {ln}")
    elif a and a[0] == "storage":
        write_storage()
        print(f"저장고 표 갱신 → {MAP}")
    elif a and a[0] == "links":
        out, inc, broken = links()
        write_links(out, inc)
        print(links_table(out, inc))
        print(f"\n길 표 갱신 → {MAP}")
        for f, i, ref in broken:
            print(f"끊긴 경로: {f}:{i}  {ref}")
        print(f"끊긴 경로 {len(broken)}개")
        return 1 if broken else 0
    elif a and a[0] == "heads":
        bad = 0
        for p in cell_files():
            n = head_len(p.read_text(encoding="utf-8"))
            flag = "" if n is not None and n <= HEAD_MAX else "  <-- 넘침/꼬리표 없음"
            bad += bool(flag)
            print(f"{n}\t{p.relative_to(BRAIN)}{flag}")
        return 1 if bad else 0
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
