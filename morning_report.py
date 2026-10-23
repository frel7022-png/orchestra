"""아침 리포트 3종 — 버전 1 (2026-10-02).

  ① 액션 리포트   morning_report/action/YYYY-MM-DD.html (+ latest_action.html)
     계좌 vs 혼합지수, Up/Down 재진입 후보, Watering Detect 물타기 후보, Fishing 하락 흐름, Basecamp 매집 후보,
     Today's Alarm, meritz 진입 후보. 각 칸 상위 10개(고정 기준이 아니라 우선순위), 행마다 그래프.
  ② 외인 샘플 리포트 link_sample/ (daily_link_report.py — 같은 데이터로 같이 돌린다)
  ③ CFG 리포트     morning_report/cfg/YYYY-MM-DD.html (+ latest_cfg.html)
     ①의 후보 중, 그 하락 구간 동안 외국인이 실제로 모은 종목만. 드문 게 정상.
  ④ Basecamp 리포트 morning_report/basecamp/YYYY-MM-DD.html (+ latest_basecamp.html, 2026-10-15 신설)
     ①의 Basecamp 칸을 상위 20개로 넓히고, 전일 대비 순위·신규/탈락, 거래량 막대, 받는손(외인/기관)까지.
     외인 정보가 들어가므로 ①→②→③ 다음 마지막에 읽는다.

읽는 순서는 ①→②→③(사용자 지시 — 먼저 스스로 판단한 뒤 세션 결론을 본다). ①에는
외인 분석(외인 변화율·비중 등)을 쓰지 않는다 — 역할 분리: ① 외인 없는 분석 / ② 외인 분석 / ③ CFG = 둘 다에서 나온
종목이 강한 신호(대표 2026-10-21). 외인 데이터가 든 셀을 가져와 보는 건 허용(쓰지 않는 것이지 보지 말라는 게 아님). 리포트는 그날 팩트만 쓴다.
총평은 세션이 morning_report/comments/YYYY-MM-DD_action.html, _cfg.html 에 써 두면 들어간다.

사용: python morning_report.py
"""
import html
from pathlib import Path

import pandas as pd

import basecamp
import daily_link_report as link
import live_check
import portfolio_core as core

HERE = Path(__file__).parent
OUT = HERE / "morning_report"
TOP = 10
MERITZ_ENTRY_LOSS = -5.0  # meritz 진입 후보: new1 평단 대비 평가손익 ≤ −5% (대표 확정 2026-10-22, 데이터센터/세팅)
MERITZ_NEAR_N = 5         # 걸린 게 없을 때 가까운 순으로 채우는 수(추천 기본 5개와 맞춤)
BC_TOP = 20   # ④ Basecamp 리포트 명단 길이
FAST_SHARE = 0.5   # 총 하락의 절반 이상이 최근 3거래일에 났으면 "급락"
esc = html.escape


# ------------------------------------------------------------------ 공통 도우미
def sign(v, d=1, unit="%"):
    if v is None or pd.isna(v):
        return '<span class="mut">-</span>'
    cls = "up" if v > 0 else ("dn" if v < 0 else "")
    return f'<span class="{cls}">{v:+.{d}f}{unit}</span>'


def series_from(ph, code, start, live_px, today):
    """price_history 종가(start 이후) + 오늘 실시간가. [(날짜, 가격)]"""
    g = ph[(ph["종목코드"] == code) & (ph["날짜"] >= start)].sort_values("날짜")
    pts = list(zip(g["날짜"], g["종가"].astype(float)))
    if live_px and (not pts or pts[-1][0] < today):
        pts.append((today, float(live_px)))
    return pts


def speed_badge(pts, total_pct):
    """총 하락 중 최근 3거래일 몫이 절반 이상이면 급락, 아니면 완만."""
    if total_pct is None or total_pct >= 0 or len(pts) < 2:
        return ""
    ref = pts[-4][1] if len(pts) >= 4 else pts[0][1]
    d3 = (pts[-1][1] / ref - 1) * 100
    fast = d3 <= total_pct * FAST_SHARE
    return (f'<span class="bd fast">급락 · 최근3일 {d3:+.1f}%</span>' if fast
            else f'<span class="bd slow">완만 · 최근3일 {d3:+.1f}%</span>')


def svg_line(pts, refs=(), marks=(), pct_base=None):
    """가격 선 그래프. refs=[(라벨, 값, 클래스)] 수평선, marks=[(날짜, 값, 클래스)] 점.
    pct_base가 있으면 y축 라벨을 그 값 대비 %로."""
    if len(pts) < 2:
        return '<div class="nochart">시세 데이터 부족</div>'
    W, H, L, R, T, B = 340, 150, 40, 54, 10, 22
    iw, ih = W - L - R, H - T - B
    dates = [d for d, _ in pts]
    xi = {d: i for i, d in enumerate(dates)}
    vals = [v for _, v in pts] + [v for _, v, _ in refs]
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.08 or hi * 0.02
    lo, hi = lo - pad, hi + pad
    sx = lambda d: L + iw * xi[d] / max(1, len(dates) - 1)
    sy = lambda v: T + ih * (hi - v) / (hi - lo)
    fmt = (lambda v: f"{(v / pct_base - 1) * 100:+.0f}%") if pct_base else (lambda v: f"{v:,.0f}")
    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="주가 추이">']
    for lab, v, cls in refs:
        y = sy(v)
        out.append(f'<line class="ref {cls}" x1="{L}" x2="{W - R}" y1="{y:.1f}" y2="{y:.1f}"/>')
        out.append(f'<text class="ax {cls}" x="{W - R + 3}" y="{y + 3:.1f}">{lab}</text>')
    out.append('<polyline class="pl" points="' + " ".join(f"{sx(d):.1f},{sy(v):.1f}" for d, v in pts) + '"/>')
    for d, v, cls in marks:
        if d in xi:
            out.append(f'<circle class="mk {cls}" cx="{sx(d):.1f}" cy="{sy(v):.1f}" r="3"/>')
    out += [f'<text class="ax" x="{L - 4}" y="{T + 8}" text-anchor="end">{fmt(hi)}</text>',
            f'<text class="ax" x="{L - 4}" y="{T + ih}" text-anchor="end">{fmt(lo)}</text>',
            f'<text class="ax" x="{L}" y="{H - 6}">{dates[0][5:]}</text>',
            f'<text class="ax" x="{W - R}" y="{H - 6}" text-anchor="end">{dates[-1][5:]}</text>', "</svg>"]
    return "".join(out)


def svg_vol(vols, baseline, base_start):
    """거래량 막대. vols=[(날짜, 거래량)], 평지 구간(base_start 이후)은 녹색, 하락 전 기준선·2배선 점선."""
    vols = [(d, v) for d, v in vols if v == v]
    if len(vols) < 2 or not baseline:
        return ""
    W, H, L, R, T, B = 340, 80, 40, 54, 6, 8
    iw, ih = W - L - R, H - T - B
    # 하루 튄 날이 스케일을 먹지 않게 상한 = 기준의 3배(평지가 더 크면 평지 최대), 넘는 막대는 위가 잘린다
    hi = max(baseline * 3, max((v for d, v in vols if d >= base_start), default=0)) * 1.1
    bw = iw / len(vols)
    sy = lambda v: T + ih * (1 - min(v, hi) / hi)
    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="거래량">']
    for i, (d, v) in enumerate(vols):
        cls = "vb on" if d >= base_start else "vb"
        out.append(f'<rect class="{cls}" x="{L + i * bw + bw * 0.15:.1f}" y="{sy(v):.1f}" '
                   f'width="{bw * 0.7:.1f}" height="{T + ih - sy(v):.1f}"/>')
    for lab, v, cls in (("기준", baseline, "first"), ("×2", baseline * 2, "last")):
        out.append(f'<line class="ref {cls}" x1="{L}" x2="{W - R}" y1="{sy(v):.1f}" y2="{sy(v):.1f}"/>'
                   f'<text class="ax {cls}" x="{W - R + 3}" y="{sy(v) + 3:.1f}">{lab}</text>')
    out.append(f'<text class="ax" x="{L - 4}" y="{T + 8}" text-anchor="end">거래량</text></svg>')
    return "".join(out)


def card(title, meta, chart, extra=""):
    return (f'<div class="card"><div class="ch">{title}<span class="meta">{meta}</span></div>'
            f'{chart}{extra}</div>')


CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#191b21;--soft:#5b606b;--faint:#8b909c;--rule:#e0e3ea;
--up:#c9313d;--dn:#2c5fc4;--fl:#15794c;--warn:#b4690e;--wash:#fbf0e2}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#13151a;--card:#1a1d24;--ink:#e8eaef;--soft:#a2a7b3;--faint:#6e7480;--rule:#2b2f38;--up:#f0555f;--dn:#6f9bef;--fl:#4cc38a;--warn:#e0a45e;--wash:#2a2013;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#13151a;--card:#1a1d24;--ink:#e8eaef;--soft:#a2a7b3;--faint:#6e7480;--rule:#2b2f38;--up:#f0555f;--dn:#6f9bef;--fl:#4cc38a;--warn:#e0a45e;--wash:#2a2013;color-scheme:dark}
body{background:var(--bg);color:var(--ink);font-family:"Pretendard","Apple SD Gothic Neo","Malgun Gothic",system-ui,sans-serif;margin:0;line-height:1.6}
.wrap{max-width:860px;margin:0 auto;padding:28px 16px 64px}
h1{font-size:1.5rem;margin:0} .sub{color:var(--soft);font-size:13px;margin:4px 0 0}
h2{font-size:1.05rem;margin:34px 0 6px;padding-top:14px;border-top:1px solid var(--rule)}
.scroll{overflow-x:auto} table{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}
th,td{padding:7px 8px;border-bottom:1px solid var(--rule);text-align:right;white-space:nowrap}
th{color:var(--soft);font-weight:600;font-size:12px} .l{text-align:left} td.rk{font-weight:700}
.up{color:var(--up)} .dn{color:var(--dn)} .mut{color:var(--faint)} .new{color:var(--fl);font-weight:700;font-size:11px}
.own{font-size:10px;color:var(--faint);margin-left:5px}
.bd{font-size:11px;padding:1px 7px;border-radius:999px;border:1px solid currentColor;margin-left:4px}
.fast{color:var(--up)} .slow{color:var(--soft)} .st{color:var(--fl)} .st.down{color:var(--dn)} .st.base{color:var(--warn)}
.grid2{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:10px;margin-top:10px}
.card{background:var(--card);border:1px solid var(--rule);border-radius:10px;padding:10px 10px 4px;min-width:0}
.ch{font-weight:600;font-size:13.5px} .meta{display:block;font-weight:400;font-size:11.5px;color:var(--soft)}
svg{width:100%;height:auto;display:block} .pl{fill:none;stroke:var(--dn);stroke-width:1.8} .fl{fill:none;stroke:var(--fl);stroke-width:1.8}
.ref{stroke-dasharray:4 3;stroke-width:1} .ref.sell{stroke:var(--up)} .ref.avg{stroke:var(--fl)} .ref.first{stroke:var(--faint)} .ref.last{stroke:var(--warn)} .ref.zero{stroke:var(--faint)}
.ax{font-size:9.5px;fill:var(--faint)} .ax.sell{fill:var(--up)} .ax.avg{fill:var(--fl)} .ax.last{fill:var(--warn)}
.mk{fill:var(--card);stroke-width:1.6} .mk.buy{stroke:var(--up)} .mk.sell{stroke:var(--dn)} .mk.low{stroke:var(--dn);fill:var(--dn)}
.pk{fill:var(--fl)} .grid{stroke:var(--rule);stroke-dasharray:3 3} .ax.p{fill:var(--dn)} .ax.f{fill:var(--fl)}
.legend{font-size:12px;color:var(--soft);margin:4px 0 0}
.note{font-size:12px;color:var(--faint)} .nochart{font-size:12px;color:var(--faint);padding:20px 0}
.cm{background:var(--card);border:1px solid var(--rule);border-radius:10px;padding:6px 16px;font-size:14px}
.cm h3{font-size:13px;color:var(--soft);margin:12px 0 2px} .cm p{margin:6px 0}
.kpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px}
.kpi div{background:var(--card);border:1px solid var(--rule);border-radius:10px;padding:10px 12px}
.kpi .k{font-size:11.5px;color:var(--soft)} .kpi .v{font-size:1.2rem;font-weight:600;font-variant-numeric:tabular-nums}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:6px} @media(max-width:560px){.pair{grid-template-columns:1fr}}
.alarm li{margin:4px 0;font-size:13.5px}
.card.near{border-color:#b9bdc6;border-style:dashed;opacity:.92} .near-why{color:var(--soft);margin:0 0 4px}
tr.hl td{background:#fff3b0;font-weight:700}
.mkt{margin:4px 0 8px;font-size:13.5px}
.vb{fill:var(--rule)} .vb.on{fill:var(--fl)} .who{font-size:11px;font-weight:700;margin-left:5px} .who.f{color:var(--fl)} .who.i{color:var(--warn)}
"""


REGEN_NOTE = ""   # 재생성 표시 — main(regen=사유)이 채운다


def regen_note(reason: str, now) -> str:
    """사고로 숫자가 틀린 리포트를 고친 뒤 다시 만들 때 부제 끝에 붙는 한 줄(대표 10-23 MD 보고 답 5-5 A).
    다시 만들면 숫자가 그 시각 값이 되니 그것도 같이 밝힌다."""
    t = now.strftime("%H:%M")
    return f' · <b>{t} 재생성({html.escape(reason)})</b> — 숫자는 {t} 기준'


def page(title, sub, body):
    return f'<title>{title}</title>\n<style>{CSS}</style>\n<div class="wrap"><h1>{title}</h1><p class="sub">{sub}{REGEN_NOTE}</p>{body}</div>\n'


# ------------------------------------------------------------------ 계산
def build(data, link_res):
    today, ph, fh, live, quotes = data["today"], data["ph"], data["fh"], data["live"], data["quotes"]
    tx = core.load_transactions()
    h = core.load_holdings()
    state = core.load_state()
    # @뇌 meritz_보유_읽기
    mp = HERE.parent / "meritz" / "portfolio_data.csv"
    hm = pd.read_csv(mp, dtype={"종목코드": str}) if mp.exists() else pd.DataFrame(columns=["종목명", "평단가"])
    m_names = set(hm["종목명"])
    n_names = set(h["종목명"])
    name2code = dict(zip(ph["종목명"], ph["종목코드"]))
    name2code.update(dict(zip(h["종목명"], h["종목코드"].astype(str))))
    trade_days = sorted(ph["날짜"].unique())

    def own(n):
        return " ".join(t for t, ok in (("N", n in n_names), ("M", n in m_names)) if ok)

    def tdays_since(d):
        return sum(1 for x in trade_days if x > d) + (0 if trade_days and trade_days[-1] >= today else 1)

    # 실시간가 반영한 보유 사본(Watering/Alarm 계산용 — portfolio_data.csv는 안 건드림)
    hl = h.copy()
    for i, r in hl.iterrows():
        q = quotes.get(str(r["종목코드"]))
        if q and q.get("price"):
            hl.at[i, "현재가"] = q["price"]
            hl.at[i, "등락률"] = q.get("change_pct")
    missing = [c for c in hl["종목코드"].astype(str) if c not in quotes]
    if missing:
        q2, _ = core.fetch_quotes(missing)
        for i, r in hl.iterrows():
            q = q2.get(str(r["종목코드"]))
            if q and q.get("price"):
                hl.at[i, "현재가"] = q["price"]
                hl.at[i, "등락률"] = q.get("change_pct")
                live[str(r["종목코드"])] = q["price"]

    res = {"today": today, "asof": data["asof"], "own": own}

    # A. 계좌 vs 혼합지수 — live_check.live_snapshot() 그대로(히스토리 CSV가 아니라 확정 종가 + 지금 실시간).
    #    위에서 받은 실시간 시세(hl)를 넘겨서 같은 시각 가격으로 맞춘다.
    lq = {str(r["종목코드"]): {"price": float(r["현재가"]),
                              "change_pct": None if pd.isna(r["등락률"]) else float(r["등락률"])}
          for _, r in hl.iterrows()}
    snap = live_check.live_snapshot(holdings=hl, quotes=lq)
    res["index"] = {"ex": {"벤치": snap["ex"], "계좌": snap["acct"], "주식": snap["stock"]},
                    "main": {"벤치": snap["main"]}, "asof": snap["holdings_asof"], "snap": snap}

    # @뇌 updown_후보
    # B. Up/Down 재진입 후보 — 마지막 매도가 대비 많이 빠진 순
    closed = core.get_closed_out_last_sells(h, tx)
    _ended = core.ended_watch_names()   # 관심 끝 종목은 Up/Down에서 뺀다(대표 2026-10-22 (나))
    if _ended and not closed.empty:
        closed = closed[~closed["종목명"].isin(_ended)]
    need = [name2code.get(n) for n in closed["종목명"] if name2code.get(n) and name2code.get(n) not in live]
    if need:
        q3, _ = core.fetch_quotes([c for c in need if c])
        live.update({c: v["price"] for c, v in q3.items() if v.get("price")})
    ud = []
    for _, r in closed.iterrows():
        c = name2code.get(r["종목명"])
        px = live.get(c) if c else None
        if not px:
            continue
        pts = series_from(ph, c, r["매도일"], px, today)
        pct = (px / r["매도가"] - 1) * 100
        low_d, low_v = min(pts, key=lambda t: t[1]) if pts else (today, px)
        ud.append({"종목명": r["종목명"], "코드": c, "매도일": r["매도일"], "매도가": float(r["매도가"]),
                   "현재가": px, "pct": pct, "경과": tdays_since(r["매도일"]),
                   "저점": low_v, "저점일": low_d, "반등": (px / low_v - 1) * 100 if low_v else 0,
                   "pts": series_from(ph, c, min(r["매도일"], trade_days[-15] if len(trade_days) > 15 else r["매도일"]), px, today),
                   "since": pts})
    ud.sort(key=lambda x: x["pct"])
    res["updown"] = ud[:TOP]
    res["updown_n"] = len(ud)

    # C. Watering Detect — 물타기 중 종목, 마지막 매수가 대비 많이 빠진 순(앱과 같은 함수)
    wrows = core.compute_watering_rows(tx, hl)
    wd = []
    for r in wrows:
        c = name2code.get(r["종목명"])
        pts_tr = core.get_holding_trade_points(tx, r["종목명"])
        buys = pts_tr[pts_tr["구분"] == "매수"]
        hr = hl[hl["종목명"] == r["종목명"]].iloc[0]
        px = float(hr["현재가"])
        start = buys.iloc[0]["날짜"]
        pts = series_from(ph, c, start, px, today) if c else []
        last_d = buys.iloc[-1]["날짜"]
        since = [p for p in pts if p[0] >= last_d]
        wd.append({**r, "코드": c, "현재가": px, "평단가": float(hr["평단가"]),
                   "최초가": float(buys.iloc[0]["단가"]), "마지막가": float(buys.iloc[-1]["단가"]),
                   "마지막일": last_d, "매수횟수": len(buys), "경과": tdays_since(last_d),
                   "buys": list(zip(buys["날짜"], buys["단가"].astype(float))), "pts": pts, "since": since})
    res["watering"] = wd[:TOP]
    res["watering_n"] = len(wd)

    # D. Fishing — 관심종목 기준일 대비 누적 하락 순 + 바닥·반등 흐름 + 순위 변화
    fish = []
    for c, g in ph.groupby("종목코드"):
        g = g.sort_values("날짜")
        # @뇌 fishing_기준가
        base_d, base_px = g["날짜"].iloc[0], float(g["종가"].iloc[0])
        px = live.get(c)
        pts = series_from(ph, c, base_d, px, today)
        if not base_px or len(pts) < 2:
            continue
        cur = pts[-1][1]
        cum = (cur / base_px - 1) * 100
        low_d, low_v = min(pts, key=lambda t: t[1])
        reb = (cur / low_v - 1) * 100
        since_low = sum(1 for d, _ in pts if d > low_d)
        last3 = (cur / pts[-4][1] - 1) * 100 if len(pts) >= 4 else 0
        if since_low <= 1 or reb < 1:
            st_ = ("빠지는 중", "down")
        elif reb >= 3 and last3 > 0:
            st_ = ("반등 중", "")
        else:
            st_ = ("바닥 다지는 중", "base")
        q = quotes.get(c, {})
        fish.append({"종목명": g["종목명"].iloc[0], "코드": c, "기준일": base_d, "기준가": base_px,
                     "현재가": cur, "cum": cum, "저점": low_v, "저점일": low_d,
                     "저점누적": (low_v / base_px - 1) * 100, "반등": reb, "저점후": since_low,
                     "최근3일": last3, "상태": st_, "전일": q.get("change_pct"), "pts": pts})
    fish.sort(key=lambda x: x["cum"])
    rank_path = OUT / "fishing_rank.csv"
    rh = pd.read_csv(rank_path, dtype={"코드": str}) if rank_path.exists() else pd.DataFrame(columns=["날짜", "코드", "순위"])
    prev_d = sorted(d for d in rh["날짜"].unique() if d < today)
    prev = dict(zip(rh[rh["날짜"] == prev_d[-1]]["코드"], rh[rh["날짜"] == prev_d[-1]]["순위"])) if prev_d else {}
    for i, f in enumerate(fish, 1):
        f["순위"] = i
        f["이전"] = prev.get(f["코드"])
    rh = pd.concat([rh[rh["날짜"] != today],
                    pd.DataFrame([{"날짜": today, "코드": f["코드"], "순위": f["순위"]} for f in fish])], ignore_index=True)
    OUT.mkdir(exist_ok=True)
    rh.to_csv(rank_path, index=False, encoding="utf-8-sig")
    res["fishing"] = fish[:TOP]
    res["fishing_first"] = not prev_d
    res["fishing_n"] = len(fish)

    # D2. Basecamp — 죽죽 빠지다 평지에서 거래량이 평소보다 꾸준히 터지는 종목(basecamp.py, 점수 순)
    camps = basecamp.scan(ph, fh, live, today=today, top_n=None)
    for x in camps[:BC_TOP]:
        i0 = trade_days.index(x["고점일"]) if x["고점일"] in trade_days else 0
        start = trade_days[max(0, i0 - 10)]
        x["pts"] = series_from(ph, x["종목코드"], start, live.get(x["종목코드"]), today)
        g = fh[(fh["종목코드"] == x["종목코드"]) & (fh["날짜"] >= start) & (fh["날짜"] < today)].sort_values("날짜")
        x["vols"] = list(zip(g["날짜"], pd.to_numeric(g["거래량"], errors="coerce").astype(float)))
    res["basecamp"] = camps[:TOP]
    # ④용 — 전일 순위(basecamp_rank.csv)와 비교해 순위 변동·신규·탈락
    bpath = OUT / "basecamp_rank.csv"
    bh = (pd.read_csv(bpath, dtype={"코드": str}) if bpath.exists()
          else pd.DataFrame(columns=["날짜", "코드", "종목명", "순위", "상태"]))
    bprev_d = sorted(d for d in bh["날짜"].unique() if d < today)
    bprev = bh[bh["날짜"] == bprev_d[-1]] if bprev_d else bh.iloc[0:0]
    prev_rank = dict(zip(bprev["코드"], bprev["순위"]))
    all_rank = {x["종목코드"]: i for i, x in enumerate(camps, 1)}
    by_code = {x["종목코드"]: x for x in camps}
    full = camps[:BC_TOP]
    for i, x in enumerate(full, 1):
        x["순위"], x["이전"] = i, prev_rank.get(x["종목코드"])
    top_codes = {x["종목코드"] for x in full}
    res["bc_dropped"] = [{"종목명": r["종목명"], "이전": int(r["순위"]), "지금": all_rank.get(r["코드"]),
                          "상태": by_code[r["코드"]]["상태"] if r["코드"] in by_code else "평지 아님"}
                         for _, r in bprev.sort_values("순위").iterrows() if r["코드"] not in top_codes]
    bh = pd.concat([bh[bh["날짜"] != today],
                    pd.DataFrame([{"날짜": today, "코드": x["종목코드"], "종목명": x["종목명"],
                                   "순위": x["순위"], "상태": x["상태"]} for x in full])], ignore_index=True)
    bh.to_csv(bpath, index=False, encoding="utf-8-sig")
    res["bc_full"], res["bc_n"], res["bc_prev"] = full, len(camps), (bprev_d[-1] if bprev_d else None)

    # E. Today's Alarm — 앱과 같은 함수, 입력만 리포트 데이터로 구성
    fp = pd.DataFrame([{"종목명": f["종목명"], "최초가": f["기준가"], "최근가": f["현재가"],
                        "전일대비": f["전일"], "기준일": f["기준일"]} for f in fish])
    res["alarm"] = core.compute_todays_alarm(tx, hl, fp, fh, ph)

    # @뇌 meritz_진입후보
    # F. meritz 진입 후보 — new1 보유 중 **평단 대비 평가손익 −5% 이하** + meritz 미보유를 **거른다**(필터).
    #    걸린 게 없으면 문턱에 가장 가까운 순으로 채우고 "가까운 순"이라고 밝힌다(대표 원칙).
    #    대표 2026-10-22 원문 "뉴원에서 가진 종목이 … 현재 -5%이상 손실을 보고잇는데 그 종목이 메리츠에 없다면 보고하면돼"
    #    (세팅 데이터센터/세팅 "meritz 진입 후보", 0ee5412). 대체됨: 10-21 최초진입가 대비 순위 10 + −5% 점선.
    mz = []
    for _, r in hl.iterrows():
        if r["종목명"] in m_names:
            continue
        px, avg = float(r["현재가"]), float(r["평단가"])
        pts_tr = core.get_holding_trade_points(tx, r["종목명"])
        buys = pts_tr[pts_tr["구분"] == "매수"]
        if buys.empty:
            continue
        first = float(buys.iloc[0]["단가"])
        c = str(r["종목코드"])
        mz.append({"종목명": r["종목명"], "코드": c, "현재가": px, "평단가": avg, "최초가": first,
                   "pct_avg": (px / avg - 1) * 100, "pct_first": (px / first - 1) * 100,
                   "최초일": buys.iloc[0]["날짜"], "매수횟수": len(buys),
                   "buys": list(zip(buys["날짜"], buys["단가"].astype(float))),
                   "pts": series_from(ph, c, buys.iloc[0]["날짜"], px, today)})
    hit = sorted([x for x in mz if x["pct_avg"] <= MERITZ_ENTRY_LOSS], key=lambda x: x["pct_avg"])
    if hit:
        res["meritz"], res["meritz_near"] = hit[:TOP], False
    else:
        # 걸린 게 없으면 문턱(−5%)에 가장 가까운 순 — 손실이 큰 순과 같다(문턱 위에선 pct_avg가 작을수록 가까움)
        res["meritz"], res["meritz_near"] = sorted(mz, key=lambda x: x["pct_avg"])[:MERITZ_NEAR_N], True

    # ③ CFG — ①의 후보 중, 그 하락 구간 동안 외인이 실제로 늘었고 외인 상태가 나쁘지 않은 종목
    stats = link_res["stats"]
    def fwin(code, start):
        g = fh[(fh["종목코드"] == code) & (fh["날짜"] >= start)].sort_values("날짜")
        if len(g) < 2:
            return None
        a, b = float(g["외국인보유율"].iloc[0]), float(g["외국인보유율"].iloc[-1])
        top3 = float(g["외국인보유율"].iloc[1:].tail(3).max())   # 끝 사흘(거래일) 최고 — 짧으면 구간 전체(시작 빼고)
        return {"시작": g["날짜"].iloc[0], "시작값": a, "지금": b, "d": b - a, "d3": top3 - a,
                "rel": (b - a) / a * 100 if a else 0, "끝": g["날짜"].iloc[-1]}
    # @뇌 cfg_후보_출처
    srcs = ([("Up/Down", x["코드"], x["종목명"], x["매도일"], x["pct"], "매도가 대비") for x in res["updown"]]
            + [("Watering", x["코드"], x["종목명"], x["마지막일"], x["pct_last"], "마지막 매수가 대비") for x in res["watering"]]
            + [("Fishing", x["코드"], x["종목명"], x["기준일"], x["cum"], "기준일 대비") for x in res["fishing"]]
            + [("Basecamp", x["종목코드"], x["종목명"], x["평지시작"], x["고점대비"], "고점 대비(평지 구간 외인)") for x in res["basecamp"]]
            + [("meritz 후보", x["코드"], x["종목명"], x["최초일"], x["pct_avg"], "new1 평단 대비") for x in res["meritz"]])
    cfg, near = [], []
    for src, c, n, start, pct, basis in srcs:
        if not c or pct is None or pct > -3:
            continue
        s = stats.get(c)
        w = fwin(c, start)
        if not w or not s:
            continue
        row = {"출처": src, "코드": c, "종목명": n, "시작": start, "pct": pct, "basis": basis, "fw": w, "s": s}
        if not s.get("착시") and not s.get("꺾임") and (w["d"] >= 0.3 or (w["시작값"] >= 3 and w["rel"] >= 5)):
            cfg.append({**row, "score": abs(pct) * max(w["rel"], 0)})
        else:
            dist = cfg_distance(w, s)
            if dist["max"] < 1:          # 거리 1 = 외인 하나도 안 늘음·착시 60%·꺾임 30% — 비슷한 게 아님(35·87 2차)
                near.append({**row, "dist": dist})
    merged = {}
    for x in cfg:
        if x["코드"] not in merged or x["score"] > merged[x["코드"]]["score"]:
            merged[x["코드"]] = {**x, "출처들": set()}
        merged[x["코드"]]["출처들"].add(x["출처"])
    res["cfg"] = sorted(merged.values(), key=lambda x: -x["score"])
    # 가까운 것(대표 10-23 ⓐ): 진짜 CFG가 아닌 종목만, 종목마다 가장 가까운 출처 하나로. 문턱까지 거리의 가장 큰 칸이 작은 순,
    # 같으면 합이 작은 순. 진짜 위 + 가까운 것으로 CFG_SHOW칸을 채우고, 1~CFG_KEEP위는 cfg_rank.csv에 저장(나중에 연구소 재료).
    nm = {}
    for x in near:
        if x["코드"] in merged:
            continue
        k = (x["dist"]["max"], x["dist"]["sum"])
        if x["코드"] not in nm or k < (nm[x["코드"]]["dist"]["max"], nm[x["코드"]]["dist"]["sum"]):
            nm[x["코드"]] = {**x, "출처들": nm.get(x["코드"], {}).get("출처들", set())}
        nm[x["코드"]]["출처들"].add(x["출처"])
    res["cfg_near"] = sorted(nm.values(), key=lambda x: (x["dist"]["max"], x["dist"]["sum"]))
    res["cfg_rank"] = (res["cfg"] + res["cfg_near"])[:CFG_KEEP]
    res["stats"] = stats
    return res


# ------------------------------------------------------------------ CFG 가까운 것
CFG_SHOW, CFG_KEEP = 10, 15      # 대표 10-23 「에이가 맞아」 — 매일 10칸(진짜 위 + 가까운 것 회색), 1~15위는 cfg_rank.csv
CFG_RANK_CSV = OUT / "cfg_rank.csv"


def cfg_distance(w: dict, s: dict) -> dict:
    """CFG 문턱까지 거리(체마다 0~1, 0 = 그 체 통과). 대표 10-23 「착시인지 꺾인 건지 판단 자체가 숫자처럼 명확한 게 아니야
    … 확률이 가장 낮은 것들은 포함」 → 35·87 합의(주민센터 CFG_가까운것_회의_35_87.md):
    - 외인: 늘어난 양 = 하락 구간 끝 사흘 최고 − 시작(하루이틀 빠진 건 안 뺌). (0.3%p − 늘어난 양)/0.3,
      원래 비중 3% 이상이면 상대 (5% − 늘어난 %)/5와 짧은 쪽. 0 이하로 늘었으면 거리 ≥ 1(안 보임).
    - 착시: (뱉은 비율 − 30%)/30%(착시일 때만). 꺾임: (뱉은 비율 − 15%)/15%(꺾임 셋이 다 맞을 때만).
    줄 세우기는 가장 큰 거리(max) → 같으면 합(sum)."""
    inc = w.get("d3", w["d"])
    df = max(0.0, (0.3 - inc) / 0.3)
    if w["시작값"] >= 3:
        df = min(df, max(0.0, (5 - inc / w["시작값"] * 100) / 5))
    give = float(s.get("반납률") or 0)
    di = max(0.0, (give - 0.30) / 0.30) if s.get("착시") else 0.0
    dt = max(0.0, (give - 0.15) / 0.15) if s.get("꺾임") else 0.0
    parts = {"외인": df, "착시": di, "꺾임": dt}
    worst = max(parts, key=parts.get)
    if parts[worst] == 0:                    # 셋 다 0인데 진짜가 아님 = 착시·꺾임 문턱에 딱 걸렸거나 외인 끝 값만 모자람
        worst = "착시" if s.get("착시") else "꺾임" if s.get("꺾임") else "외인"
    return {"외인": df, "착시": di, "꺾임": dt, "max": parts[worst], "sum": df + di + dt, "주원인": worst}


def near_reason(x: dict) -> str:
    """가까운 것 카드 머리 한 줄 — 무엇이 얼마나 모자랐나."""
    d, w, s = x["dist"], x["fw"], x["s"]
    if d["주원인"] == "착시":
        why = f'착시 문턱 30%에 {s["반납률"]:.0%}로 걸림'
    elif d["주원인"] == "꺾임":
        why = f'꺾임(뱉은 비율 {s["반납률"]:.0%}·최근 5일 감소)'
    elif d["외인"] == 0:
        why = f'외인 끝 값 {w["d"]:+.2f}%p가 문턱 아래(끝 사흘 최고 {w["d3"]:+.2f}%p는 넘음)'
    else:
        why = f'외인 {w["d3"]:+.2f}%p(끝 사흘 최고) — 문턱 +0.3%p 아래'
    return f'가장 비슷한 쪽으로 골랐어 — {why} (거리 {d["max"]:.2f})'


def save_cfg_rank(res: dict, live: dict, asof: str) -> None:
    """그날 CFG 순위 1~CFG_KEEP를 cfg_rank.csv에(같은 날짜는 교체). 연구소(이관 뒤 새 빌딩)가 가져다 쓰는 재료."""
    rows = []
    for i, x in enumerate(res.get("cfg_rank", []), 1):
        d = x.get("dist") or {"외인": 0.0, "착시": 0.0, "꺾임": 0.0, "max": 0.0}
        rows.append({"날짜": res["today"], "시각": asof, "순위": i, "구분": "진짜" if "dist" not in x else "가까운 것",
                     "종목코드": x["코드"], "종목명": x["종목명"], "출처": "|".join(sorted(x["출처들"])),
                     "시작일": x["시작"], "하락%": round(x["pct"], 2),
                     "외인시작": round(x["fw"]["시작값"], 2), "외인증가pp": round(x["fw"]["d"], 3),
                     "외인증가3일pp": round(x["fw"].get("d3", x["fw"]["d"]), 3),
                     "거리_외인": round(d["외인"], 3), "거리_착시": round(d["착시"], 3), "거리_꺾임": round(d["꺾임"], 3),
                     "거리max": round(d["max"], 3), "현재가": live.get(x["코드"])})
    old = pd.read_csv(CFG_RANK_CSV, dtype={"종목코드": str}) if CFG_RANK_CSV.exists() else pd.DataFrame()
    if not old.empty:
        old = old[old["날짜"] != res["today"]]
    pd.concat([old, pd.DataFrame(rows)], ignore_index=True).to_csv(CFG_RANK_CSV, index=False, encoding="utf-8-sig")


# ------------------------------------------------------------------ HTML
NO_COMMENT = '<p class="note">아직 작성 전</p>'
CM_OPEN, CM_CLOSE = "<!--총평-->", "<!--/총평-->"


def comment(name):
    p = OUT / "comments" / name
    return p.read_text(encoding="utf-8") if p.exists() else NO_COMMENT


def attach_comments(day: str) -> list[str]:
    """이미 만든 그날 리포트 4종의 총평 칸(<!--총평-->…<!--/총평-->)만 comments 파일로 갈아끼운다.
    대표 10-23 「총평이 달린 리포트를 한 번에 올리면 되지 않아?」 — 09:30 생성 → 세션이 총평을 쓰고 이것만 돌린다.
    데이터·네트워크를 안 부르고 CSV(cfg_log·ledger·roster·basecamp_rank)도 안 건드려서 숫자는 생성 시각 그대로다.
    latest_*.html은 그 판이 같은 날 판일 때만(내용이 같을 때만) 같이 고친다. 표시 없는 옛 판은 건너뛰고 알린다."""
    targets = [
        ("① 액션", OUT / "action" / f"{day}.html", OUT / "latest_action.html", OUT / "comments" / f"{day}_action.html"),
        ("② 외인", link.OUT / "daily" / f"{day}.html", link.OUT / "latest.html", link.OUT / "comments" / f"{day}.html"),
        ("③ CFG", OUT / "cfg" / f"{day}.html", OUT / "latest_cfg.html", OUT / "comments" / f"{day}_cfg.html"),
        ("④ Basecamp", OUT / "basecamp" / f"{day}.html", OUT / "latest_basecamp.html", OUT / "comments" / f"{day}_basecamp.html"),
    ]
    out = []
    for lab, dated, latest, cpath in targets:
        if not dated.exists():
            out.append(f"{lab}: 리포트 없음({dated.name})")
            continue
        old = dated.read_text(encoding="utf-8")
        a, b = old.find(CM_OPEN), old.find(CM_CLOSE)
        if a < 0 or b < a:
            out.append(f"{lab}: 총평 표시 없는 판(이 길 이전 생성) — 건너뜀")
            continue
        body = cpath.read_text(encoding="utf-8") if cpath.exists() else NO_COMMENT
        new = old[:a + len(CM_OPEN)] + body + old[b:]
        dated.write_text(new, encoding="utf-8")
        same = latest.exists() and latest.read_text(encoding="utf-8") == old
        if same:
            latest.write_text(new, encoding="utf-8")
        out.append(f"{lab}: {'총평 붙임' if cpath.exists() else '총평 파일 없음(작성 전 표시)'}"
                   f"{' · latest도' if same else ''}")
    return out


# 대표 매매 문턱(10-23 대표 확정) — 리포트는 거르지 않고 넘은 줄만 형광펜(「숫자는 안 박아」).
NEW_ENTRY_LINE = -10.0   # 신규 진입: Fishing 기준일 대비 −10% 이상
REENTRY_BASE = -5.0      # 재진입 기본: 마지막 매도가 대비 −5%


def market_regime(day_pct):
    """그날(당일) 혼합지수(삼성·하이닉스 제외) % → (장, 재진입 형광펜 선 %, 대표 말). 대표 10-23 기준 그대로:
    +1% 이상 좋음(−3%도) / −1% 초과~+1% 미만 보통(−5% 기본) / −2% 초과~−1% 이하 안 좋음(−10%에 가까워짐,
    −1.5~−2% 빈칸도 안 좋음 — 대표 6-A) / −2% 이하 매우 안 좋음(−10% 쪽). 형광펜은 기본 −5%, 좋은 날만 −3%."""
    if day_pct is None:
        return "모름", REENTRY_BASE, "−5% 기본"
    if day_pct >= 1.0:
        return "좋음", -3.0, "−3%짜리도 살 수 있음"
    if day_pct > -1.0:
        return "보통", REENTRY_BASE, "−5% 기본"
    if day_pct > -2.0:
        return "안 좋음", REENTRY_BASE, "−10%에 가까워짐"
    return "매우 안 좋음", REENTRY_BASE, "−10% 쪽"


def write_action(res, ph):
    today, own = res["today"], res["own"]
    ix, im = res["index"]["ex"], res["index"]["main"]
    def kv(k, lab):
        a = ix.get(k) if k != "main벤치" else im.get("벤치")
        if not a or a[0] is None:
            return ""
        return f'<div><div class="k">{lab}</div><div class="v">{sign(a[0] * 100, 2)}</div><div class="k">당일 {sign(a[1] * 100, 2)}</div></div>'
    kpi = (kv("벤치", "혼합지수(삼성·하이닉스 제외)") + kv("main벤치", "혼합지수(일반)") + kv("계좌", "내 계좌")
           + kv("주식", "내 주식(예수금 제외)"))

    # Up/Down
    _bday = ix.get("벤치")[1] * 100 if ix.get("벤치") and ix["벤치"][1] is not None else None
    mk, mk_line, mk_say = market_regime(_bday)
    n_hit = sum(1 for x in res["updown"] if x["pct"] <= mk_line)
    _t = str(res["index"]["snap"].get("asof", ""))[-5:]           # 장 판정 시각(대표 10-23: 09:30 = 장 시작 30분이 흐름을 정함)
    _tl = f"{_t}, 장 시작 30분" if _t[:2] == "09" else _t
    mkt_html = (f'<p class="mkt">오늘 장({_tl}): 혼합지수(삼성·하이닉스 제외) 당일 {sign(_bday, 2) if _bday is not None else "—"} → '
                f'<b>{mk}</b> · 재진입 선 {mk_say} · 형광펜 = 매도가 대비 {mk_line:.0f}% 넘은 줄 {n_hit}개'
                + (' (넘은 줄 없음 — 맨 위가 가장 가까운 것)' if not n_hit and res["updown"] else '') + '</p>')
    rows, cards = [], []
    for i, x in enumerate(res["updown"], 1):
        hl = ' class="hl"' if x["pct"] <= mk_line else ""
        rows.append(f'<tr{hl}><td class="rk">{i}</td><td class="l">{esc(x["종목명"])}</td><td>{x["매도일"][5:]}</td>'
                    f'<td>{x["매도가"]:,.0f}</td><td>{x["현재가"]:,.0f}</td><td>{sign(x["pct"])}</td>'
                    f'<td>{x["경과"]}일</td><td>{sign(-(1 - x["저점"] / x["매도가"]) * 100)} ({x["저점일"][5:]})</td>'
                    f'<td>{sign(x["반등"])}</td><td class="l">{speed_badge(x["since"], x["pct"])}</td></tr>')
        cards.append(card(f'{i}. {esc(x["종목명"])}',
                          f'{x["매도일"][5:]}에 {x["매도가"]:,.0f}원 매도 → 지금 {x["현재가"]:,.0f}원 ({x["pct"]:+.1f}%) · 매도 후 {x["경과"]}거래일',
                          svg_line(x["pts"], refs=[("매도가", x["매도가"], "sell")],
                                   marks=[(x["매도일"], x["매도가"], "sell")])))
    ud_html = (f'<div class="scroll"><table><thead><tr><th>#</th><th class="l">종목</th><th>매도일</th><th>매도가</th><th>지금</th>'
               f'<th>매도가 대비</th><th>경과</th><th>매도 후 저점</th><th>저점 대비</th><th class="l">속도</th></tr></thead>'
               f'<tbody>{"".join(rows)}</tbody></table></div><div class="grid2">{"".join(cards)}</div>')

    # Watering
    rows, cards = [], []
    for i, x in enumerate(res["watering"], 1):
        rows.append(f'<tr><td class="rk">{i}</td><td class="l">{esc(x["종목명"])}<span class="own">{own(x["종목명"])}</span></td>'
                    f'<td>{x["매수횟수"]}회</td><td>{x["마지막일"][5:]}</td><td>{sign(x["pct_last"])}</td><td>{x["경과"]}일</td>'
                    f'<td>{sign(x["pct_first_avg"])}</td><td>{sign(x["pct_first_cur"])}</td>'
                    f'<td class="l">{speed_badge(x["since"], x["pct_last"])}</td></tr>')
        cards.append(card(f'{i}. {esc(x["종목명"])}',
                          f'최초 {x["최초가"]:,.0f} · 평단 {x["평단가"]:,.0f} · 마지막 매수 {x["마지막가"]:,.0f}({x["마지막일"][5:]}) → 지금 {x["현재가"]:,.0f}',
                          svg_line(x["pts"], refs=[("최초", x["최초가"], "first"), ("평단", x["평단가"], "avg"),
                                                    ("마지막", x["마지막가"], "last")],
                                   marks=[(d, p, "buy") for d, p in x["buys"]])))
    wd_html = (f'<div class="scroll"><table><thead><tr><th>#</th><th class="l">종목</th><th>매수</th><th>마지막 매수</th>'
               f'<th>마지막 매수가 대비</th><th>경과</th><th>평단(최초 대비)</th><th>지금(최초 대비)</th><th class="l">속도</th></tr></thead>'
               f'<tbody>{"".join(rows)}</tbody></table></div><div class="grid2">{"".join(cards)}</div>')

    # Fishing
    n_new = sum(1 for x in res["fishing"] if x["cum"] <= NEW_ENTRY_LINE)
    fi_mkt = (f'<p class="mkt">형광펜 = 신규 진입 선(기준일 대비 {NEW_ENTRY_LINE:.0f}%) 넘은 줄 {n_new}개'
              + (' (넘은 줄 없음 — 맨 위가 가장 가까운 것)' if not n_new and res["fishing"] else '') + '</p>')
    rows, cards = [], []
    for x in res["fishing"]:
        mv = "" if res["fishing_first"] else (
            '<span class="new">NEW</span>' if x["이전"] is None or x["이전"] > TOP else
            (f'<span class="up">▲{x["이전"] - x["순위"]}</span>' if x["이전"] > x["순위"] else
             f'<span class="dn">▼{x["순위"] - x["이전"]}</span>' if x["이전"] < x["순위"] else '<span class="mut">-</span>'))
        st_, cls = x["상태"]
        hl = ' class="hl"' if x["cum"] <= NEW_ENTRY_LINE else ""
        rows.append(f'<tr{hl}><td class="rk">{x["순위"]}</td><td>{mv}</td><td class="l">{esc(x["종목명"])}<span class="own">{own(x["종목명"])}</span></td>'
                    f'<td>{sign(x["cum"])}</td><td>{sign(x["저점누적"])} ({x["저점일"][5:]})</td><td>{sign(x["반등"])}</td>'
                    f'<td>{x["저점후"]}일</td><td>{sign(x["최근3일"])}</td><td class="l"><span class="bd st {cls}">{st_}</span></td></tr>')
        cards.append(card(f'{x["순위"]}. {esc(x["종목명"])}',
                          f'기준일 {x["기준일"][5:]} 대비 {x["cum"]:+.1f}% · 바닥 {x["저점누적"]:+.1f}% ({x["저점일"][5:]}) 이후 {x["반등"]:+.1f}% · {st_}',
                          svg_line(x["pts"], refs=[("기준가", x["기준가"], "zero")],
                                   marks=[(x["저점일"], x["저점"], "low")], pct_base=x["기준가"])))
    fi_html = (f'<div class="scroll"><table><thead><tr><th>#</th><th></th><th class="l">종목</th><th>누적</th><th>바닥</th>'
               f'<th>바닥 대비</th><th>바닥 후</th><th>최근 3일</th><th class="l">상태</th></tr></thead>'
               f'<tbody>{"".join(rows)}</tbody></table></div><div class="grid2">{"".join(cards)}</div>')

    # Basecamp
    rows, cards = [], []
    st_cls = {"돌파": "", "다지는 중": "base", "이탈": "down"}
    for i, x in enumerate(res["basecamp"], 1):
        rows.append(f'<tr><td class="rk">{i}</td><td class="l">{esc(x["종목명"])}<span class="own">{own(x["종목명"])}</span></td>'
                    f'<td>{x["고점일"][5:]}</td><td>{sign(x["하락률"])}</td><td>{x["평지시작"][5:]} · {x["평지일수"]}일</td>'
                    f'<td>{x["밴드"]:.1f}%</td><td>×{x["거래량배수"]:.1f}</td><td>{x["대량일수"]}/{x["거래량일수"]}일</td>'
                    f'<td>{sign(x["고점대비"])}</td><td class="l"><span class="bd st {st_cls[x["상태"]]}">{x["상태"]}</span></td></tr>')
        cards.append(card(f'{i}. {esc(x["종목명"])}',
                          f'고점 {x["고점일"][5:]} {x["고점"]:,.0f} → 평지 {x["평지시작"][5:]}부터 {x["평지일수"]}일 '
                          f'({x["평지저"]:,.0f}~{x["평지고"]:,.0f}) · 거래량 하락 전 대비 ×{x["거래량배수"]:.1f}, '
                          f'2배 이상 {x["대량일수"]}/{x["거래량일수"]}일 · 지금 {x["현재가"]:,.0f} {x["상태"]}',
                          svg_line(x["pts"], refs=[("고점", x["고점"], "first"), ("평지고", x["평지고"], "avg"),
                                                   ("평지저", x["평지저"], "last")])))
    bc_html = (f'<div class="scroll"><table><thead><tr><th>#</th><th class="l">종목</th><th>고점</th><th>평지까지 하락</th>'
               f'<th>평지</th><th>밴드</th><th>거래량</th><th>2배↑</th><th>지금(고점 대비)</th><th class="l">상태</th></tr></thead>'
               f'<tbody>{"".join(rows)}</tbody></table></div><div class="grid2">{"".join(cards)}</div>') if rows else '<p class="note">없음</p>'

    # Alarm
    al = res["alarm"]
    items = []
    for r in al.get("watering", []):
        fb = ' <span class="mut">(−5% 급락 없음, 가장 가까운 1개)</span>' if al.get("watering_fallback") else ""
        items.append(f'<li>물타기 급락: <b>{esc(r["종목명"])}</b> 전일 {sign(r["등락률"])} · 마지막 매수가 대비 {sign(r["pct_last"])}{fb}</li>')
    # @뇌 알람_외인_폭락줄
    for key, lab, fld, unit in (("quiet_hands", "외인 급증(최근3일)", "최근3일pp", "%p"), ("fishing", "관심종목 전일 폭락", "전일대비", "%")):
        blk = al.get(key, {})
        for r in blk.get("items", []):
            fb = ' <span class="mut">(참고용 1개)</span>' if blk.get("is_fallback") else ""
            items.append(f'<li>{lab}: <b>{esc(r["종목명"])}</b> {sign(r[fld], 2 if unit == "%p" else 1, unit)}{fb}</li>')
    al_html = f'<ul class="alarm">{"".join(items) or "<li class=mut>없음</li>"}</ul>'

    # meritz
    rows, cards = [], []
    for i, x in enumerate(res["meritz"], 1):
        rows.append(f'<tr><td class="rk">{i}</td><td class="l">{esc(x["종목명"])}</td><td>{x["최초일"][5:]}</td><td>{x["매수횟수"]}회</td>'
                    f'<td>{sign(x["pct_avg"])}</td><td>{sign(x["pct_first"])}</td></tr>')
        cards.append(card(f'{i}. {esc(x["종목명"])}',
                          f'new1 최초 {x["최초가"]:,.0f}({x["최초일"][5:]}) · 평단 {x["평단가"]:,.0f} → 지금 {x["현재가"]:,.0f}',
                          svg_line(x["pts"], refs=[("최초", x["최초가"], "first"), ("평단", x["평단가"], "avg"),
                                                    ("−5%", x["평단가"] * (1 + MERITZ_ENTRY_LOSS / 100), "last")],
                                   marks=[(d, p, "buy") for d, p in x["buys"]])))
    mz_near_note = (' — 걸린 종목 없음, 문턱에 가장 가까운 순' if res.get("meritz_near") else '')
    mz_html = (f'<div class="scroll"><table><thead><tr><th>#</th><th class="l">종목</th><th>new1 최초 진입</th><th>매수</th>'
               f'<th>평단 대비</th><th>최초진입가 대비</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
               f'<div class="grid2">{"".join(cards)}</div>')

    body = f"""
<h2>계좌 vs 혼합지수 (8/14 기준 누적)</h2>
<div class="kpi">{kpi}</div>
<p class="note">{res["index"]["snap"]["asof"]} 기준 · 지수는 확정 종가 + 지금 실시간, 당일은 전일 확정 종가 대비 · 보유·예수금은 매매일지 {res["index"]["asof"]} 반영분. 혼합지수는 보유종목 코스피/코스닥 평가금액 비중으로 가중.</p>

<h2>Up/Down — 재진입 후보 (매도가 대비 많이 빠진 순, 전체 {res["updown_n"]}종목 중 상위 {TOP})</h2>
{mkt_html}<p class="legend">빨간 점선 = 마지막 매도가 · 빨간 점 = 매도일</p>
{ud_html}

<h2>Watering Detect — 물타기 후보 (마지막 매수가 대비 많이 빠진 순, 물타기 중 {res["watering_n"]}종목 중 상위 {TOP})</h2>
<p class="legend">점선: 회색 최초진입가 · 녹색 평단 · 주황 마지막 매수가 · 빨간 원 = 매수 시점</p>
{wd_html}

<h2>Fishing — 관심종목 누적 하락 상위 {TOP} (전체 {res["fishing_n"]}종목)</h2>
<p class="legend">세로축 = 기준일 대비 % · 파란 점 = 기간 중 바닥 · 상태: 빠지는 중(바닥이 최근) / 바닥 다지는 중 / 반등 중(바닥 대비 +3%↑이고 최근 3일 상승)</p>
{fi_mkt}{fi_html}

<h2>Basecamp — 빠지다 평지에서 물량이 터지는 종목 (점수 상위 {TOP})</h2>
<p class="legend">점선: 회색 고점 · 녹색 평지 고가 · 주황 평지 저가 · 거래량 = 평지 구간 중간값 ÷ 하락 시작 전 중간값 · 2배↑ = 평지 중 하락 전의 2배 넘게 터진 날</p>
{bc_html}

<h2>Today's Alarm</h2>
{al_html}

<h2>meritz 진입 후보 (new1 보유 · meritz 미보유 · new1 평단 대비 {MERITZ_ENTRY_LOSS:.0f}% 이하){mz_near_note}</h2>
<p class="legend">점선: 회색 new1 최초진입가 · 녹색 new1 평단 · 주황 평단 −5%(meritz 문턱)</p>
{mz_html}

<h2>총평</h2>
<div class="cm"><!--총평-->{comment(f"{today}_action.html")}<!--/총평--></div>
<p class="note">속도 배지: 총 하락 중 절반 이상이 최근 3거래일에 났으면 급락, 아니면 완만. N = new1 보유 · M = meritz 보유. 외인 정보는 일부러 넣지 않음(② 외인 리포트에서 독립적으로 보기 위해).</p>
"""
    html_ = page("Overture · Action", f"{today} · 주가 {res['asof']} 기준 · ① 액션 리포트", body)
    (OUT / "action").mkdir(parents=True, exist_ok=True)
    (OUT / "action" / f"{today}.html").write_text(html_, encoding="utf-8")
    (OUT / "latest_action.html").write_text(html_, encoding="utf-8")


def write_cfg(res, data):
    today, ph, fh, live = res["today"], data["ph"], data["fh"], data["live"]
    cards = []
    n_near = max(0, CFG_SHOW - len(res["cfg"]))
    for i, x in enumerate(res["cfg"] + res.get("cfg_near", [])[:n_near], 1):
        s, w = x["s"], x["fw"]
        is_near = "dist" in x
        pts = series_from(ph, x["코드"], x["시작"], live.get(x["코드"]), today)
        left = svg_line(pts, refs=[("시작", pts[0][1], "zero")] if pts else (), marks=[], pct_base=pts[0][1] if pts else None)
        right = link.svg_chart(link.price_series(ph, x["코드"], s["기준일"]), link.foreign_series(fh, x["코드"], s["기준일"]),
                               live.get(x["코드"]), today)
        state = ("명단 안" if s.get("자격") else "다시 모으는 중" if s.get("재매집") else "조건 밖")
        near_html = f'<div class="note near-why">{esc(near_reason(x))}</div>' if is_near else ""
        cards.append(
            f'<div class="card{" near" if is_near else ""}">{near_html}<div class="ch">{i}. {esc(x["종목명"])} <span class="bd st">{" · ".join(sorted(x["출처들"]))}</span>'
            f'<span class="meta">{x["basis"]} {x["pct"]:+.1f}% ({x["시작"][5:]}부터) · 같은 구간 외인 '
            f'{w["시작값"]:.2f}% ({w["시작"][5:]}) → {w["지금"]:.2f}% ({w["끝"][5:]}) = {w["d"]:+.2f}%p (상대 {w["rel"]:+.1f}%) · ② 상태: {state}</span></div>'
            f'<div class="pair"><div><div class="note">① 그 기능의 하락 구간 주가</div>{left}</div>'
            f'<div><div class="note">② 외인 리포트 그래프 (기준일부터)</div>{right}</div></div></div>')
    body = f"""
<p class="note">① 액션 리포트 후보(Up/Down·Watering·Fishing·Basecamp·meritz 후보) 중, <b>그 기능이 잰 하락 구간(Basecamp는 평지 구간) 동안</b> 외국인 보유율이 실제로 늘었고(+0.3%p 이상 또는 상대 +5% 이상), ② 외인 리포트에서 착시·꺾임이 아닌 종목. 하락 3% 미만은 제외. 드문 게 정상. 진짜가 {CFG_SHOW}칸이 안 되면 문턱에 가장 가까운 것(체마다 문턱까지 거리 중 가장 큰 것이 작은 순)을 점선 카드로 채움 — 1~{CFG_KEEP}위는 cfg_rank.csv에 저장.</p>
<h2>오늘의 CFG (진짜 {len(res["cfg"])} · 가까운 것 {min(len(res.get("cfg_near", [])), max(0, CFG_SHOW - len(res["cfg"])))})</h2>
<div class="grid2" style="grid-template-columns:1fr">{"".join(cards) or '<p class="note">오늘은 없음</p>'}</div>
<h2>총평</h2>
<div class="cm"><!--총평-->{comment(f"{today}_cfg.html")}<!--/총평--></div>
"""
    html_ = page("Overture · CFG", f"{today} · 주가 {res['asof']} 기준 · ③ 두 신호가 겹치는 종목", body)
    (OUT / "cfg").mkdir(parents=True, exist_ok=True)
    (OUT / "cfg" / f"{today}.html").write_text(html_, encoding="utf-8")
    (OUT / "latest_cfg.html").write_text(html_, encoding="utf-8")


def write_basecamp(res, n_names, m_names):
    """④ Basecamp 리포트 — 상위 BC_TOP, 전일 대비 순위·신규/탈락, 가격+거래량 그래프, 받는손."""
    today = res["today"]
    own = lambda n: " ".join(t for t, ok in (("N", n in n_names), ("M", n in m_names)) if ok)
    st_cls = {"돌파": "", "다지는 중": "base", "이탈": "down"}

    def delta(x):
        if res["bc_prev"] is None:
            return ""
        if x["이전"] is None or pd.isna(x["이전"]):
            return '<span class="new">NEW</span>'
        d = int(x["이전"]) - x["순위"]
        if d > 0:
            return f'<span class="up">▲{d}</span>'
        return f'<span class="dn">▼{-d}</span>' if d < 0 else '<span class="mut">-</span>'

    def who(x):
        if x.get("받는손") == "외인":
            return '<span class="who f">외인</span>'
        if x.get("받는손") == "기관":
            return '<span class="who i">기관</span>'
        return ""

    def share(v):
        return "-" if v is None or pd.isna(v) else f"{v:+.1f}%"

    def fx(x):
        """외인 보유율 원래 비중 → 끝 (변화 %p · 비중대비 %) — 대표 2026-10-21 "상대적 가치"."""
        a, b, pp = x.get("외인시작"), x.get("외인끝"), x.get("외인pp")
        if a is None or b is None or pp is None or pd.isna(a) or pd.isna(b):
            return sign(pp, 2, "%p")
        rel = f" · 비중대비 {pp / a * 100:+.1f}%" if a else ""
        return f"{a:.2f}→{b:.2f}% ({pp:+.2f}%p{rel})"

    full = res["bc_full"]
    cnt = {k: sum(1 for x in full if x["상태"] == k) for k in ("돌파", "다지는 중", "이탈")}
    rows, cards = [], []
    for x in full:
        rows.append(f'<tr><td class="rk">{x["순위"]}</td><td>{delta(x)}</td>'
                    f'<td class="l">{esc(x["종목명"])}<span class="own">{own(x["종목명"])}</span>{who(x)}</td>'
                    f'<td>{x["score"]:.2f}</td><td>{x["고점일"][5:]}</td><td>{sign(x["하락률"])}</td>'
                    f'<td>{x["평지시작"][5:]} · {x["평지일수"]}일</td><td>{x["밴드"]:.1f}%</td><td>×{x["거래량배수"]:.1f}</td>'
                    f'<td>{x["대량일수"]}/{x["거래량일수"]}일</td><td>{fx(x)}</td>'
                    f'<td>{share(x["외인몫"])}</td><td>{share(x["기관몫"])}</td><td>{sign(x["고점대비"])}</td>'
                    f'<td class="l"><span class="bd st {st_cls[x["상태"]]}">{x["상태"]}</span></td></tr>')
        cards.append(card(f'{x["순위"]}. {esc(x["종목명"])}{who(x)}',
                          f'고점 {x["고점일"][5:]} {x["고점"]:,.0f} → 평지 {x["평지시작"][5:]}부터 {x["평지일수"]}일 '
                          f'({x["평지저"]:,.0f}~{x["평지고"]:,.0f}) · 거래량 ×{x["거래량배수"]:.1f}, '
                          f'2배↑ {x["대량일수"]}/{x["거래량일수"]}일 · 평지 외인 {fx(x)} · 거래량대비 {share(x["외인몫"])} · '
                          f'기관 {share(x["기관몫"])} · 지금 {x["현재가"]:,.0f} {x["상태"]}',
                          svg_line(x["pts"], refs=[("고점", x["고점"], "first"), ("평지고", x["평지고"], "avg"),
                                                   ("평지저", x["평지저"], "last")]),
                          svg_vol(x.get("vols", []), x["기준거래량"], x["평지시작"])))
    drop = "".join(f'<li><b>{esc(d["종목명"])}</b> {d["이전"]}위 → '
                   f'{str(d["지금"]) + "위" if d["지금"] else "명단 밖"} · {d["상태"]}</li>' for d in res["bc_dropped"])
    prev_txt = f'전일 리포트 {res["bc_prev"]} 대비' if res["bc_prev"] else "첫 리포트라 순위 변동 없음"
    none_li = '<li class="mut">없음</li>'
    body = f"""
<div class="kpi"><div><div class="k">평지 잡힌 종목</div><div class="v">{res["bc_n"]}</div></div>
<div><div class="k">상위 {BC_TOP} 중 돌파</div><div class="v">{cnt["돌파"]}</div></div>
<div><div class="k">다지는 중</div><div class="v">{cnt["다지는 중"]}</div></div>
<div><div class="k">이탈</div><div class="v">{cnt["이탈"]}</div></div></div>

<h2>점수 상위 {BC_TOP} ({prev_txt})</h2>
<p class="legend">점수 = 하락 깊이 × 평지 평탄도 × 거래량 배수 · 받는손: <span class="who f">외인</span> 평지 구간 외인 순매수 합&gt;0이고 보유율도 오름 / <span class="who i">기관</span> 기관 순매수 합&gt;0 · 평지 외인 비중 = 평지 직전 보유율→끝 (변화 %p · 원래 비중 대비 %) · 외인몫·기관몫 = 평지 구간 순매수 합 ÷ 거래량 합(비중 변화가 아니다) · 상태: 돌파(평지 고가 위) / 다지는 중 / 이탈(평지 저가 −3% 아래)</p>
<div class="scroll"><table><thead><tr><th>#</th><th></th><th class="l">종목</th><th>점수</th><th>고점</th><th>평지까지 하락</th><th>평지</th><th>밴드</th>
<th>거래량</th><th>2배↑</th><th>평지 외인 비중</th><th>외인몫(거래량)</th><th>기관몫(거래량)</th><th>지금(고점 대비)</th><th class="l">상태</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></div>

<h2>명단에서 빠진 종목</h2>
<ul class="alarm">{drop or none_li}</ul>

<h2>그래프</h2>
<p class="legend">위: 주가 (점선 회색 고점 · 녹색 평지 고가 · 주황 평지 저가) · 아래: 거래량 (녹색 = 평지 구간, 점선 = 하락 전 기준·2배)</p>
<div class="grid2">{"".join(cards)}</div>

<h2>총평</h2>
<div class="cm"><!--총평-->{comment(f"{today}_basecamp.html")}<!--/총평--></div>
<p class="note">N = new1 보유 · M = meritz 보유. 오늘 거래량은 장중이라 평지 계산과 그래프에서 뺐고, 오늘 실시간 가격은 상태 판정에만 썼어.</p>
"""
    html_ = page("Overture · Basecamp", f"{today} · 주가 {res['asof']} 기준 · ④ Basecamp 리포트", body)
    (OUT / "basecamp").mkdir(parents=True, exist_ok=True)
    (OUT / "basecamp" / f"{today}.html").write_text(html_, encoding="utf-8")
    (OUT / "latest_basecamp.html").write_text(html_, encoding="utf-8")


def main(regen: str | None = None):
    """regen = 재생성 사유(사고로 숫자가 틀린 리포트를 고친 뒤 다시 만들 때). 4종 부제에 "HH:MM 재생성(사유)"를 붙이고
    logs/auto_report.log에 한 줄 남긴다. 그날 cfg_log·cfg_rank·ledger·rank CSV도 이 시각 값으로 바뀐다(고치는 게 목적)."""
    global REGEN_NOTE
    if regen:
        REGEN_NOTE = link.REGEN_NOTE = regen_note(regen, core.now_kst())
        log = HERE / "logs" / "auto_report.log"
        log.parent.mkdir(exist_ok=True)
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"{core.now_kst():%Y-%m-%d %H:%M} 재생성 — {regen}\n")
    data = link.load_data()
    link_res = link.main(data)
    res = build(data, link_res)
    write_action(res, data["ph"])
    write_cfg(res, data)
    mp = HERE.parent / "meritz" / "portfolio_data.csv"
    m_names = set(pd.read_csv(mp)["종목명"]) if mp.exists() else set()
    write_basecamp(res, set(core.load_holdings()["종목명"]), m_names)
    # CFG에 오른 종목 기록(보조 자료 — 안 산 CFG 종목이 나중에 어떻게 됐는지도 보려고).
    log = OUT / "cfg_log.csv"
    old = pd.read_csv(log, dtype={"코드": str}) if log.exists() else pd.DataFrame()
    rows = [{"날짜": res["today"], "코드": x["코드"], "종목명": x["종목명"], "출처": "|".join(sorted(x["출처들"])),
             "하락": round(x["pct"], 2), "구간외인": round(x["fw"]["d"], 2), "구간외인상대": round(x["fw"]["rel"], 2),
             "현재가": data["live"].get(x["코드"])} for x in res["cfg"]]
    if not old.empty:
        old = old[old["날짜"] != res["today"]]
    pd.concat([old, pd.DataFrame(rows)], ignore_index=True).to_csv(log, index=False, encoding="utf-8-sig")
    save_cfg_rank(res, data["live"], data["asof"])
    # 후보 장부(바라보는 눈 빌딩 저장고) — 그날 순위에 오른 종목을 하루 한 번. 실패해도 리포트는 그대로.
    try:
        import ledger
        rp = HERE / "link_sample" / "roster.csv"
        ro = pd.read_csv(rp, dtype={"종목코드": str}) if rp.exists() else pd.DataFrame()
        ro = ro[ro["날짜"] == res["today"]] if not ro.empty else ro
        ledger.daily(res["today"], ledger.rows_from_report(res, ro, TOP))
    except Exception as e:
        print(f"[경고] 후보 장부 갱신 실패: {e}")
    print(f"[완료] {res['today']} ① Up/Down {len(res['updown'])} · Watering {len(res['watering'])} · "
          f"Fishing {len(res['fishing'])} · Basecamp {len(res['basecamp'])} · meritz {len(res['meritz'])} / ③ CFG {len(res['cfg'])}")
    for x in res["cfg"]:
        print(f"  CFG {x['종목명']} [{', '.join(sorted(x['출처들']))}] {x['basis']} {x['pct']:+.1f}% · 외인 {x['fw']['d']:+.2f}%p ({x['fw']['rel']:+.1f}%)")
    return res


if __name__ == "__main__":
    import sys
    if "--regen" in sys.argv:
        i = sys.argv.index("--regen")
        if i + 1 >= len(sys.argv) or not sys.argv[i + 1].strip():
            sys.exit('사유를 같이 줘: python morning_report.py --regen "사유"')
        main(regen=sys.argv[i + 1])
    elif "--comments" in sys.argv:
        rest = [a for a in sys.argv[1:] if a != "--comments"]
        for line in attach_comments(rest[0] if rest else core.today_kst_str()):
            print(line)
    else:
        main()
