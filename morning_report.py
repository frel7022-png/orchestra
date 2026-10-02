"""아침 리포트 3종 — 버전 1 (2026-10-02).

  ① 액션 리포트   morning_report/action/YYYY-MM-DD.html (+ latest_action.html)
     계좌 vs 혼합지수, Up/Down 재진입 후보, Watering Detect 물타기 후보, Fishing 하락 흐름,
     Today's Alarm, meritz 진입 후보. 각 칸 상위 10개(고정 기준이 아니라 우선순위), 행마다 그래프.
  ② 외인 샘플 리포트 link_sample/ (daily_link_report.py — 같은 데이터로 같이 돌린다)
  ③ CFG 리포트     morning_report/cfg/YYYY-MM-DD.html (+ latest_cfg.html)
     ①의 후보 중, 그 하락 구간 동안 외국인이 실제로 모은 종목만. 드문 게 정상.

읽는 순서는 ①→②→③(사용자 지시 — 먼저 스스로 판단한 뒤 세션 결론을 본다). 그래서 ①에는
외인 정보를 넣지 않는다(②의 결론이 ①로 새지 않게). 리포트는 그날 팩트만 쓴다.
총평은 세션이 morning_report/comments/YYYY-MM-DD_action.html, _cfg.html 에 써 두면 들어간다.

사용: python morning_report.py
"""
import html
from pathlib import Path

import pandas as pd

import daily_link_report as link
import portfolio_core as core

