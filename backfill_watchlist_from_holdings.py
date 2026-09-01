"""
153개 관심종목(Fishing 기반 데이터, Supabase watchlist/price_history)에 없는 현재 보유종목을
찾아서 리스트에 추가하고, 그 종목이 실제로 포트폴리오에 "처음 들어온 날짜"(현재 보유 사이클의
최초 매수일)부터 오늘까지의 일별 시세를 네이버에서 백필해 Supabase price_history에 채운다.

사용법:
    python backfill_watchlist_from_holdings.py

동작:
    1. portfolio_data.csv(현재 보유종목)와 watchlist.csv(153개)를 비교해 watchlist에 없는
       보유종목을 찾는다.
    2. 각 종목의 "현재 보유 사이클" 최초 매수일을 transactions.csv에서 구한다
       (get_holding_trade_points, §6-10과 동일한 사이클 기준 — 전량매도 후 재진입이면
       그 이후만). 이래야 Fishing의 "누적" 등락률 기준일이 실제 진입 시점과 맞고,
       Up/Down에서도 판별 가능해진다.
    3. 네이버 일별시세 API(api.finance.naver.com/siseJson.naver)로 (최초매수일 - 여유 10일)
       ~ 오늘 시세를 받아와 등락률을 직접 계산(전날 종가 대비)한 뒤, 최초매수일 이후
       구간만 Supabase price_history에 upsert한다. 여유 10일을 두는 이유: 최초매수일
       당일의 등락률을 계산하려면 그 전날 종가가 필요하기 때문(그 전날 데이터 자체는
       저장하지 않고 계산에만 씀).
    4. Supabase watchlist 테이블과 로컬 watchlist.csv 양쪽에 종목을 추가한다(기존 153개는
       안 건드림 — 새 종목만 append).
    5. 결과 요약 출력. 이후 세션이 git add/commit/push로 watchlist.csv를 반영해야 한다(§1-5).

**재사용 가능**: 앞으로 153개 밖의 새 종목이 보유종목에 들어올 때마다 이 스크립트를 다시
실행하면 된다 — 이미 watchlist에 있는 종목은 건드리지 않고 새로 편입된 것만 처리한다.
"""
import tomllib
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

import portfolio_core as core

HERE = Path(__file__).parent
PLACEHOLDER_USER_ID = "00000000-0000-0000-0000-000000000000"


def load_supabase_credentials() -> tuple[str, str]:
    secrets_path = HERE / ".streamlit" / "secrets.toml"
    secrets = tomllib.loads(secrets_path.read_text(encoding="utf-8"))
    sb = secrets.get("supabase", {})
    if not sb.get("url") or not sb.get("anon_key"):
        raise RuntimeError("`.streamlit/secrets.toml`에 [supabase] 접속 정보가 없습니다.")
    return sb["url"], sb["anon_key"]


def upsert(sb_url: str, headers: dict, table: str, records: list[dict], on_conflict: str) -> int:
    if not records:
        return 0
    url = f"{sb_url}/rest/v1/{table}?on_conflict={on_conflict}"
    resp = requests.post(url, headers=headers, json=records, timeout=60)
    if not resp.ok:
        raise RuntimeError(f"{table} upsert 실패 ({resp.status_code}): {resp.text}")
    return len(resp.json())


def main():
    sb_url, anon_key = load_supabase_credentials()
    headers = {
        "apikey": anon_key, "Authorization": f"Bearer {anon_key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation,resolution=merge-duplicates",
    }

    holdings = core.load_holdings()
    watchlist = core.load_watchlist()
    tx = core.load_transactions()

    missing = holdings[(~holdings["종목코드"].isin(watchlist["종목코드"])) & (holdings["종목코드"] != "")]
    if missing.empty:
        print("[알림] 153개 관심종목 밖의 보유종목이 없습니다. 할 일 없음.")
        return

    print(f"관심종목(153) 밖 보유종목 {len(missing)}개 발견 — 백필 시작")
    today = core.today_kst_str()
    new_watchlist_rows = []
    price_records = []
    skipped = []

    for _, r in missing.iterrows():
        name, code, sector = r["종목명"], r["종목코드"], r["섹터"]
        pts = core.get_holding_trade_points(tx, name)
        buys = pts[pts["구분"] == "매수"]
        if buys.empty:
            skipped.append((name, "매수 기록 없음"))
            continue
        first_buy = buys["날짜"].min()

        buffer_start = (date.fromisoformat(first_buy) - timedelta(days=10)).isoformat()
        hist = core.fetch_daily_price_history(code, buffer_start, today)
        if not hist:
            skipped.append((name, "네이버 일별시세 조회 실패"))
            continue

        stock_records = []
        prev_close = None
        for h in hist:
            if prev_close is not None and h["날짜"] >= first_buy:
                change_pct = (h["종가"] - prev_close) / prev_close * 100 if prev_close else 0.0
                stock_records.append({
                    "stock_code": code, "trade_date": h["날짜"],
                    "close_price": h["종가"], "change_pct": round(change_pct, 2),
                    "volume": h["거래량"],
                })
            prev_close = h["종가"]

        if not stock_records:
            skipped.append((name, f"최초매수일({first_buy}) 이후 시세 데이터 없음"))
            continue

        print(f"  - {name}({code}): 최초매수일 {first_buy}, 시세 {len(stock_records)}일치 백필")
        price_records.extend(stock_records)
        new_watchlist_rows.append({"종목명": name, "종목코드": code, "섹터": sector})

    if price_records:
        n = upsert(sb_url, headers, "price_history", price_records, "stock_code,trade_date")
        print(f"[완료] price_history에 {n}건 upsert")

    if new_watchlist_rows:
        sb_rows = [{"user_id": PLACEHOLDER_USER_ID, "stock_code": r["종목코드"],
                    "stock_name": r["종목명"], "sector": r["섹터"]} for r in new_watchlist_rows]
        n = upsert(sb_url, headers, "watchlist", sb_rows, "user_id,stock_code")
        print(f"[완료] Supabase watchlist에 {n}건 추가")

        merged = pd.concat(
            [watchlist[["종목명", "종목코드"]],
             pd.DataFrame(new_watchlist_rows)[["종목명", "종목코드"]]],
            ignore_index=True)
        core.save_watchlist(merged.to_dict("records"))
        print(f"[완료] 로컬 watchlist.csv에 {len(new_watchlist_rows)}건 추가 (총 {len(merged)}개)")

    if skipped:
        print("\n건너뜀:")
        for name, reason in skipped:
            print(f"  - {name}: {reason}")


if __name__ == "__main__":
    main()
