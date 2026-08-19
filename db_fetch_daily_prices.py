"""
watchlist 테이블(Supabase)의 전 종목 시세를 조회해서 price_history에 오늘자로 적재.
같은 날 여러 번 실행해도 안전(stock_code+trade_date unique라 upsert로 덮어씀).
GitHub Actions cron이 매일 이 스크립트를 실행한다(.github/workflows/daily-price-fetch.yml).

접속 정보는 로컬에서는 .streamlit/secrets.toml, GitHub Actions에서는 환경변수
(SUPABASE_URL, SUPABASE_ANON_KEY, 레포 secrets에 등록됨)에서 읽는다.
"""
import os
import tomllib
from pathlib import Path

import requests

import portfolio_core as core  # fetch_quotes(), today_kst_str() 재사용

HERE = Path(__file__).parent


def load_supabase_credentials() -> tuple[str, str]:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_ANON_KEY")
    if url and key:
        return url, key
    secrets_path = HERE / ".streamlit" / "secrets.toml"
    if secrets_path.exists():
        secrets = tomllib.loads(secrets_path.read_text(encoding="utf-8"))
        sb = secrets.get("supabase", {})
        if sb.get("url") and sb.get("anon_key"):
            return sb["url"], sb["anon_key"]
    raise RuntimeError(
        "Supabase 접속 정보를 찾을 수 없습니다. 환경변수 SUPABASE_URL/SUPABASE_ANON_KEY를 "
        "설정하거나 .streamlit/secrets.toml의 [supabase] 섹션을 채워주세요."
    )


SUPABASE_URL, ANON_KEY = load_supabase_credentials()
HEADERS = {
    "apikey": ANON_KEY,
    "Authorization": f"Bearer {ANON_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation,resolution=merge-duplicates",
}


def rest_get(path):
    r = requests.get(f"{SUPABASE_URL}/rest/v1/{path}", headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()


def main():
    print("1) watchlist 종목코드 조회...")
    watchlist = rest_get("watchlist?select=stock_code,stock_name")
    codes = [r["stock_code"] for r in watchlist]
    print(f"   대상 {len(codes)}종목")

    print("2) 네이버 시세 조회 (20개씩 청크)...")
    quotes, errors = core.fetch_quotes(codes)
    print(f"   응답 {len(quotes)}종목, 실패 {len(errors)}건")
    for e in errors:
        print("   경고:", e)

    missing = [c for c in codes if c not in quotes]
    if missing:
        names = {r["stock_code"]: r["stock_name"] for r in watchlist}
        print(f"   시세 못 받은 종목({len(missing)}개): {[names[c] for c in missing]}")

    today = core.today_kst_str()
    records = [
        {"stock_code": code, "trade_date": today, "close_price": q["price"], "change_pct": q["change_pct"]}
        for code, q in quotes.items()
    ]

    print(f"3) price_history에 {len(records)}건 upsert (날짜: {today})...")
    url = f"{SUPABASE_URL}/rest/v1/price_history?on_conflict=stock_code,trade_date"
    resp = requests.post(url, headers=HEADERS, json=records, timeout=30)
    if not resp.ok:
        raise RuntimeError(f"적재 실패 ({resp.status_code}): {resp.text}")
    print(f"   완료: {len(resp.json())}건")


if __name__ == "__main__":
    main()
