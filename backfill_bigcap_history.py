"""bigcap_history.csv(삼성전자/삼성전자우/SK하이닉스 일별 종가)를 채운다 — §6-19
"SamHynix extracted"의 합성 지수 계산용.

재실행 가능: 이미 있는 날짜는 그대로 두고, index_history.csv의 시작일부터 오늘까지
중 빠진 날짜만 네이버 일별시세로 받아 채운다. 인자 없음.

    python backfill_bigcap_history.py
"""

import pandas as pd

import portfolio_core as core


def main():
    idx = core.load_index_history()
    if idx.empty:
        print("[중단] index_history.csv가 비어있음 — 지수 히스토리부터 채우세요.")
        return

    start = str(idx["날짜"].min())
    end = core.resolve_trading_date()
    print(f"대상 기간: {start} ~ {end}")

    existing = core.load_bigcap_history()
    have = set(existing["날짜"].astype(str)) if not existing.empty else set()

    frames = {}
    for nm, code in core.BIGCAP_CODES.items():
        rows = core.fetch_daily_price_history(code, start, end)
        frames[nm] = {r["날짜"]: r["종가"] for r in rows}
        print(f"  {nm}({code}): {len(rows)}일 수신")

    all_dates = sorted(set().union(*[set(f) for f in frames.values()]) if frames else set())
    new_rows = [
        {"날짜": d, **{nm: frames[nm].get(d) for nm in core.BIGCAP_CODES}}
        for d in all_dates if d not in have
    ]
    if not new_rows and not existing.empty:
        print("[완료] 이미 최신 — 추가할 날짜 없음.")
        return

    merged = pd.concat([existing, pd.DataFrame(new_rows)], ignore_index=True)
    merged = merged.drop_duplicates(subset="날짜", keep="last").sort_values("날짜")
    core.save_bigcap_history(merged)
    print(f"[완료] 저장 {len(merged)}일 ({merged['날짜'].min()} ~ {merged['날짜'].max()}), "
          f"신규 {len(new_rows)}일")
    print(merged.tail(5).to_string(index=False))


if __name__ == "__main__":
    main()
