"""
watchlist 테이블(Supabase)의 전 종목(2026-10 기준 181개) 시세/거래량/수급을 조회해서 오늘자로 적재.
같은 날 여러 번 실행해도 안전(각 테이블이 stock_code+trade_date 또는 market+trade_date
unique라 upsert로 덮어씀). GitHub Actions cron이 매일 이 스크립트를 실행한다
(.github/workflows/daily-price-fetch.yml).

2026-08-24에 거래량/수급(외국인·기관 순매수, 코스피·코스닥 시장 전체 베이스라인) 적재를
추가함 — 당시 153개 전체 watchlist 종목 대상(51개 보유종목만이 아니라, 신규 편입 후보 판단
근거로도 쓰려는 목적). fetch_investor_flow는 종목 하나당 페이지 하나씩 긁는 비공식
스크레이핑이라 종목 수만큼 순차 요청함 — 너무 몰아치지 않게 요청 사이 짧은 지연을 둠.

접속 정보는 로컬에서는 .streamlit/secrets.toml, GitHub Actions에서는 환경변수
(SUPABASE_URL, SUPABASE_ANON_KEY, 레포 secrets에 등록됨)에서 읽는다.
"""
import os
import time
import tomllib
from datetime import date, timedelta
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
    # 휴장일 달력(2026-10-21): 오늘이 휴장일(주말 포함)이고 장 시간대에 돈 거면 적재 없이 종료.
    # resolve_trading_date가 이제 휴장일을 건너뛰어 직전 거래일을 주므로, 이 막이가 없으면 휴장일 cron이
    # 직전 거래일 값을 같은 날짜로 한 번 더 덮어쓴다(무해하지만 쓸데없음). 자정 넘어 지연된 실행(9시 전)은 막지 않는다.
    if core.is_krx_closed(core.today_kst_str()) and core.now_kst().hour >= 9:
        print(f"{core.today_kst_str()}은 휴장일(krx_holidays.csv·주말)이라 적재하지 않고 종료합니다.")
        return
    today = core.resolve_trading_date()
    traded = core.market_traded_on(today)
    if traded is False:
        print(f"{today}은 휴장일(마지막 체결이 그 전 거래일)이라 적재하지 않고 종료합니다.")
        return
    if traded is None:
        print(f"경고: {today} 개장 여부 확인 실패 — 거래일로 보고 그대로 적재합니다.")

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

    # @뇌 시세_레코드
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

    # @뇌 외인수급_단계
    # 아래 두 단계(4~7)는 2026-08-24 신규 추가라, DB 마이그레이션이 아직 안 됐거나
    # 스크레이핑이 실패해도 위의 핵심 시세 적재(1~3)는 절대 안 깨지게 통째로 감싼다.
    try:
        print(f"4) 종목별 외국인/기관 수급 조회 ({len(codes)}종목, 종목당 최근 5영업일 — 모바일 API 한계)...")
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
            time.sleep(0.3)  # 종목 수만큼 연속 요청이라 너무 몰아치지 않게 짧은 지연
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

    try:
        fill_recent_gaps(watchlist, today)
    except Exception as e:
        print(f"   경고: 빠진 날 메우기 단계에서 문제가 발생해 이 단계만 건너뜁니다: {e}")


GAP_LOOKBACK = 5        # 오늘 이전 최근 몇 거래일까지 빈 날을 찾아 메울지
GAP_MIN_RATIO = 0.9     # 그날 적재 종목 수가 watchlist의 이 비율 미만이면 "빠진 날"로 본다


def fill_recent_gaps(watchlist: list[dict], today: str) -> None:
    """cron이 하루 안 돌았거나 일부만 들어간 날을 다음 실행 때 저절로 메운다(2026-10-13, 튜즈데이
    load_daily.py의 "최근 N거래일 중 빠진 날만" 방식을 가져옴 — 국장은 8/27 cron 미실행으로 하루가
    영구 공백이 된 전례가 있음).

    - 거래일 목록은 코스피 일별시세에 실제로 있는 날짜로 정한다(한국 휴장일 목록 하드코딩 불필요).
    - price_history: 그날 적재 수가 watchlist의 90% 미만이면 빠진 종목만 일별시세 API로 그날 확정 종가·
      거래량을 받아 넣는다(실시간 시세 API는 오늘 값만 줘서 과거 날짜엔 못 씀). 거래정지 한두 종목처럼
      매일 정상적으로 빠지는 경우는 90% 문턱 아래로 안 내려가서 건드리지 않는다.
    - market_flow: 그날 KOSPI/KOSDAQ 행이 없으면 날짜 지정으로 다시 받는다.
    - investor_flow는 4단계가 종목마다 최근 5영업일치를 통째로 upsert하므로 이미 저절로 메워진다.
      그 API가 5영업일치만 줘서, 5영업일 넘게 cron이 멈추면 외인 수급은 이 방법으로도 못 메운다.
    """
    print(f"8) 최근 {GAP_LOOKBACK}거래일 빈 날 점검...")
    start = (date.fromisoformat(today) - timedelta(days=GAP_LOOKBACK * 2 + 10)).isoformat()
    days = [r["날짜"] for r in core.fetch_daily_price_history("KOSPI", start, today) if r["날짜"] < today]
    days = days[-GAP_LOOKBACK:]
    if not days:
        print("   거래일 목록을 못 받아 건너뜁니다.")
        return

    codes = [w["stock_code"] for w in watchlist]
    need = int(len(codes) * GAP_MIN_RATIO)
    gaps: dict[str, set[str]] = {}
    for d in days:
        have = {r["stock_code"] for r in rest_get(f"price_history?select=stock_code&trade_date=eq.{d}")}
        if len(have) < need:
            gaps[d] = set(codes) - have
            print(f"   price_history {d}: {len(have)}/{len(codes)} — 메울 대상 {len(gaps[d])}종목")
    if gaps:
        lo = (date.fromisoformat(min(gaps)) - timedelta(days=10)).isoformat()
        hi = max(gaps)
        records = []
        for code in sorted(set().union(*gaps.values())):
            hist = core.fetch_daily_price_history(code, lo, hi)
            for prev, cur in zip(hist, hist[1:]):
                if cur["날짜"] in gaps and code in gaps[cur["날짜"]]:
                    pct = (cur["종가"] - prev["종가"]) / prev["종가"] * 100 if prev["종가"] else 0.0
                    records.append({"stock_code": code, "trade_date": cur["날짜"], "close_price": cur["종가"],
                                    "change_pct": round(pct, 2), "volume": cur["거래량"]})
            time.sleep(0.2)
        if records:
            n = upsert("price_history", records, "stock_code,trade_date", chunk_size=500)
            print(f"   price_history 메움: {n}건")

    mf_records = []
    for d in days:
        have = {r["market"] for r in rest_get(f"market_flow?select=market&trade_date=eq.{d}")}
        for market in ("KOSPI", "KOSDAQ"):
            if market in have:
                continue
            for r in core.fetch_market_flow(market, d):
                mf_records.append({"market": market, "trade_date": r["날짜"], "volume": r["거래량"],
                                   "individual_net": r["개인순매수"], "foreign_net": r["외국인순매수"],
                                   "institution_net": r["기관순매수"]})
    if mf_records:
        n = upsert("market_flow", mf_records, "market,trade_date")
        print(f"   market_flow 메움: {n}건")
    if not gaps and not mf_records:
        print(f"   {days[0]}~{days[-1]} 빈 날 없음")


if __name__ == "__main__":
    main()
