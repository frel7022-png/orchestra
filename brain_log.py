"""뇌(셀) 기록 도구 — 손으로 쓰는 건 셀 칸 + 커밋 `셀:` 줄 두 곳뿐, 나머지는 여기서 뽑는다 (규율 v0.9 §16, 2026-10-19).

    python brain_log.py log [시작일] [끝일]   # 커밋 메시지의 `셀:` 줄 모음 — 셀 이력·하루 일기·주간 "뇌 변경 보고"의 원천
    python brain_log.py storage              # 셀마다의 `저장고:` 줄(원본)을 모아 셀관리.md 저장고 표를 다시 만든다
    python brain_log.py heads                # 셀 머리(제목 ~ "## 꼬리표" 전) 줄 수 — 15 넘으면 표시
    python brain_log.py links                # 셀 사이 길(다음 셀 신호) 표 → 셀관리.md + 뇌_초안 안 끊긴 경로 검사(2026-10-20 셀회의)
                                             #   + 셀→코드 문 검사(심볼·@뇌 이름표·줄 번호 금지, 2026-10-21)
    python brain_log.py where 이름|파일.py    # 이름표·심볼의 지금 줄 / 그 파일을 가리키는 셀 문 목록(구조 바꾸기 전)
"""
import datetime
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
    return sorted(p for p in BRAIN.glob("*/*.md") if not p.name.startswith("_") and p.parent.name not in ("기록관", "설계도실", "ARCHIVE", "HISTORY"))   # 기록관은 원문 보관, HISTORY는 날짜별 서사(셀 아님)


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


# ---- 셀 → 코드 문 (2026-10-21 위크·새러데이 합의, 대표 "코드를 고치거나 쪼개면 문이 깨지냐") ----
# 셀은 코드를 줄 번호로 가리키지 않는다(줄이 밀리면 조용히 엉뚱한 줄을 가리킨다). 두 꼴만 쓴다:
#   심볼 문  `모듈.이름` — 함수·클래스·최상위 대입(중첩 함수는 `모듈.바깥.안`, 모듈 설명은 `모듈.__doc__`)
#   이름표 문 `@뇌 이름` — 함수 안 특정 갈래만. 코드 쪽엔 그 자리 위에 `# @뇌 이름` 한 줄(셀 경로는 안 씀).
# 검사는 import 없이 AST·텍스트만 읽는다(실데이터 무접촉). 기록 칸(HISTORY·ARCHIVE·기록관)은 그날의 사실이라 뺀다.
CODE_SKIP = ("HISTORY", "ARCHIVE", "기록관")
LINE_REF = re.compile(r"(?<![\w/.])(?:new1/|\./)?([A-Za-z_]\w*\.py):\d+")   # `new1/`·`./` 접두도 이 레포(위크 10-21)
TAG_REF = re.compile(r"@뇌 ([^\s`*/<][^\s`*/]*)")   # `@뇌 <이름>`은 자리표시(규칙 문서의 예)
SYM_REF = re.compile(r"`([A-Za-z_]\w*)\.([^\W\d][\w.]*)")


def code_cells() -> list[Path]:
    return sorted(p for p in BRAIN.rglob("*.md") if p.relative_to(BRAIN).parts[0] not in CODE_SKIP)


def _py_files() -> list[Path]:
    return sorted(HERE.glob("*.py"))


def code_tags() -> dict[str, list[tuple[str, int]]]:
    """코드의 `@뇌 이름` → [(파일, 줄)]. 주석(#)이나 CSS 주석(/* */) 어디든 텍스트로 찾는다."""
    tags = {}
    for p in _py_files():
        if p.name == "brain_log.py":
            continue
        for i, ln in enumerate(p.read_text(encoding="utf-8").split("\n"), 1):
            for m in TAG_REF.finditer(ln):
                tags.setdefault(m.group(1), []).append((p.name, i))
    return tags


def _symbols(path: Path) -> dict[str, int]:
    """모듈의 이름 → 줄. 최상위 def/class/대입 + 중첩 def/class(점으로 이음) + __doc__."""
    import ast
    t = ast.parse(path.read_text(encoding="utf-8"))
    out = {}
    if ast.get_docstring(t) is not None:
        out["__doc__"] = 1

    def walk(nodes, prefix):
        for n in nodes:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                out[prefix + n.name] = n.lineno
                walk(n.body, prefix + n.name + ".")
            elif not prefix and isinstance(n, (ast.Assign, ast.AnnAssign)):
                for tg in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                    for x in ([tg] if isinstance(tg, ast.Name) else getattr(tg, "elts", [])):
                        if isinstance(x, ast.Name):
                            out[x.id] = n.lineno
    walk(t.body, "")
    return out


