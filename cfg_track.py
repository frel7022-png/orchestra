"""CFG 추적 — 대표가 "외인 리포트 보고 골랐어"라고 한 종목을 매수 → 물타기 → 마지막 매도까지 사이클째로 따라간다
(2026-10-20 대표: "내가 골랐다고 하면 넌 그게 매수부터 물타고 마지막 나오는 싸이클까지 관리하는 저장 즉 트랙킹").

cfg_tagged.csv 한 줄 = 카드 하나. 태그일이 들어 있는 사이클(최초매수일 ≤ 태그일, 아직 보유 중이거나 태그일 이후 전량매도)을 찾아
요약 한 줄(cfg_track/summary.csv)과 사건 목록(cfg_track/events.json — 매수·물타기·매도마다 그날 외인 보유율)을 다시 만든다.
끝난 사이클은 실현률로, 보유 중이면 지금 평가로 — 열린 사이클은 "지금까지"일 뿐 맞다·틀리다로 확정하지 않는다.
외인 보유율은 investor_flow(Supabase). 시크릿·네트워크가 없으면 외인 칸만 비운다.

사용: python cfg_track.py
"""
import json
import sys
import tomllib
from pathlib import Path

import pandas as pd

import portfolio_core as core

HERE = Path(__file__).parent
OUT = HERE / "cfg_track"


def _flow():
    try:
        sec = tomllib.load(open(HERE / ".streamlit/secrets.toml", "rb"))["supabase"]
        return core.load_investor_flow_db(sec["url"], sec.get("anon_key") or sec.get("key"))
    except Exception:
        return pd.DataFrame()


def _fpct(fh: pd.DataFrame, name: str, day: str | None) -> float | None:
    """day 당일(없으면 그 이전 가장 가까운 날) 외인 보유율. day=None이면 마지막 값."""
    if fh is None or fh.empty:
        return None
    g = fh[fh["종목명"] == name].sort_values("날짜")
    if day is not None:
        g = g[g["날짜"] <= day]
    if g.empty:
        return None
    v = pd.to_numeric(g.iloc[-1]["외국인보유율"], errors="coerce")
    return None if pd.isna(v) else float(v)


def cycle_rows(tx: pd.DataFrame, name: str, tag_day: str) -> pd.DataFrame:
    """name의 거래 중 tag_day가 들어 있는 사이클의 매수·매도 행(날짜·입력순)."""
    t = tx[(tx["종목명"] == name) & tx["구분"].isin(["매수", "매도"])].copy()
    t["_ord"] = range(len(t))
    t = t.sort_values(["날짜", "_ord"])
    qty, cur, found = 0.0, [], None
    for _, r in t.iterrows():
        cur.append(r)
        qty += float(r["수량"]) if r["구분"] == "매수" else -float(r["수량"])
        if qty <= 1e-9:
            if cur[0]["날짜"] <= tag_day <= r["날짜"]:
                found = cur
                break
            cur, qty = [], 0.0
    if found is None and cur and cur[0]["날짜"] <= tag_day:
        found = cur
    return pd.DataFrame(found) if found else pd.DataFrame()


def track_one(tx, holdings, fh, name, tag_day, memo):
    c = cycle_rows(tx, name, tag_day)
    if c.empty:
        return None, []
    c["수량"] = pd.to_numeric(c["수량"]); c["단가"] = pd.to_numeric(c["단가"])
    c["실현손익"] = pd.to_numeric(c["실현손익"], errors="coerce").fillna(0.0)
    buys, sells = c[c["구분"] == "매수"], c[c["구분"] == "매도"]
    held = buys["수량"].sum() - sells["수량"].sum()
    closed = held <= 1e-9
    tag_buys = buys[buys["날짜"] == tag_day]
    tag_px = (tag_buys["수량"] * tag_buys["단가"]).sum() / tag_buys["수량"].sum() if not tag_buys.empty else None
    buy_amt = float((buys["수량"] * buys["단가"]).sum())
    end_day = str(c["날짜"].iloc[-1]) if closed else None
    row = {
        "종목명": name, "태그일": tag_day, "메모": memo,
        "사이클시작": str(buys["날짜"].iloc[0]), "최초진입가": float(buys["단가"].iloc[0]),
        "태그매수가": tag_px, "매수횟수": len(buys), "태그뒤_물타기": int((buys["날짜"] > tag_day).sum()),
        "매도횟수": len(sells), "상태": "끝" if closed else "보유중", "종료일": end_day, "매수액": round(buy_amt),
        "실현손익": round(float(sells["실현손익"].sum())),
    }
    if closed:
        row["결과%"] = round(row["실현손익"] / buy_amt * 100, 2) if buy_amt else None
        sell_avg = (sells["수량"] * sells["단가"]).sum() / sells["수량"].sum()
        row["태그가대비%"] = round((sell_avg / tag_px - 1) * 100, 2) if tag_px else None
    else:
        h = holdings[holdings["종목명"] == name]
        if not h.empty:
            cur, avg = float(h.iloc[0]["현재가"]), float(h.iloc[0]["평단가"])
            row["평단가"], row["현재가"] = avg, cur
            row["결과%"] = round((cur / avg - 1) * 100, 2) if avg else None
            row["태그가대비%"] = round((cur / tag_px - 1) * 100, 2) if tag_px else None
    f0, f1 = _fpct(fh, name, tag_day), _fpct(fh, name, end_day)
    row["외인_태그일"], row["외인_지금또는종료"] = f0, f1
    row["외인변화pp"] = round(f1 - f0, 2) if f0 is not None and f1 is not None else None
    events = [{"날짜": str(r["날짜"]), "구분": r["구분"], "수량": float(r["수량"]), "단가": float(r["단가"]),
               "외인보유율": _fpct(fh, name, str(r["날짜"]))} for _, r in c.iterrows()]
    return row, events


def build(fh=None):
    tx = core.load_transactions()
    holdings = core.load_holdings()
    tags = core.load_cfg_tags()
    fh = _flow() if fh is None else fh
    rows, ev = [], {}
    for _, t in tags.iterrows():
        row, events = track_one(tx, holdings, fh, t["종목명"], str(t["태그일"]), t.get("메모", ""))
        if row:
            rows.append(row)
            ev[f'{t["종목명"]}|{t["태그일"]}'] = events
    OUT.mkdir(exist_ok=True)
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "summary.csv", index=False, encoding="utf-8-sig")
    (OUT / "events.json").write_text(json.dumps(ev, ensure_ascii=False, indent=1), encoding="utf-8")
    return df


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    df = build()
    cols = ["종목명", "태그일", "상태", "매수횟수", "태그뒤_물타기", "결과%", "태그가대비%", "외인변화pp"]
    print(df[[c for c in cols if c in df.columns]].to_string(index=False))
