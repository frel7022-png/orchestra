"""
"Fishing"(관심종목) 리스트 CSV를 반영하는 스크립트.

사용법:
    python import_watchlist.py <파일경로>

CSV는 종목명이 담긴 열 하나(헤더 이름 무관, 첫 번째 열을 씀)로 이루어진 파일을 기대한다
(temporary/ 폴더에 사용자가 올려두는 관심종목 리스트 형식).

하는 일:
    1. 각 종목명을 네이버 자동완성 검색으로 정정/코드 매칭한다(오탈자 대응, portfolio_core.
       match_stock_name 참고) — "오또기"→"오뚜기", "동진세미컴"→"동진쎄미켐" 같은 경우 처리.
    2. 지금 목록에 **합친다**(통째로 교체하지 않음 — 2026-10-22 대표 승인, 스펙_관심끝_표시):
       새로 들어옴 / 빠진 종목은 줄을 남기고 `관심끝 = 오늘` / 끝났던 종목이 다시 오면 관심끝 비움 /
       보유 중인 종목은 빠져도 관심 끝을 안 붙이고 경고. 같은 내용을 **Supabase watchlist에도**(ended_on)
       — Supabase 쓰기가 실패하면 csv도 안 쓰고 멈춘다(두 벌이 갈리지 않게).
    3. 매칭 결과 요약을 출력한다 — 이름이 바뀐 경우와, 확신이 안 서서 실패한 경우를 각각
       보여주니 실패 목록은 사람이 직접 확인해서 watchlist.csv를 손으로 보정할 것.

섹터는 이 스크립트에서 다루지 않는다 — watchlist.csv엔 종목명/종목코드만 저장하고, 화면에
보여줄 때 stock_sector_cache.csv에서 그때그때 조회한다(보유종목과 같은 캐시 재사용). 새로
매칭된 종목이 캐시에 없으면 기본값 "기타2"로 보이며, 세션이 `fix_sector.py`로 기존 섹터를
지정한다(2026-10-21 기타·기타2 해체 — 새 섹터는 만들지 않는다).
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

    current = core.load_watchlist(include_ended=True)
    held = set(core.load_holdings()["종목명"].astype(str))
    rows, s = core.merge_watchlist(current, matched, held, core.today_kst_str())

    import backfill_watchlist_from_holdings as bf
    try:
        sb_url, sb_key = bf.load_supabase_credentials()
        headers = {"apikey": sb_key, "Authorization": f"Bearer {sb_key}",
                   "Content-Type": "application/json", "Prefer": "return=representation,resolution=merge-duplicates"}
        sector = core.load_sector_cache()
        sb_rows = [{"user_id": bf.PLACEHOLDER_USER_ID, "stock_code": r["종목코드"], "stock_name": r["종목명"],
                    "sector": sector.get(r["종목명"], "기타2"), "ended_on": r["관심끝"] or None} for r in rows]
        bf.upsert(sb_url, headers, "watchlist", sb_rows, "user_id,stock_code")
    except Exception as e:
        print(f"[멈춤] Supabase watchlist 쓰기 실패 — csv도 안 바꿈(두 벌이 갈리지 않게): {e}")
        sys.exit(1)
    core.save_watchlist(rows)

    n_live = sum(1 for r in rows if not r["관심끝"])
    print(f"[완료] {len(matched)}개 매칭 성공, {len(failed)}개 실패 · 관심 중 {n_live} · 관심 끝 {len(rows) - n_live}")
    print(f"  새로 들어옴 {len(s['새로'])} · 관심 끝 {len(s['끝'])} · 다시 들어옴 {len(s['다시'])}")
    for n in s["새로"]:
        print(f"  + {n}")
    for n in s["끝"]:
        print(f"  - {n}  (관심 끝)")
    for n in s["다시"]:
        print(f"  ↺ {n}  (다시 들어옴 — 관심 끝이었던 날 {s['다시_기간'][n]}부터)")
    for n in s["보유경고"]:
        print(f"  ! {n}  (새 목록에 없지만 보유 중 — 관심 끝 안 붙임)")
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
