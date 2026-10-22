"""픽 추적 — 새러데이가 추천한 종목을 날짜로 자르지 않고 경로 전체로 따라간다 (2026-10-16 대표 지시).

대표 철학(유니버스 3층): 1층 외인·기관이 모으고 → 2층 new1이 그 흔적이 쌓인 *뒤* 더 좋은 자리를 선점한다.
그래서 픽의 결과는 3일 뒤가 아니라 10일·20일 뒤에 드러날 수 있다. 픽 이후 경로를 매일 쌓는다.
(2026-10-19 대표 확정: 경로는 그대로 두고, 성적은 picks.py score가 종목별 체크포인트(단기·중기·장기 = 그 종목 평소 파도 길이, 기록 순간 고정)에서 두 기준으로 따로 매긴다.
 10-16에 적었던 "+N일 고정 채점 대신 경로만"은 대체됨 — 대표 말 "기간에 따라 뒤집힌다"를 채점 폐지로 넓게 읽었던 것.)

    python tracking.py           # 카드 갱신(tracking/카드별 CSV + tracking/summary.csv) + 요약 출력
    python tracking.py 파마리서치  # 그 카드 경로 전체

카드 = 종목 하나. picks_log.csv(원본)에서 같은 종목 추천을 합치고, 기준 = 최초 픽(날짜·추천가·벤치).
카드는 경로·외인 상태 보기다. 회차별 성적은 picks.py score가 줄마다(그 줄의 추천가·날짜·벤치·고정 기간) 따로 매긴다 —
카드 요약 "초과%p"를 나중 회차의 성적으로 읽지 않는다.
매일 한 줄: 종가(오늘은 실시간)·수익%·혼합지수(ex) 대비 초과%p·외인 보유율.
요약: 지금 초과 · 최고/최저 초과(MFE/MAE) · 처음 벤치를 이긴 날(픽 후 며칠째) · 외인(1층) 상태 · 카드 상태.

외인(1층) 상태 — 2층 논리는 1층이 받쳐 줄 때만 성립한다. 픽 이후 외인이 '세게' 이탈해야 경고(대표: 이탈 강도가 어느 정도 세야 함).
- 기준 보유율 = 그 종목 가격추적 시작일(Fishing·Link와 같은 기준일) 값. 1층이 쌓은 양 = 픽 이후 고점 − 기준.
- 이탈 강도 z = (픽 이후 고점 − 지금) ÷ (하루 변화 σ × √고점 이후 거래일수). σ는 그 종목 전 기간.
- 반납률 = (픽 이후 고점 − 지금) ÷ (픽 이후 고점 − 기준) × 100.
- 근거 사라짐: z ≥ 2 그리고 반납률 ≥ 50% (둘 다). 주의: z ≥ 1.5 또는 반납률 ≥ 30%. 그 밖: 모으는 중(픽 이후 +) / 유지.
  외인 이탈은 그 픽이 "틀렸다"는 판정이 아니라 "추천 근거(1층)가 사라졌다"는 표시다(2026-10-19 대표 확정, 10-16 공동 계획 2-3).
  성적(맞음·틀림)은 picks.py score의 종목별 단기·중기·장기 체크포인트에서 따로 매긴다. 예전 이름 "경고(이탈)"은 대체됨.
카드 상태(날짜가 아니라 사건으로): 추적 → 진입(new1이 픽 이후 매수) → 졸업(그 뒤 매도로 실현손익 +) / ·근거 사라짐 / 종료(tracking/closed.csv에 대표가 넣은 종목).
"""
import math
import sys
from pathlib import Path

import pandas as pd

import portfolio_core as core
import picks

HERE = Path(__file__).parent
DIR = HERE / "tracking"
SUMMARY = DIR / "summary.csv"
CLOSED = DIR / "closed.csv"
Z_WARN, GIVE_WARN, Z_CAUTION, GIVE_CAUTION = 2.0, 50.0, 1.5, 30.0


