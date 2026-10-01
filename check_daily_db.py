"""Supabase 일일 적재 점검 — 세션 시작 시 "어제(직전 거래일)치가 다 들어왔나" 보고용.

watchlist 종목 수와 비교해서 price_history / investor_flow / market_flow의 최근 거래일별
적재 건수를 출력한다. 빠진 종목이 있으면 종목명과 그 종목의 마지막 적재일·최근 거래량을 같이
보여준다(거래정지 종목은 네이버가 거래량을 "-"로 줘서 investor_flow 행이 정상적으로 빠진다).

사용: python check_daily_db.py [일수(기본 5)]
"""
import sys
import tomllib
from collections import Counter

import requests

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 5

sec = tomllib.load(open(".streamlit/secrets.toml", "rb"))["supabase"]
URL = sec["url"]
KEY = sec.get("anon_key") or sec.get("key")
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}"}


def fetch_all(table, select, **params):
    rows, off = [], 0
    while True:
        r = requests.get(f"{URL}/rest/v1/{table}", params={"select": select, **params},
                         headers={**H, "Range": f"{off}-{off + 999}"}, timeout=20)
        r.raise_for_status()
        chunk = r.json()
        rows += chunk
        if len(chunk) < 1000:
            return rows
        off += 1000


watch = {w["stock_code"]: w["stock_name"] for w in fetch_all("watchlist", "stock_code,stock_name")}
print(f"watchlist {len(watch)}종목")

from datetime import date, timedelta
_from = (date.today() - timedelta(days=DAYS * 3 + 10)).isoformat()
recent = sorted({r["trade_date"] for r in fetch_all(
    "price_history", "trade_date", trade_date=f"gte.{_from}")}, reverse=True)[:DAYS]
since = min(recent)

for table in ("price_history", "investor_flow"):
    rows = fetch_all(table, "stock_code,trade_date", trade_date=f"gte.{since}")
    cnt = Counter(r["trade_date"] for r in rows)
    print(f"\n[{table}]")
    for d in sorted(recent, reverse=True):
        print(f"  {d}  {cnt.get(d, 0)}/{len(watch)}")
    latest = max(recent)
    have = {r["stock_code"] for r in rows if r["trade_date"] == latest}
    for code in sorted(set(watch) - have):
        last = requests.get(f"{URL}/rest/v1/{table}", params={"select": "trade_date", "stock_code": f"eq.{code}",
                            "order": "trade_date.desc", "limit": "1"}, headers=H, timeout=20).json()
        print(f"  빠짐({latest}): {watch[code]}({code}) 마지막 적재 {last[0]['trade_date'] if last else '없음'}")

mf = fetch_all("market_flow", "market,trade_date,volume,foreign_net", trade_date=f"gte.{since}")
print("\n[market_flow]")
for d in sorted(recent, reverse=True):
    ms = sorted(m["market"] for m in mf if m["trade_date"] == d)
    print(f"  {d}  {', '.join(ms) if ms else '없음'}")
