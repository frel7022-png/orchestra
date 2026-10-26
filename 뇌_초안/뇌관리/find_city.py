"""도시 찾기 — 뇌_초안 전체(.md)에서 낱말·숫자를 찾아 묶음별로 보인다.

"도시에 없다"고 쓰기 전에 한 번 돌린다(지침서 3절). 2026-10-26 밤 신설 — 그날 구멍이 거의 다
"도시에 있는데 못 찾음"이었다(대표 「응 다해봐 … 실험이 틀려도 그것도 수확이야」, 위크 전달).

    python 뇌_초안/뇌관리/find_city.py 낱말 [낱말 …]          # 하나라도 있으면(OR)
    python 뇌_초안/뇌관리/find_city.py --and 낱말 낱말 …       # 한 줄에 다 있으면(AND)
    python 뇌_초안/뇌관리/find_city.py -C 2 --max 5 낱말        # 앞뒤 2줄 · 묶음마다 5건까지

같은 뜻 다른 표기를 묶는다: 빼기표(− – — -) · "%포인트"/"% 포인트" = "%p" · 띄어쓰기 유무 · 대소문자.
묶음 순서: 지침서·뿌리 → 길 → 사무실 → 기록관 → 업그레이드 → (맨 뒤) ARCHIVE · HISTORY.
읽기만 한다 — 아무 파일도 쓰지 않는다.
"""
import argparse
import re
import sys
from pathlib import Path

CITY = Path(__file__).resolve().parent.parent  # 뇌_초안/

GROUPS = [  # (이름, 첫 폴더 이름 — None이면 뇌_초안 바로 아래 파일)
    ("지침서·뿌리", None),
    ("길", "길"),
    ("사무실", "*"),
    ("기록관", "기록관"),
    ("업그레이드", "업그레이드"),
    ("ARCHIVE", "ARCHIVE"),
    ("HISTORY", "HISTORY"),
]
NAMED = {g for _, g in GROUPS if g not in (None, "*")}

_MINUS = re.compile(r"[−–—‐-]")
_POINT = re.compile(r"%\s*포인트")
_SPACE = re.compile(r"\s+")


def norm(s: str) -> str:
    s = _MINUS.sub("-", s)
    s = _POINT.sub("%p", s)
    return _SPACE.sub("", s).lower()


def group_of(rel: Path) -> str:
    parts = rel.parts
    if len(parts) == 1:
        return "지침서·뿌리"
    top = parts[0]
    for name, folder in GROUPS:
        if folder == top:
            return name
    return "사무실"


def search(terms: list[str], need_all: bool, ctx: int):
    keys = [norm(t) for t in terms if norm(t)]
    hits = {name: [] for name, _ in GROUPS}
    for path in sorted(CITY.rglob("*.md")):
        rel = path.relative_to(CITY)
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        normed = [norm(l) for l in lines]
        for i, nl in enumerate(normed):
            found = [k in nl for k in keys]
            if (all(found) if need_all else any(found)):
                lo, hi = max(0, i - ctx), min(len(lines), i + ctx + 1)
                hits[group_of(rel)].append((rel, i + 1, [(j + 1, lines[j]) for j in range(lo, hi)]))
    return hits


def main():
    ap = argparse.ArgumentParser(description="뇌_초안 전체에서 낱말·숫자 찾기(묶음별)")
    ap.add_argument("terms", nargs="+")
    ap.add_argument("--and", dest="need_all", action="store_true", help="한 줄에 다 있어야(기본은 하나라도)")
    ap.add_argument("-C", type=int, default=1, help="앞뒤 몇 줄(기본 1)")
    ap.add_argument("--max", type=int, default=20, help="묶음마다 최대 몇 건(기본 20, 0 = 전부)")
    ap.add_argument("--width", type=int, default=220, help="줄을 몇 글자로 자를지(기본 220)")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    hits = search(a.terms, a.need_all, a.C)
    total = sum(len(v) for v in hits.values())
    mode = "AND" if a.need_all else "OR"
    print(f"찾은 말: {' · '.join(a.terms)} ({mode}) — {total}건")
    for name, _ in GROUPS:
        rows = hits[name]
        if not rows:
            continue
        shown = rows if a.max == 0 else rows[: a.max]
        print(f"\n## {name} — {len(rows)}건" + (f" (앞 {len(shown)}건)" if len(shown) < len(rows) else ""))
        for rel, ln, block in shown:
            print(f"- {rel.as_posix()}:{ln}")
            for j, text in block:
                mark = ">" if j == ln else " "
                t = text if len(text) <= a.width else text[: a.width] + "…"
                print(f"    {mark}{j:>4}| {t}")
    if total == 0:
        print("\n없음 — 낱말을 바꿔(같은 뜻 다른 말 · 숫자만 · 함수 이름) 한 번 더 돌린 뒤에만 '도시에 없다'고 쓴다.")


if __name__ == "__main__":
    main()