HERE = Path(__file__).parent
OUT = HERE / "morning_report"
TOP = 10
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
"""


def page(title, sub, body):
    return f'<title>{title}</title>\n<style>{CSS}</style>\n<div class="wrap"><h1>{title}</h1><p class="sub">{sub}</p>{body}</div>\n'


# ------------------------------------------------------------------ 계산
def build(data, link_res):
    today, ph, fh, live, quotes = data["today"], data["ph"], data["fh"], data["live"], data["quotes"]
    tx = core.load_transactions()
    h = core.load_holdings()
    state = core.load_state()
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

    # A. 계좌 vs 혼합지수
    idx_h, asset_h = core.load_index_history(), core.load_history()
    mc = core.load_market_cache()
    v = pd.to_numeric(hl["수량"]) * pd.to_numeric(hl["현재가"])
    mk = hl["종목명"].map(mc)
    ks, kq = float(v[mk == "KOSPI"].sum()), float(v[mk == "KOSDAQ"].sum())
    wk = ks / (ks + kq) if ks + kq else None
    ex = core.synthetic_kospi_ex_bigcap(idx_h, core.load_bigcap_history())
    iva_s = core.compute_index_vs_account(tx, asset_h, ex, state["initial"], state.get("fee_rate", 0.0), kospi_weight=wk)
    iva_m = core.compute_index_vs_account(tx, asset_h, idx_h, state["initial"], state.get("fee_rate", 0.0), kospi_weight=wk)
    res["index"] = {"ex": iva_s.get("latest", {}), "main": iva_m.get("latest", {}),
                    "asof": str(asset_h["날짜"].max()) if not asset_h.empty else ""}

    # B. Up/Down 재진입 후보 — 마지막 매도가 대비 많이 빠진 순
    closed = core.get_closed_out_last_sells(h, tx)
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

    # E. Today's Alarm — 앱과 같은 함수, 입력만 리포트 데이터로 구성
    fp = pd.DataFrame([{"종목명": f["종목명"], "최초가": f["기준가"], "최근가": f["현재가"],
                        "전일대비": f["전일"], "기준일": f["기준일"]} for f in fish])
    res["alarm"] = core.compute_todays_alarm(tx, hl, fp, fh, ph)

    # F. meritz 진입 후보 — new1 보유 중 평단 대비 많이 빠졌는데 meritz엔 없는 종목
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
    mz.sort(key=lambda x: x["pct_first"])
    res["meritz"] = mz[:TOP]

    # ③ CFG — ①의 후보 중, 그 하락 구간 동안 외인이 실제로 늘었고 외인 상태가 나쁘지 않은 종목
    stats = link_res["stats"]
    def fwin(code, start):
        g = fh[(fh["종목코드"] == code) & (fh["날짜"] >= start)].sort_values("날짜")
        if len(g) < 2:
            return None
        a, b = float(g["외국인보유율"].iloc[0]), float(g["외국인보유율"].iloc[-1])
        return {"시작": g["날짜"].iloc[0], "시작값": a, "지금": b, "d": b - a,
                "rel": (b - a) / a * 100 if a else 0, "끝": g["날짜"].iloc[-1]}
    srcs = ([("Up/Down", x["코드"], x["종목명"], x["매도일"], x["pct"], "매도가 대비") for x in res["updown"]]
            + [("Watering", x["코드"], x["종목명"], x["마지막일"], x["pct_last"], "마지막 매수가 대비") for x in res["watering"]]
            + [("Fishing", x["코드"], x["종목명"], x["기준일"], x["cum"], "기준일 대비") for x in res["fishing"]]
            + [("meritz 후보", x["코드"], x["종목명"], x["최초일"], x["pct_first"], "new1 최초진입가 대비") for x in res["meritz"]])
    cfg = []
    for src, c, n, start, pct, basis in srcs:
        if not c or pct is None or pct > -3:
            continue
        s = stats.get(c)
        w = fwin(c, start)
        if not w or not s or s.get("착시") or s.get("꺾임"):
            continue
        if w["d"] >= 0.3 or (w["시작값"] >= 3 and w["rel"] >= 5):
            cfg.append({"출처": src, "코드": c, "종목명": n, "시작": start, "pct": pct, "basis": basis,
                        "fw": w, "s": s, "score": abs(pct) * max(w["rel"], 0)})
    merged = {}
    for x in cfg:
        if x["코드"] not in merged or x["score"] > merged[x["코드"]]["score"]:
            merged[x["코드"]] = {**x, "출처들": set()}
        merged[x["코드"]]["출처들"].add(x["출처"])
    res["cfg"] = sorted(merged.values(), key=lambda x: -x["score"])
    res["stats"] = stats
    return res


# ------------------------------------------------------------------ HTML
def comment(name):
    p = OUT / "comments" / name
    return p.read_text(encoding="utf-8") if p.exists() else '<p class="note">아직 작성 전</p>'


def write_action(res, ph):
    today, own = res["today"], res["own"]
    ix, im = res["index"]["ex"], res["index"]["main"]
    def kv(k, lab):
        a = ix.get(k) if k != "main벤치" else im.get("벤치")
        if not a:
            return ""
        return f'<div><div class="k">{lab}</div><div class="v">{sign(a[0] * 100, 2)}</div><div class="k">당일 {sign(a[1] * 100, 2)}</div></div>'
    kpi = (kv("벤치", "혼합지수(삼성·하이닉스 제외)") + kv("main벤치", "혼합지수(일반)") + kv("계좌", "내 계좌")
           + kv("주식", "내 주식(예수금 제외)"))

    # Up/Down
    rows, cards = [], []
    for i, x in enumerate(res["updown"], 1):
        rows.append(f'<tr><td class="rk">{i}</td><td class="l">{esc(x["종목명"])}</td><td>{x["매도일"][5:]}</td>'
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
    rows, cards = [], []
    for x in res["fishing"]:
        mv = "" if res["fishing_first"] else (
            '<span class="new">NEW</span>' if x["이전"] is None or x["이전"] > TOP else
            (f'<span class="up">▲{x["이전"] - x["순위"]}</span>' if x["이전"] > x["순위"] else
             f'<span class="dn">▼{x["순위"] - x["이전"]}</span>' if x["이전"] < x["순위"] else '<span class="mut">-</span>'))
        st_, cls = x["상태"]
        rows.append(f'<tr><td class="rk">{x["순위"]}</td><td>{mv}</td><td class="l">{esc(x["종목명"])}<span class="own">{own(x["종목명"])}</span></td>'
                    f'<td>{sign(x["cum"])}</td><td>{sign(x["저점누적"])} ({x["저점일"][5:]})</td><td>{sign(x["반등"])}</td>'
                    f'<td>{x["저점후"]}일</td><td>{sign(x["최근3일"])}</td><td class="l"><span class="bd st {cls}">{st_}</span></td></tr>')
        cards.append(card(f'{x["순위"]}. {esc(x["종목명"])}',
                          f'기준일 {x["기준일"][5:]} 대비 {x["cum"]:+.1f}% · 바닥 {x["저점누적"]:+.1f}% ({x["저점일"][5:]}) 이후 {x["반등"]:+.1f}% · {st_}',
                          svg_line(x["pts"], refs=[("기준가", x["기준가"], "zero")],
                                   marks=[(x["저점일"], x["저점"], "low")], pct_base=x["기준가"])))
    fi_html = (f'<div class="scroll"><table><thead><tr><th>#</th><th></th><th class="l">종목</th><th>누적</th><th>바닥</th>'
               f'<th>바닥 대비</th><th>바닥 후</th><th>최근 3일</th><th class="l">상태</th></tr></thead>'
               f'<tbody>{"".join(rows)}</tbody></table></div><div class="grid2">{"".join(cards)}</div>')

    # Alarm
    al = res["alarm"]
    items = []
    for r in al.get("watering", []):
        items.append(f'<li>물타기 급락: <b>{esc(r["종목명"])}</b> 전일 {sign(r["등락률"])} · 마지막 매수가 대비 {sign(r["pct_last"])}</li>')
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
                    f'<td>{sign(x["pct_first"])}</td><td>{sign(x["pct_avg"])}</td></tr>')
        cards.append(card(f'{i}. {esc(x["종목명"])}',
                          f'new1 최초 {x["최초가"]:,.0f}({x["최초일"][5:]}) · 평단 {x["평단가"]:,.0f} → 지금 {x["현재가"]:,.0f}',
                          svg_line(x["pts"], refs=[("최초", x["최초가"], "first"), ("평단", x["평단가"], "avg"),
                                                    ("−5%", x["최초가"] * 0.95, "last")],
                                   marks=[(d, p, "buy") for d, p in x["buys"]])))
    mz_html = (f'<div class="scroll"><table><thead><tr><th>#</th><th class="l">종목</th><th>new1 최초 진입</th><th>매수</th>'
               f'<th>최초진입가 대비</th><th>평단 대비</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
               f'<div class="grid2">{"".join(cards)}</div>')

    body = f"""
