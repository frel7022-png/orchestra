"""
"Fishing"(관심종목) 리스트 CSV를 반영하는 스크립트.

사용법:
    python import_watchlist.py <파일경로>

CSV는 종목명이 담긴 열 하나(헤더 이름 무관, 첫 번째 열을 씀)로 이루어진 파일을 기대한다
(temporary/ 폴더에 사용자가 올려두는 관심종목 리스트 형식).

하는 일:
    1. 각 종목명을 네이버 자동완성 검색으로 정정/코드 매칭한다(오탈자 대응, portfolio_core.
       match_stock_name 참고) — "오또기"→"오뚜기", "동진세미컴"→"동진쎄미켐" 같은 경우 처리.
    2. 정정된 이름/코드를 watchlist.csv에 저장한다(기존 내용은 이번 파일로 통째로 교체).
    3. 매칭 결과 요약을 출력한다 — 이름이 바뀐 경우와, 확신이 안 서서 실패한 경우를 각각
       보여주니 실패 목록은 사람이 직접 확인해서 watchlist.csv를 손으로 보정할 것.

섹터는 이 스크립트에서 다루지 않는다 — watchlist.csv엔 종목명/종목코드만 저장하고, 화면에
보여줄 때 stock_sector_cache.csv에서 그때그때 조회한다(보유종목과 같은 캐시 재사용). 새로
매칭된 종목이 캐시에 없으면 "미분류"로 보이며, 필요하면 거래 기록 탭의 "섹터 일괄 수정"으로
채워주면 된다.
"""

import sys

import pandas as pd

import portfolio_core as core


def main():
    if len(sys.argv) != 2:
        print("사용법: python import_watchlist.py <파일경로>")
        sys.exit(1)

    file_path = sys.argv[1]

    df = None
    for enc in ("utf-8-sig", "cp949", "utf-8"):
        try:
            df = pd.read_csv(file_path, encoding=enc)
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    if df is None or df.empty:
        print("[오류] 파일을 읽지 못했거나 비어있습니다.")
        sys.exit(1)

    names = [str(v).strip() for v in df.iloc[:, 0].tolist() if str(v).strip() and str(v).strip().lower() != "nan"]
    names = list(dict.fromkeys(names))  # 순서 유지하며 중복 제거

    print(f"총 {len(names)}개 종목명 확인, 네이버에서 매칭 중...")

    matched, renamed, failed = [], [], []
    for name in names:
        result = core.match_stock_name(name)
        if result is None:
            failed.append(name)
            continue
        matched.append({"종목명": result["name"], "종목코드": result["code"]})
        if result["name"] != name:
            renamed.append((name, result["name"]))

    core.save_watchlist(matched)

    print(f"[완료] {len(matched)}개 매칭 성공, {len(failed)}개 실패")
    if renamed:
        print(f"\n이름이 정정된 종목 {len(renamed)}건:")
        for orig, fixed in renamed:
            print(f"  - {orig} → {fixed}")
    if failed:
        print(f"\n매칭 실패(직접 확인 필요) {len(failed)}건:")
        for name in failed:
            print(f"  - {name}")


if __name__ == "__main__":
    main()
