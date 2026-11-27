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

# 출력을 파일 · 파이프로 받으면 Windows 기본 cp949라 "—" · "⚠️"에서 UnicodeEncodeError로 멈춘다(터미널에선 안 남).
# 예약 작업이 로그로 받아도 끝까지 찍히게 utf-8로 고정(2026-11-27 17시 보고 물을 것 2 — picks.py main과 같은 꼴).
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 휴장일 달력 경고(2026-10-21 위크) — 달력 확인이 사람 기억에만 달리지 않게 매일 점검 첫머리에 찍는다.
import portfolio_core as core  # noqa: E402
for _w in core.krx_calendar_warnings():
    print(_w)

# 히스토리 다섯 · 픽 추적 · 15:35 실패 로그(2026-10-30 위크 편지 B1·B2) — 무인으로 도는 일이 멈춰도 다음 아침에 보이게.
import os  # noqa: E402
from datetime import date as _date, datetime as _dt, timedelta as _td  # noqa: E402
_al = core.check_history_alignment()
print(f"[히스토리 다섯 OK] 모두 {_al['latest']}" if _al["aligned"]
      else "[⚠️ 히스토리 다섯] " + " · ".join(f"{k} {v}" for k, v in _al["maxes"].items()) + " — 뒤처짐: " + ", ".join(_al["behind"]))
_today, _now = core.today_kst_str(), core.now_kst()
_ts = core.HERE / "tracking" / "summary.csv"
_tsd = _dt.fromtimestamp(os.path.getmtime(_ts)).strftime("%Y-%m-%d") if _ts.exists() else None
print(core.pick_track_line(_today, (_now.hour, _now.minute), _tsd))
# 전날 HISTORY · 두 계좌 sync(2026-11-27 9시 보고 B 10번 칸 — 11-25 · 26에 둘 다 빈 채 지나감 · 대표 부재 위임 11-27 17:02:59).
print(core.prev_history_line(_today, core.HERE / "뇌_초안" / "HISTORY"))
_ba = core.HERE / "both_accounts.csv"
_ba_rows = [l.split(",", 1)[0] for l in (_ba.read_text(encoding="utf-8-sig").splitlines()[1:] if _ba.exists() else []) if l.strip()]
print(core.both_accounts_sync_line(_ba_rows[-1] if _ba_rows else None, _al["latest"]))
_fl = core.HERE / "logs" / "daily_close_fail.log"
_since = (_date.fromisoformat(_today) - _td(days=7)).isoformat()
_fails = [l.strip() for l in (_fl.read_text(encoding="utf-8").splitlines() if _fl.exists() else []) if l.strip()[:10] >= _since]
_big_fix = [l for l in _fails if "대형주 전날 줄" in l or "fix_prev_bigcap" in l]   # 09:25 대형주 전날 줄(대표 「어제 종가를 못가져오면 보고해.」)
_fails = [l for l in _fails if l not in _big_fix]
print("[15:35 실패 없음(최근 7일)]" if not _fails else "[⚠️ 15:35 실패] " + " | ".join(_fails[-3:]))
print("[09:25 대형주 전날 줄 OK(최근 7일)]" if not _big_fix else "[⚠️ 09:25 대형주 전날 줄 못 맞춤 — 위크에게] " + " | ".join(_big_fix[-3:]))

# 금요일 걸음 셋(2026-11-25 대표 13:12:54 「위크 추천대로」 — 11-24 보고 고를 것 4 · 시뮬레이션 07 13번):
# 주간_리포트 걸음 2(picks.py score · cfg_track.py · pick_cases.py build)는 세션 기억에 달려 10-30 · 11-06 · 11-13 · 11-20 네 번 빠졌다.
# 셋 다 돌 때마다 파일을 새로 쓰니 수정 시각으로 잰다. 기준 = 오늘 전 마지막 금요일(금요일 당일엔 그 전 금요일 — 걸음이 장 마감 뒤라서).
print(core.friday_steps_line(_today, {
    "picks_score": core.HERE / "picks_score.csv",
    "cfg_track": core.HERE / "cfg_track" / "summary.csv",
    "pick_cases": core.HERE / "pick_cases" / "index.csv",
}))

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


try:
    _wl = fetch_all("watchlist", "stock_code,stock_name,ended_on")
except requests.HTTPError:   # ended_on 칸이 아직 없으면(마이그레이션 전) 예전처럼
    _wl = fetch_all("watchlist", "stock_code,stock_name")
watch = {w["stock_code"]: w["stock_name"] for w in _wl}
ended = {w["stock_code"]: w["ended_on"] for w in _wl if w.get("ended_on")}
# 관심 끝 종목도 cron이 계속 쌓는다(대표 2026-10-22 (가) ⓐ) — 그래서 분모는 전체. 관심 끝은 꼬리표로만.
print(f"watchlist {len(watch)}종목 (관심 중 {len(watch) - len(ended)} · 관심 끝 {len(ended)})"
      + ("" if not ended else " — 관심 끝: " + ", ".join(f"{watch[c]}(관심 끝 {d})" for c, d in sorted(ended.items()))))

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
        tag = f" (관심 끝 {ended[code]})" if code in ended else ""
        print(f"  빠짐({latest}): {watch[code]}({code}){tag} 마지막 적재 {last[0]['trade_date'] if last else '없음'}")

mf = fetch_all("market_flow", "market,trade_date,volume,foreign_net", trade_date=f"gte.{since}")
print("\n[market_flow]")
for d in sorted(recent, reverse=True):
    ms = sorted(m["market"] for m in mf if m["trade_date"] == d)
    print(f"  {d}  {', '.join(ms) if ms else '없음'}")