def _cards(log: pd.DataFrame) -> list[dict]:
    out = []
    for c, g in log.sort_values(["날짜", "시각"]).groupby("종목코드", sort=False):
        f = g.iloc[0]
        out.append({"종목코드": c, "종목명": f["종목명"], "픽일": f["날짜"], "픽가": float(f["추천가"]),
                    "벤치K": f["벤치K"], "벤치Q": f["벤치Q"], "픽횟수": len(g),
                    "이력": " / ".join(f'{r["날짜"][5:]} {r["메모"].split(" 소급")[0]} {r["구분"]}' for _, r in g.iterrows()),
                    "근거": f["근거"]})
    return out


def _foreign_state(fl: pd.DataFrame, base_date: str, pick_date: str) -> dict:
    """fl = 그 종목 investor_flow(날짜순). 기준·픽·고점·지금 보유율과 이탈 강도."""
    if fl.empty:
        return {"외인상태": "기록 없음"}
    s = fl.set_index("날짜")["외국인보유율"].astype(float)
    base = s[s.index <= base_date].iloc[-1] if (s.index <= base_date).any() else s.iloc[0]
    at_pick = s[s.index <= pick_date].iloc[-1] if (s.index <= pick_date).any() else s.iloc[0]
    after = s[s.index >= pick_date]
    if after.empty:
        after = s.tail(1)
    hi, hi_d, now = float(after.max()), after.idxmax(), float(s.iloc[-1])
    n = max(int((s.index > hi_d).sum()), 1)
    sd = float(s.diff().dropna().std()) if len(s) > 2 else float("nan")
    drop = hi - now
    z = drop / (sd * math.sqrt(n)) if sd and sd == sd and sd > 0 else float("nan")
    acc = hi - base
    give = drop / acc * 100 if acc > 0 else float("nan")
    if z == z and z >= Z_WARN and give == give and give >= GIVE_WARN:
        st = "근거 사라짐"
    elif (z == z and z >= Z_CAUTION) or (give == give and give >= GIVE_CAUTION):
        st = "주의"
    elif now > at_pick:
        st = "모으는 중"
    else:
        st = "유지"
    return {"외인기준": base, "외인픽": at_pick, "외인고점": hi, "외인지금": now, "외인픽후%p": now - at_pick,
            "이탈z": z, "반납%": give, "외인상태": st, "외인최신일": s.index[-1]}


def _card_state(name: str, pick_date: str, tx: pd.DataFrame, closed: set, fstate: str) -> str:
    if name in closed:
        return "종료"
    t = tx[(tx["종목명"] == name) & (tx["날짜"] >= pick_date)]
    buys, sells = t[t["구분"] == "매수"], t[t["구분"] == "매도"]
    state = "추적"
    if not buys.empty:
        state = "진입"
        later = sells[sells["날짜"] >= buys["날짜"].min()]
        if not later.empty and pd.to_numeric(later["실현손익"], errors="coerce").sum() > 0:
            state = "졸업"
    if fstate == "근거 사라짐" and state != "졸업":
        state += "·근거 사라짐"
    return state


