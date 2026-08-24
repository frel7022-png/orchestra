"""
watchlist 테이블(Supabase)의 전 종목(153개) 시세/거래량/수급을 조회해서 오늘자로 적재.
같은 날 여러 번 실행해도 안전(각 테이블이 stock_code+trade_date 또는 market+trade_date
unique라 upsert로 덮어씀). GitHub Actions cron이 매일 이 스크립트를 실행한다
(.github/workflows/daily-price-fetch.yml).

2026-08-24에 거래량/수급(외국인·기관 순매수, 코스피·코스닥 시장 전체 베이스라인) 적재를
추가함 — 153개 전체 watchlist 종목 대상(51개 보유종목만이 아니라, 신규 편입 후보 판단
근거로도 쓰려는 목적). fetch_investor_flow는 종목 하나당 페이지 하나씩 긁는 비공식
스크레이핑이라 153번 순차 요청함 — 너무 몰아치지 않게 요청 사이 짧은 지연을 둠.

접속 정보는 로컬에서는 .streamlit/secrets.toml, GitHub Actions에서는 환경변수
(SUPABASE_URL, SUPABASE_ANON_KEY, 레포 secrets에 등록됨)에서 읽는다.
"""
import os
import time
import tomllib
from pathlib import Path

import requests

import portfolio_core as core  # fetch_quotes(), fetch_investor_flow(), fetch_market_flow() 재사용

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


def upsert(table: str, records: list[dict], on_conflict: str, chunk_size: int | None = None) -> int:
    """records를 chunk_size씩(없으면 한 번에) upsert하고 총 적재 건수를 반환."""
    url = f"{SUPABASE_URL}/rest/v1/{table}?on_conflict={on_conflict}"
    chunks = [records[i:i + chunk_size] for i in range(0, len(records), chunk_size)] \
        if chunk_size else [records]
    total = 0
    for chunk in chunks:
        resp = requests.post(url, headers=HEADERS, json=chunk, timeout=60)
        if not resp.ok:
            raise RuntimeError(f"{table} 적재 실패 ({resp.status_code}): {resp.text}")
        total += len(resp.json())
    return total


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
        {"stock_code": code, "trade_date": today, "close_price": q["price"],
         "change_pct": q["change_pct"], "volume": q.get("volume")}
        for code, q in quotes.items()
    ]

    print(f"3) price_history에 {len(records)}건 upsert (날짜: {today})...")
    try:
        n = upsert("price_history", records, "stock_code,trade_date")
        print(f"   완료: {n}건")
    except RuntimeError as e:
        if "volume" in str(e) and "does not exist" in str(e):
            print("   경고: price_history에 volume 컬럼이 아직 없습니다(마이그레이션 SQL "
                  "미실행) — volume 없이 재시도합니다.")
            for r in records:
                r.pop("volume", None)
            n = upsert("price_history", records, "stock_code,trade_date")
            print(f"   완료(volume 제외): {n}건")
        else:
            raise

    # 아래 두 단계(4~7)는 2026-08-24 신규 추가라, DB 마이그레이션이 아직 안 됐거나
    # 스크레이핑이 실패해도 위의 핵심 시세 적재(1~3)는 절대 안 깨지게 통째로 감싼다.
    try:
        print(f"4) 종목별 외국인/기관 수급 조회 ({len(codes)}종목, 종목당 최근 20영업일)...")
        flow_records = []
        flow_failed = []
        for i, code in enumerate(codes):
            rows = core.fetch_investor_flow(code)
            if not rows:
                flow_failed.append(code)
            else:
                for r in rows:
                    flow_records.append({
                        "stock_code": code, "trade_date": r["날짜"], "volume": r["거래량"],
                        "institution_net": r["기관순매수"], "foreign_net": r["외국인순매수"],
                        "foreign_pct": r["외국인보유율"],
                    })
            time.sleep(0.3)  # 153번 연속 요청이라 너무 몰아치지 않게 짧은 지연
            if (i + 1) % 30 == 0:
                print(f"   {i + 1}/{len(codes)}...")
        print(f"   조회 완료: {len(flow_records)}행, 실패 {len(flow_failed)}종목")
        if flow_failed:
            names = {r["stock_code"]: r["stock_name"] for r in watchlist}
            print(f"   실패 종목: {[names.get(c, c) for c in flow_failed]}")

        if flow_records:
            print(f"5) investor_flow에 {len(flow_records)}건 upsert...")
            n = upsert("investor_flow", flow_records, "stock_code,trade_date", chunk_size=500)
            print(f"   완료: {n}건")
    except Exception as e:
        print(f"   경고: 외국인/기관 수급 적재 단계에서 문제가 발생해 이 단계만 건너뜁니다: {e}")

    try:
        print("6) 시장 전체(코스피/코스닥) 거래량+수급 조회...")
        market_records = []
        for market in ("KOSPI", "KOSDAQ"):
            rows = core.fetch_market_flow(market)
            print(f"   {market}: {len(rows)}행")
            for r in rows:
                market_records.append({
                    "market": market, "trade_date": r["날짜"], "volume": r["거래량"],
                    "individual_net": r["개인순매수"], "foreign_net": r["외국인순매수"],
                    "institution_net": r["기관순매수"],
                })

        if market_records:
            print(f"7) market_flow에 {len(market_records)}건 upsert...")
            n = upsert("market_flow", market_records, "market,trade_date")
            print(f"   완료: {n}건")
    except Exception as e:
        print(f"   경고: 시장 전체 수급 적재 단계에서 문제가 발생해 이 단계만 건너뜁니다: {e}")


if __name__ == "__main__":
    main()
