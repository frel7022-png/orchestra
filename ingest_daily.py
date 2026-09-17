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

    # 확정 종가 기준(compute_metrics_at_close) — 반영 시점이 장중이어도 asset_history
    # 스냅샷이 항상 그 날짜의 실제 마감 기준이 되게 한다(2026-09-14, 9/11 낮 12:36 스냅
    # 오염으로 DC 캡처가 -1 근처까지 왜곡된 것을 계기로 도입). portfolio_data.csv의 표시용
    # 현재가는 그대로 두고(save_holdings는 위에서 이미 끝남) 스냅샷 계산에만 별도로 씀.
    df, stock_val, total_assets, unrealized_loss = core.compute_metrics_at_close(
        holdings2, state2["cash"], trade_date)
    core.snapshot_history(total_assets, total_assets + unrealized_loss, on_date=trade_date)
    core.snapshot_sector_history(core.compute_sector_weights(df), on_date=trade_date)

    # 지수 대비 계좌(§6-17): asset_history엔 trade_date 행이 생기는데 index_history는 앱
    # 새로고침에서만 갱신됐고(그건 배포 서버 로컬에만 쓰여 git엔 안 올라감 → 재배포 때 초기화),
    # 그래서 배포판에서 index_history가 asset_history보다 며칠 뒤처져 "혼합지수 당일 0.00%"
    # 버그가 났다(2026-09-07 실제로 겪음). ingest에서도 같이 찍어 두 파일을 lock-step으로.
    #
    # **확정 종가 우선(2026-09-10, 2026-09-17 core.confirmed_close_or_live로 통합)**: 과거
    # 날짜의 매매일지를 오늘(장중)에 반영하면 fetch_index_quotes()/fetch_bigcap_quotes()는
    # '오늘 장중값'을 준다 — 그걸 그 과거 날짜 행에 찍으면 히스토리가 오염된다. 이 로직을
    # new1/meritz가 각자 로컬 클로저로 복제하던 걸(§6-2 4번째 재발 당시) portfolio_core.py의
    # 공유 함수로 통합함 — 실제로 그 복제 때문에 meritz의 index_history[9/16]이 확정 종가와
    # 65p 어긋난 채 방치된 사고가 재발했음(2026-09-17).
    _close_on = core.confirmed_close_or_live

    try:
        iq = core.fetch_index_quotes() or {}
        kospi = _close_on("KOSPI", trade_date, (iq.get("KOSPI") or {}).get("price"))
        kosdaq = _close_on("KOSDAQ", trade_date, (iq.get("KOSDAQ") or {}).get("price"))
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
        closes = {n: _close_on(core.BIGCAP_CODES[n], trade_date, bq.get(n)) for n in core.BIGCAP_CODES}
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

    # ---- 라이브 시세 새로고침, 매일 무조건 (2026-09-17 추가) ----
    # 예전엔 "코드 미확인 종목이 있을 때만" 세션이 수동으로 refresh_all_prices를 돌렸는데,
    # 코드가 이미 다 있는 평범한 날엔 이 스텝이 통째로 스킵돼서 portfolio_data.csv에 커밋되는
    # 현재가/등락률이 "마지막으로 라이브 새로고침됐던 시점"에 그대로 멈춰있었다. 실제로 겪음
    # (2026-09-17): GS리테일이 9/15 17:20 시점 값(등락률 -5.79%)으로 이틀 넘게 멈춰있었는데
    # 그 사이 코드 미확인 종목이 없어 아무도 이걸 안 건드렸고, 그 stale 값이 그대로 커밋돼
    # §6-33 Today's Alarm에 "오늘 -5.8% 급락"으로 잘못 떴다(실제 그날 등락률은 -0.21%).
    # 배포 서버의 라이브 새로고침은 git에 안 올라가므로(§6-1), 재배포가 잦은 날엔 이 stale
    # 커밋값으로 계속 되돌아가는 것처럼 보인다 — 매 ingest마다 무조건 한 번 라이브로 새로고침해
    # 커밋하면 이 멀티데이 staleness 자체가 생기지 않는다. compute_metrics_at_close(위) 기반
    # 스냅샷 계산은 이미 끝났으므로 여기서 표시용 현재가만 바꿔도 asset_history 등엔 영향 없음.
    holdings2, _price_report = core.refresh_all_prices(holdings2)
    core.save_holdings(holdings2)
    if _price_report["unresolved"]:
        print("[경고] 시세를 못 찾은 종목: " + ", ".join(_price_report["unresolved"]))
    if _price_report["failed"]:
        print("[경고] 시세 조회 실패 종목: " + ", ".join(_price_report["failed"]))

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