def update(data: dict | None = None) -> pd.DataFrame:
    log = picks.load_log()
    if log.empty:
        return log
    if data is None:
        import daily_link_report as link
        data = link.load_data(drop_ended=False)
    ph, fh, live = data["ph"], data["fh"], data["live"]
    closes, now = picks.bench_levels(live=True)
    wk = picks._wk()
    today = core.today_kst_str()
    tx = core.load_transactions()
    closed = set(pd.read_csv(CLOSED)["종목명"]) if CLOSED.exists() else set()
    DIR.mkdir(exist_ok=True)

    def bret(b0, b1):
        return (wk * (b1[0] / b0[0] - 1) + (1 - wk) * (b1[1] / b0[1] - 1)) * 100

    rows = []
    for cd in _cards(log):
        c, d0, p0 = cd["종목코드"], cd["픽일"], cd["픽가"]
        b0 = (float(cd["벤치K"]), float(cd["벤치Q"])) if pd.notna(cd["벤치K"]) else closes.get(d0)
        g = ph[ph["종목코드"].astype(str) == c].sort_values("날짜")
        path = {r["날짜"]: float(r["종가"]) for _, r in g[g["날짜"] >= d0].iterrows()}
        if live.get(c) and core.market_traded_on(today) is not False:
            path[today] = float(live[c])
        fl = fh[fh["종목코드"].astype(str) == c].sort_values("날짜")
        fmap = dict(zip(fl["날짜"], fl["외국인보유율"].astype(float)))
        lines = []
        for d in sorted(path):
            b1 = now if (d == today and now) else closes.get(d)
            ex = (path[d] / p0 - 1) * 100 - bret(b0, b1) if (b0 and b1) else float("nan")
            lines.append({"날짜": d, "가격": path[d], "수익%": (path[d] / p0 - 1) * 100, "초과%p": ex, "외인보유율": fmap.get(d)})
        pl = pd.DataFrame(lines)
        pl.to_csv(DIR / f"{cd['종목명'].replace('/', '_')}_{c}.csv", index=False, encoding="utf-8-sig")
        exs = pl["초과%p"].dropna() if not pl.empty else pd.Series(dtype=float)
        win = pl[pl["초과%p"] > 0] if not pl.empty else pl
        base_d = g["날짜"].min() if not g.empty else d0
        fs = _foreign_state(fl, base_d, d0)
        rows.append({**{k: cd[k] for k in ("종목명", "종목코드", "픽일", "픽가", "픽횟수", "근거", "이력")},
                     "지금가": pl["가격"].iloc[-1] if not pl.empty else None,
                     "수익%": pl["수익%"].iloc[-1] if not pl.empty else None,
                     "초과%p": exs.iloc[-1] if not exs.empty else None,
                     "최고초과%p": exs.max() if not exs.empty else None, "최저초과%p": exs.min() if not exs.empty else None,
                     "첫승일": win["날짜"].iloc[0] if not win.empty else "",
                     "첫승까지": (int((pl["날짜"] <= win["날짜"].iloc[0]).sum()) - 1) if not win.empty else None,
                     "추적일수": max(len(pl) - 1, 0), **fs,
                     "상태": _card_state(cd["종목명"], d0, tx, closed, fs.get("외인상태", ""))})
    sm = pd.DataFrame(rows)
    # 관심 끝 종목은 트래킹을 계속하되 이름 앞에 [관심 끝] 태그(대표 2026-10-22 "트래킹은 하지, 앞에 태그를 붙여서").
    # 종목명 칸은 그대로 두고(카드 파일 이름·조회 키) 표시용 '표시명' 칸을 따로 둔다.
    _ended = core.ended_watch_names()
    if not sm.empty:
        sm.insert(0, "표시명", ["[관심 끝] " + n if n in _ended else n for n in sm["종목명"]])
    sm.to_csv(SUMMARY, index=False, encoding="utf-8-sig")
    return sm


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    sm = update()
    if sm.empty:
        print("추적 중인 픽 없음")
        return 0
    pd.set_option("display.width", 250)
    pd.set_option("display.max_rows", 300)
    if len(sys.argv) > 1:
        r = sm[sm["종목명"] == " ".join(sys.argv[1:])]
        if r.empty:
            print("그런 카드 없음")
            return 1
        r = r.iloc[0]
        print(r.to_string())
        print(pd.read_csv(DIR / f"{r['종목명']}_{r['종목코드']}.csv").round(2).to_string(index=False))
        return 0
    cols = ["표시명", "픽일", "픽횟수", "픽가", "지금가", "수익%", "초과%p", "최고초과%p", "최저초과%p", "첫승까지",
            "외인픽후%p", "이탈z", "반납%", "외인상태", "상태"]
    print(sm[[c for c in cols if c in sm]].round(2).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