<h2>계좌 vs 혼합지수 (8/14 기준 누적)</h2>
<div class="kpi">{kpi}</div>
<p class="note">계좌·주식은 asset_history 최신일({res["index"]["asof"]}) 기준. 혼합지수는 보유종목 코스피/코스닥 평가금액 비중으로 가중.</p>

<h2>Up/Down — 재진입 후보 (매도가 대비 많이 빠진 순, 전체 {res["updown_n"]}종목 중 상위 {TOP})</h2>
<p class="legend">빨간 점선 = 마지막 매도가 · 빨간 점 = 매도일</p>
{ud_html}

<h2>Watering Detect — 물타기 후보 (마지막 매수가 대비 많이 빠진 순, 물타기 중 {res["watering_n"]}종목 중 상위 {TOP})</h2>
<p class="legend">점선: 회색 최초진입가 · 녹색 평단 · 주황 마지막 매수가 · 빨간 원 = 매수 시점</p>
{wd_html}

<h2>Fishing — 관심종목 누적 하락 상위 {TOP} (전체 {res["fishing_n"]}종목)</h2>
<p class="legend">세로축 = 기준일 대비 % · 파란 점 = 기간 중 바닥 · 상태: 빠지는 중(바닥이 최근) / 바닥 다지는 중 / 반등 중(바닥 대비 +3%↑이고 최근 3일 상승)</p>
{fi_html}

<h2>Today's Alarm</h2>
{al_html}

<h2>meritz 진입 후보 (new1 보유 · meritz 미보유, new1 최초진입가 대비 많이 빠진 순)</h2>
<p class="legend">점선: 회색 new1 최초진입가 · 녹색 new1 평단 · 주황 최초진입가 −5%(meritz 문턱)</p>
{mz_html}

