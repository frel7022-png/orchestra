"""
증권사 "일일 매매일지" CSV 한 장을 반영하는 스크립트.

사용법:
    python ingest_daily.py <파일경로> <YYYY-MM-DD>

하는 일:
    1. 해당 파일을 파싱해서 그날의 매수/매도 내역을 뽑아낸다.
    2. transactions.csv에서 같은 날짜에 이미 이 방식으로 반영된 거래가 있으면
       지우고, 이번 내용으로 교체한다 (증권사 CSV는 "그날 하루 전체 누적"이라
       두 번 올려도 중복되지 않게 하기 위함).
    3. transactions.csv를 재생(replay)해서 holdings/현금/실현손익을 다시 계산하고,
       portfolio_data.csv / transactions.csv / account_state.csv에 저장한다 — 매번 전체를
       처음부터 재생하지 않고, 체크포인트(checkpoint_holdings.csv / checkpoint_state.csv) 이후
       구간만 재생한다(rebuild_portfolio_incremental, portfolio_core.py 참고).
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
    prior_holdings = core.load_holdings()
    holdings2, state2, tx2 = core.rebuild_portfolio_incremental(
        tx2, state.get("initial", 10_000_000.0), state.get("fee_rate", 0.0),
        prior_holdings=prior_holdings)

    core.save_transactions(tx2)
    core.save_holdings(holdings2)
    core.save_state(state2)

    df, stock_val, total_assets, unrealized_loss = core.compute_metrics(holdings2, state2["cash"])
    core.snapshot_history(total_assets, total_assets + unrealized_loss, on_date=trade_date)
    core.snapshot_sector_history(core.compute_sector_weights(df), on_date=trade_date)

    # 지수 대비 계좌(§6-17): asset_history엔 trade_date 행이 생기는데 index_history는 앱
    # 새로고침에서만 갱신됐고(그건 배포 서버 로컬에만 쓰여 git엔 안 올라감 → 재배포 때 초기화),
    # 그래서 배포판에서 index_history가 asset_history보다 며칠 뒤처져 "혼합지수 당일 0.00%"
    # 버그가 났다(2026-09-07 실제로 겪음). ingest에서도 같이 찍어 두 파일을 lock-step으로.
    #
    # **확정 종가 우선(2026-09-10)**: 과거 날짜의 매매일지를 오늘(장중)에 반영하면
    # fetch_index_quotes()/fetch_bigcap_quotes()는 '오늘 장중값'을 준다 — 그걸 그 과거
    # 날짜 행에 찍으면 히스토리가 오염된다(실제로 겪음: meritz 9/9 행이 9/10 장중값으로 덮여
    # new1과 어긋나고 혼합지수·VIP vs Orchestra 패널이 통째로 오염됨). 그래서 네이버 일별
    # 시세(fetch_daily_price_history)로 trade_date '그 날짜의 종가'를 먼저 조회하고, 그게
    # 없을 때(막 개장한 당일 등)만 실시간 시세로 폴백한다. 이래야 new1/meritz가 언제
    # 반영하든 index_history/bigcap_history가 같은 값으로 수렴한다.
    def _close_on(code, fallback):
        try:
            for row in core.fetch_daily_price_history(code, trade_date, trade_date) or []:
                if row.get("날짜") == trade_date and row.get("종가"):
                    return float(row["종가"])
        except Exception:
            pass
        return fallback

    try:
        iq = core.fetch_index_quotes() or {}
        kospi = _close_on("KOSPI", (iq.get("KOSPI") or {}).get("price"))
        kosdaq = _close_on("KOSDAQ", (iq.get("KOSDAQ") or {}).get("price"))
        if kospi and kosdaq:
            core.snapshot_index_history(kospi, kosdaq, on_date=trade_date)
            print(f"[지수] {trade_date} 코스피 {kospi:,.2f} · 코스닥 {kosdaq:,.2f} index_history 반영")
    except Exception as e:
        print(f"[경고] index_history 갱신 실패(무시): {e}")

    # SamHynix extracted(§6-19)도 같은 이유로 lock-step. bigcap_history가 index_history보다
    # 하루라도 비면 synthetic_kospi_ex_bigcap이 '여러 날치 대형주 수익률'을 'KOSPI 하루치'에서
    # 빼서 ex 지수가 폭주한다(2026-09-08 실제로 -10%까지 튐).
    try:
        bq = core.fetch_bigcap_quotes() or {}
        closes = {n: _close_on(core.BIGCAP_CODES[n], bq.get(n)) for n in core.BIGCAP_CODES}
        if all(closes.get(n) for n in core.BIGCAP_CODES):
            core.snapshot_bigcap_history(closes, on_date=trade_date)
            print(f"[대형주] {trade_date} " + " · ".join(f"{n} {closes[n]:,.0f}" for n in core.BIGCAP_CODES)
                  + " bigcap_history 반영")
    except Exception as e:
        print(f"[경고] bigcap_history 갱신 실패(무시): {e}")

    # 펀드 기준가(§6-21)는 자동 조회 경로가 없어서 세션이 채팅으로 값을 받아
    # fund_nav_history.csv에 직접 append한다(ingest에서 안 다룸).

    # 신규 종목은 아직 종목코드가 비어있을 수 있는데(코드 캐시에 없던 이름), 그러면 바로 아래
    # watchlist 자동 편입이 걸러버린다. 백필 전에 코드 없는 종목만 네이버로 가볍게 조회해 채운다
    # (시세는 안 받음 — 시세/등락률 보충은 §6-2대로 세션이 refresh_all_prices로 따로 함).
    missing_code = holdings2[holdings2["종목코드"].astype(str).str.len() < 6]
    if not missing_code.empty:
        code_cache = core.load_code_cache()
        resolved = {}
        for nm in missing_code["종목명"].tolist():
            c = core.resolve_code(nm, code_cache)
            if c:
                resolved[nm] = c
                holdings2.loc[holdings2["종목명"] == nm, "종목코드"] = c
        if resolved:
            core.update_code_cache(resolved)
            core.save_holdings(holdings2)
            print("[코드보충] " + ", ".join(f"{n}={c}" for n, c in resolved.items()))

    # 관심종목(watchlist) 밖의 신규 보유종목이 있으면 Supabase에 자동 편입 (§6-16) — 안 그러면
    # 그 종목이 Fishing/Volume/Foreigner 스크리너에 계속 안 나온다. 실패해도(시크릿 없음/네트워크
    # 오류 등) 매매일지 반영 자체는 성공으로 두고 경고만 남긴다.
    try:
        import backfill_watchlist_from_holdings as bw
        print("---- 관심종목(watchlist) 밖 신규 보유종목 자동 편입 ----")
        bw.main()
    except Exception as e:
        print(f"[경고] watchlist 자동 편입 실패(매매일지 반영은 정상 완료됨): {e}")

    # ---- 히스토리 파일 정합성 자동 체크 (§6-2, 2026-09-10) ----
    _al = core.check_history_alignment(trade_date)
    if _al["aligned"] and _al["target_ok"]:
        print(f"[정합성 OK] asset/sector/index/bigcap_history 전부 {_al['latest']}까지 · {trade_date} 포함")
    else:
        print("[⚠️ 정합성] 히스토리 파일 커버가 어긋남 — git commit 전에 확인:")
        for _n, _mx in _al["maxes"].items():
            print(f"    {_n:16s} 마지막 {_mx or '(비어있음)'}"
                  + ("  <-- 뒤처짐" if _n in _al["behind"] else ""))
        if _al["target_ok"] is False:
            print(f"    ※ 반영일 {trade_date}가 일부 파일에 없음")

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
                  f" (종목코드 {r['종목코드'] or '미확인'}, 섹터 {r['섹터'] or '기타2'})")


if __name__ == "__main__":
    main()
