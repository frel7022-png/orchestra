"""
이미 보유 중인 종목의 섹터를 안전하게 고치는 스크립트.

사용법:
    python fix_sector.py <종목명> <섹터>

portfolio_data.csv / stock_sector_cache.csv / checkpoint_holdings.csv를 한 번에 같이
고친다(portfolio_core.fix_holding_sector 참고) — 이 중 하나만 고치면 다음
ingest_daily.py 실행 때 값이 옛날 것으로 되돌아갈 수 있다(2026-08-28 실제로 겪은 버그,
CLAUDE.md §1-6 참고).
"""

import sys

import portfolio_core as core


def main():
    if len(sys.argv) != 3:
        print("사용법: python fix_sector.py <종목명> <섹터>")
        sys.exit(1)

    name, sector = sys.argv[1], sys.argv[2]
    touched = core.fix_holding_sector(name, sector)

    if not touched:
        print(f"[알림] '{name}' 종목을 찾지 못했습니다(보유 중이 아니거나 이름 불일치).")
        sys.exit(1)

    print(f"[완료] '{name}' 섹터를 '{sector}'로 변경: " + ", ".join(touched))


if __name__ == "__main__":
    main()