<h2>총평</h2>
<div class="cm">{comment(f"{today}_action.html")}</div>
<p class="note">속도 배지: 총 하락 중 절반 이상이 최근 3거래일에 났으면 급락, 아니면 완만. N = new1 보유 · M = meritz 보유. 외인 정보는 일부러 넣지 않음(② 외인 리포트에서 독립적으로 보기 위해).</p>
"""
    html_ = page("Overture · Action", f"{today} · 주가 {res['asof']} 기준 · ① 액션 리포트", body)
    (OUT / "action").mkdir(parents=True, exist_ok=True)
    (OUT / "action" / f"{today}.html").write_text(html_, encoding="utf-8")
    (OUT / "latest_action.html").write_text(html_, encoding="utf-8")


def write_cfg(res, data):
    today, ph, fh, live = res["today"], data["ph"], data["fh"], data["live"]
    cards = []
    for i, x in enumerate(res["cfg"], 1):
        s, w = x["s"], x["fw"]
        pts = series_from(ph, x["코드"], x["시작"], live.get(x["코드"]), today)
        left = svg_line(pts, refs=[("시작", pts[0][1], "zero")] if pts else (), marks=[], pct_base=pts[0][1] if pts else None)
        right = link.svg_chart(link.price_series(ph, x["코드"], s["기준일"]), link.foreign_series(fh, x["코드"], s["기준일"]),
                               live.get(x["코드"]), today)
        state = ("명단 안" if s.get("자격") else "다시 모으는 중" if s.get("재매집") else "조건 밖")
        cards.append(
            f'<div class="card"><div class="ch">{i}. {esc(x["종목명"])} <span class="bd st">{" · ".join(sorted(x["출처들"]))}</span>'
            f'<span class="meta">{x["basis"]} {x["pct"]:+.1f}% ({x["시작"][5:]}부터) · 같은 구간 외인 '
            f'{w["시작값"]:.2f}% ({w["시작"][5:]}) → {w["지금"]:.2f}% ({w["끝"][5:]}) = {w["d"]:+.2f}%p (상대 {w["rel"]:+.1f}%) · ② 상태: {state}</span></div>'
            f'<div class="pair"><div><div class="note">① 그 기능의 하락 구간 주가</div>{left}</div>'
            f'<div><div class="note">② 외인 리포트 그래프 (기준일부터)</div>{right}</div></div></div>')
    body = f"""
<p class="note">① 액션 리포트 후보(Up/Down·Watering·Fishing·meritz 후보) 중, <b>그 기능이 잰 하락 구간 동안</b> 외국인 보유율이 실제로 늘었고(+0.3%p 이상 또는 상대 +5% 이상), ② 외인 리포트에서 착시·꺾임이 아닌 종목. 하락 3% 미만은 제외. 드문 게 정상.</p>
<h2>오늘의 CFG ({len(res["cfg"])}종목)</h2>
<div class="grid2" style="grid-template-columns:1fr">{"".join(cards) or '<p class="note">오늘은 없음</p>'}</div>
<h2>총평</h2>
<div class="cm">{comment(f"{today}_cfg.html")}</div>
"""
    html_ = page("Overture · CFG", f"{today} · 주가 {res['asof']} 기준 · ③ 두 신호가 겹치는 종목", body)
    (OUT / "cfg").mkdir(parents=True, exist_ok=True)
    (OUT / "cfg" / f"{today}.html").write_text(html_, encoding="utf-8")
    (OUT / "latest_cfg.html").write_text(html_, encoding="utf-8")


def main():
    data = link.load_data()
    link_res = link.main(data)
    res = build(data, link_res)
    write_action(res, data["ph"])
    write_cfg(res, data)
    # CFG에 오른 종목 기록(보조 자료 — 안 산 CFG 종목이 나중에 어떻게 됐는지도 보려고).
    log = OUT / "cfg_log.csv"
    old = pd.read_csv(log, dtype={"코드": str}) if log.exists() else pd.DataFrame()
    rows = [{"날짜": res["today"], "코드": x["코드"], "종목명": x["종목명"], "출처": "|".join(sorted(x["출처들"])),
             "하락": round(x["pct"], 2), "구간외인": round(x["fw"]["d"], 2), "구간외인상대": round(x["fw"]["rel"], 2),
             "현재가": data["live"].get(x["코드"])} for x in res["cfg"]]
    if not old.empty:
        old = old[old["날짜"] != res["today"]]
    pd.concat([old, pd.DataFrame(rows)], ignore_index=True).to_csv(log, index=False, encoding="utf-8-sig")
    print(f"[완료] {res['today']} ① Up/Down {len(res['updown'])} · Watering {len(res['watering'])} · "
          f"Fishing {len(res['fishing'])} · meritz {len(res['meritz'])} / ③ CFG {len(res['cfg'])}")
    for x in res["cfg"]:
        print(f"  CFG {x['종목명']} [{', '.join(sorted(x['출처들']))}] {x['basis']} {x['pct']:+.1f}% · 외인 {x['fw']['d']:+.2f}%p ({x['fw']['rel']:+.1f}%)")
    return res


if __name__ == "__main__":
    main()
