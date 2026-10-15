"""아침 리포트 자동 생성 — 거래일 09:30 Windows 작업 스케줄러가 실행 (2026-10-15 대표 결정).

    python C:/Users/frel/Desktop/new1/auto_morning_report.py [--no-push]

1. 오늘 장이 안 열렸으면(주말·공휴일, market_traded_on) 아무것도 안 하고 끝낸다.
2. morning_report.py를 돌려 리포트 4종(① 액션 ② 외인 샘플 ③ CFG ④ Basecamp)을 만든다.
   총평 칸은 비어 있다("아직 작성 전") — 총평은 세션이 따로 쓴다.
3. 리포트 산출물만 커밋하고 push한다(orchestra는 public 유지, 대표 결정). 다른 파일은 건드리지 않는다.
4. 결과는 logs/auto_report.log에 남긴다(로컬 전용).

써스데이(Desktop/Thursday)는 10:00에 이 리포트를 읽는다. 09:35까지 안 나오면 대표가 세션에 "리포트 해"로 수동 생성.
"""
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
LOG = HERE / "logs" / "auto_report.log"
OUTPUTS = ["morning_report", "link_sample"]


def log(msg: str) -> None:
    LOG.parent.mkdir(exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")


def git(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=HERE, capture_output=True, text=True, encoding="utf-8")


def main() -> int:
    # 작업 스케줄러로 돌면 콘솔 인코딩이 cp949라 morning_report의 한글 print가 깨지거나 예외가 날 수 있다
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    sys.path.insert(0, str(HERE))
    import portfolio_core as core
    today = core.today_kst_str()
    if core.market_traded_on(today) is False:
        log(f"{today} 휴장 — 건너뜀")
        return 0
    try:
        import morning_report
        morning_report.main()
    except Exception:
        log(f"{today} 리포트 생성 실패\n{traceback.format_exc()}")
        return 1
    log(f"{today} 리포트 생성 완료")
    if "--no-push" in sys.argv:
        return 0
    git("add", *OUTPUTS)
    if not git("diff", "--cached", "--quiet").returncode:
        log("커밋할 변경 없음")
        return 0
    c = git("commit", "-m", f"Overture {today}: 아침 리포트 자동 생성 (09:30)\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>")
    if c.returncode:
        log(f"커밋 실패: {c.stderr.strip()}")
        return 1
    p = git("pull", "--rebase", "--autostash")
    if p.returncode:
        git("rebase", "--abort")
        log(f"pull 실패 — push 안 함(로컬 커밋은 남음): {p.stderr.strip()}")
        return 1
    p = git("push")
    log("push 완료" if not p.returncode else f"push 실패: {p.stderr.strip()}")
    return p.returncode


if __name__ == "__main__":
    sys.exit(main())