def code_link_problems() -> list[str]:
    """셀 → 코드 문 검사. 빈 리스트면 통과.
    - 줄 번호 문(`파일.py:줄`)이 남아 있으면 실패
    - `@뇌 이름`이 코드에 정확히 하나 없으면 실패, 코드의 이름표를 아무 셀도 안 가리키면(고아) 실패
    - `모듈.이름`에서 모듈이 이 레포 .py인데 그 이름이 없으면 실패(모듈이 아닌 것 — `core.`·`st.` 등 — 은 안 본다)"""
    mods = {p.stem: p for p in _py_files()}
    syms, probs, used = {}, [], set()
    tags = code_tags()
    for p in code_cells():
        rel = p.relative_to(BRAIN)
        for i, ln in enumerate(p.read_text(encoding="utf-8").split("\n"), 1):
            for m in LINE_REF.finditer(ln):
                if (HERE / m.group(1)).exists() and "Thursday" not in ln[max(0, m.start() - 40):m.start()]:
                    probs.append(f"줄 번호 문: {rel}:{i}  {m.group(0)}")
            for m in TAG_REF.finditer(ln):
                name = m.group(1)
                used.add(name)
                n = len(tags.get(name, []))
                if n != 1:
                    probs.append(f"이름표 {'없음' if n == 0 else f'{n}곳 중복'}: {rel}:{i}  @뇌 {name}")
            for m in SYM_REF.finditer(ln):
                mod, name = m.group(1), m.group(2).rstrip(".")
                if (mod not in mods or name.split(".")[0] in ("py", "csv", "md", "json", "html", "txt", "xlsx")
                        or "Thursday" in ln[max(0, m.start() - 40):m.start()]):
                    continue
                if mod not in syms:
                    syms[mod] = _symbols(mods[mod])
                if name not in syms[mod]:
                    probs.append(f"심볼 없음: {rel}:{i}  {mod}.{name}")
    for name, where in tags.items():
        if name not in used:
            probs.append(f"고아 이름표(가리키는 셀 없음): {where[0][0]}:{where[0][1]}  @뇌 {name}")
    return probs


def where(name: str) -> list[str]:
    """이름표·심볼의 지금 자리. `where 파일.py`면 그 파일을 가리키는 셀 문 목록(구조 바꾸기 전 점검)."""
    out = []
    if name.endswith(".py"):
        stem = name[:-3]
        tag_names = {t for t, w in code_tags().items() if any(f == name for f, _ in w)}
        for p in code_cells():
            for i, ln in enumerate(p.read_text(encoding="utf-8").split("\n"), 1):
                if f"`{stem}." in ln or any(f"@뇌 {t}" in ln for t in tag_names):
                    out.append(f"{p.relative_to(BRAIN)}:{i}")
        return out
    name = name.removeprefix("@뇌 ")
    for f, i in code_tags().get(name, []):
        out.append(f"@뇌 {name} → {f}:{i}")
    if "." in name:
        mod, sym = name.split(".", 1)
        if (HERE / f"{mod}.py").exists():
            ln = _symbols(HERE / f"{mod}.py").get(sym)
            if ln:
                out.append(f"{name} → {mod}.py:{ln}")
    return out


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


def history_skeleton(day: str) -> str:
    """HISTORY 하루 파일 뼈대 — 그날 커밋(id·제목)과 거기 나온 대표 원문 번호·바뀐 셀을 git log에서 뽑는다.
    손으로 쓰는 건 흐름·물어본 것·남은 것만(2026-10-20 위크 회의 — 서사는 셀에서 빼서 HISTORY로)."""
    nxt = (datetime.date.fromisoformat(day) + datetime.timedelta(days=1)).isoformat()
    log = subprocess.run(["git", "-c", "core.quotepath=off", "log", f"--since={day} 00:00", f"--until={nxt} 00:00", "--reverse",
                          "--name-only", "--format=@@%h %s"], cwd=BRAIN.parent, capture_output=True,
                         text=True, encoding="utf-8").stdout
    commits, cells, wons = [], set(), []
    for ln in log.splitlines():
        if ln.startswith("@@"):
            commits.append(ln[2:])
            wons += [w for w in re.findall(r"원-\d{4}-\d{2}-\d{2}-\d+", ln) if w not in wons]
        elif ln.startswith("뇌_초안/") and ln.count("/") == 2 and not ln.split("/")[2].startswith("_"):
            top = ln.split("/")[1]
            if top not in ("기록관", "설계도실", "ARCHIVE", "HISTORY"):
                cells.add(ln[len("뇌_초안/"):-3])
    out = [f"## 진행된 일 (커밋 {len(commits)}개, git log)"] + [f"- {c}" for c in commits]
    out += ["", "## 바뀐 셀", "- " + (", ".join(sorted(cells)) or "(없음)"),
            "", "## 대표 원문 번호(커밋 메시지에 나온 것)", "- " + (", ".join(wons) or "(없음)")]
    return "\n".join(out)


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
    elif a and a[0] == "history":
        print(history_skeleton(a[1]))
    elif a and a[0] == "history-old":
        # 3개월 넘은 HISTORY 하루 파일 — 지우기 전 대표에게 묻는다(원-2026-10-20-24). 목차 줄은 남긴다.
        cut = (datetime.date.today() - datetime.timedelta(days=92)).isoformat()
        old = sorted(p.stem for p in (BRAIN / "HISTORY").glob("????-??-??.md") if p.stem < cut)
        print("3개월 넘은 HISTORY 파일: " + (", ".join(old) if old else "없음"))
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
        code = code_link_problems()
        for x in code:
            print(x)
        print(f"코드 문 문제 {len(code)}개 (이름표 {len(code_tags())}개)")
        return 1 if broken or code else 0
    elif a and a[0] == "where" and len(a) > 1:
        for ln in where(" ".join(a[1:])) or ["없음"]:
            print(ln)
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
