"""
증권사 "일일 매매일지" CSV 한 장을 반영하는 스크립트.

사용법:
    python ingest_daily.py <파일경로> <YYYY-MM-DD>

하는 일:
    1. 해당 파일을 파싱해서 그날의 매수/매도 내역을 뽑아낸다.
    2. transactions.csv에서 같은 날짜에 이미 이 방식으로 반영된 거래가 있으면
       지우고, 이번 내용으로 교체한다 (증권사 CSV는 "그날 하루 전체 누적"이라
       두 번 올려도 중복되지 않게 하기 위함).
    3. transactions.csv 전체를 처음부터 재생(replay)해서 holdings/현금/실현손익을
       다시 계산하고, portfolio_data.csv / transactions.csv / account_state.csv에 저장한다.
    4. 자산/섹터 스냅샷을 그 날짜 기준으로 남긴다 (거래 캘린더/자산추이 그래프용).
    5. 결과 요약(보유종목 수, 총자산, 현금 등)을 출력한다 — 이 값을 실제
       메리츠 앱 화면과 대조해서 반영이 정확한지 확인할 것.

앱(app.py)에는 업로드 UI를 넣지 않기로 했으므로, 이 스크립트를 매매일지가
생길 때마다(하루에 여러 번이어도 상관없음) 직접 실행하는 방식으로 반영한다.
"""

import sys

import portfolio_core as core


def main():
    if len(sys.argv) != 3:
        print("사용법: python ingest_daily.py <파일경로> <YYYY-MM-DD>")
        sys.exit(1)

    file_path, trade_date = sys.argv[1], sys.argv[2]

    with open(file_path, "rb") as f:
        raw = f.read()

    try:
        parsed = core.parse_daily_trade_csv(raw)
    except Exception as e:
        print(f"[오류] CSV를 읽는 중 문제가 발생했습니다: {e}")
        sys.exit(1)

    if parsed.empty:
        print("[알림] 파일에서 종목 데이터를 찾지 못했습니다. 형식을 확인해주세요.")
        sys.exit(1)

    tx = core.load_transactions()
    tx2, n_new, n_replaced = core.import_daily_trades(parsed, tx, trade_date)

    if n_new == 0:
        print(f"[알림] {trade_date}: 이 파일에는 매수/매도 내역이 없습니다 (전량 0). 반영할 거래가 없어요.")
        sys.exit(0)

    state = core.load_state()
    holdings2, state2, tx2 = core.rebuild_portfolio_from_transactions(
        tx2, state.get("initial", 10_000_000.0), state.get("fee_rate", 0.0))

    core.save_transactions(tx2)
    core.save_holdings(holdings2)
    core.save_state(state2)

    df, stock_val, total_assets, unrealized_loss = core.compute_metrics(holdings2, state2["cash"])
    core.snapshot_history(total_assets, total_assets + unrealized_loss, on_date=trade_date)
    core.snapshot_sector_history(core.compute_sector_weights(df), on_date=trade_date)

    print(f"[완료] {trade_date} 매매일지 반영: 신규 거래 {n_new}건"
          + (f" (기존 {n_replaced}건 교체)" if n_replaced else ""))
    print("---- 반영 후 상태 (실제 메리츠 앱 화면과 대조하세요) ----")
    print(f"보유종목 수: {len(holdings2)}개")
    print(f"예수금(현금): {state2['cash']:,.0f}원")
    print(f"보유종목 평가금액 합계: {stock_val:,.0f}원")
    print(f"총자산(평가금액+현금): {total_assets:,.0f}원")
    if not holdings2.empty:
        print("보유종목:")
        for _, r in holdings2.sort_values("종목명").iterrows():
            print(f"  - {r['종목명']}: {r['수량']:.0f}주 @ 평단가 {r['평단가']:,.0f}원"
                  f" (종목코드 {r['종목코드'] or '미확인'}, 섹터 {r['섹터'] or '미분류'})")


if __name__ == "__main__":
    main()
