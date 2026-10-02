"""매일 외인 다이버전스 샘플 리포트 (link_sample/) — 버전 1 (2026-10-02).

"주가는 빠지는데 외국인은 모으는" 종목 10개를 명단으로 관리하고, 매일 HTML 리포트를 만든다.
앱 화면이 아니라 세션이 매일 첫 대화 때 돌려서 아티팩트로 보여주는 용도(사용자 지시).

규칙
- 대상: watchlist 전체. P(기준일 대비 주가%)·dF(기준일 대비 외인비중 %p)는 Link
  (`compute_link_candidates`)와 같은 값 — Foreigner/Fishing과 뿌리가 같다.
- 자격: P<0 이고 dF>0. 단 **착시**(기간 중 외인 누적 증가 고점 대비 GIVEBACK_LIMIT 이상
  반납)는 제외 — "지금 +8%p라 많이 모은 것 같지만 열흘 전엔 +15%p였다"를 거르기 위함.
- 강도: 완화 단계(TIERS, 위일수록 셈) 우선, 같은 단계면 dF 큰 쪽.
- 명단 10개는 이어간다: 어제 명단을 오늘 값으로 다시 평가해 자격 잃은 종목은 탈락,
  빈자리는 센 순으로 채우고, 명단 밖 종목이 10위보다 세면 교체. 항상 10개를 채운다.
- 명단에 처음 들어온 날의 주가를 기록해 이후 실제로 올랐는지 추적한다.

사용: python daily_link_report.py   → link_sample/daily/YYYY-MM-DD.html, link_sample/latest.html,
                                      link_sample/roster.csv(날짜별 명단 누적)
"""
import html
import tomllib
from datetime import datetime
from pathlib import Path

import pandas as pd

import portfolio_core as core

HERE = Path(__file__).parent
OUT = HERE / "link_sample"
ROSTER = OUT / "roster.csv"
ROSTER_SIZE = 10
GIVEBACK_LIMIT = 0.30

# (라벨, 기준외인비중 이상, 주가 이하(%), 외인 증가 이상(%p)) — 위일수록 셈
TIERS = [
    ("이상", 10, -15, 5.0),
    ("강", 5, -15, 5.0),
    ("강", 10, -10, 3.0),
    ("중", 5, -10, 3.0),
    ("중", 5, -10, 2.0),
    ("약", 3, -7, 1.5),
    ("약", 3, -5, 1.0),
    ("약", 3, -5, 0.5),
]
FILL = ("보충", None, None, None)  # 위 단계에 못 걸렸지만 P<0, dF>0인 종목(명단 채우기용)

ROSTER_COLS = ["날짜", "순위", "종목코드", "종목명", "단계", "단계번호", "P", "기준외인비중",
               "현재외인비중", "dF", "고점dF", "반납률", "상대증가", "현재가", "진입일", "진입가"]


def tier_of(row) -> tuple[int, str]:
    for i, (lab, base, p, d) in enumerate(TIERS):
        if row["기준외인비중"] >= base and row["P"] <= p and row["dF"] >= d:
            return i, lab
    return len(TIERS), FILL[0]


def foreign_series(fh: pd.DataFrame, code: str, base_date: str) -> pd.DataFrame:
    g = fh[(fh["종목코드"] == code) & (fh["날짜"] >= base_date)].sort_values("날짜")
    return g[["날짜", "외국인보유율"]].dropna()


def price_series(ph: pd.DataFrame, code: str, base_date: str) -> pd.DataFrame:
    g = ph[(ph["종목코드"] == code) & (ph["날짜"] >= base_date)].sort_values("날짜")
    return g[["날짜", "종가"]].dropna()


def svg_chart(ps: pd.DataFrame, fs: pd.DataFrame, live_price: float | None, today: str) -> str:
    """주가(기준일 대비 %, 왼쪽 축)와 외인 보유율(%, 오른쪽 축) 두 축 선 그래프. 외인 고점에 점."""
    if ps.empty or fs.empty:
        return '<div class="nochart">데이터 부족</div>'
    ps = ps.copy()
    if live_price and ps["날짜"].iloc[-1] < today:
        ps = pd.concat([ps, pd.DataFrame([{"날짜": today, "종가": live_price}])], ignore_index=True)
    base_px = ps["종가"].iloc[0]
    ps["pct"] = (ps["종가"] / base_px - 1) * 100
    dates = sorted(set(ps["날짜"]) | set(fs["날짜"]))
    xi = {d: i for i, d in enumerate(dates)}
    W, H, L, R, T, B = 340, 150, 34, 38, 10, 22
    iw, ih = W - L - R, H - T - B

    def sx(d):
        return L + iw * xi[d] / max(1, len(dates) - 1)

    plo, phi = min(ps["pct"].min(), -1), max(ps["pct"].max(), 1)
    flo, fhi = fs["외국인보유율"].min(), fs["외국인보유율"].max()
    pad = max(0.2, (fhi - flo) * 0.1)
    flo, fhi = flo - pad, fhi + pad

    def sp(v):
        return T + ih * (phi - v) / (phi - plo)

    def sf(v):
        return T + ih * (fhi - v) / (fhi - flo)

    p_pts = " ".join(f"{sx(d):.1f},{sp(v):.1f}" for d, v in zip(ps["날짜"], ps["pct"]))
    f_pts = " ".join(f"{sx(d):.1f},{sf(v):.1f}" for d, v in zip(fs["날짜"], fs["외국인보유율"]))
    pk = fs.loc[fs["외국인보유율"].idxmax()]
    zero = sp(0)
    lab = lambda x, y, t, a="start", c="ax": f'<text class="{c}" x="{x:.1f}" y="{y:.1f}" text-anchor="{a}">{t}</text>'
    parts = [
        f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="주가와 외인 보유율 추이">',
        f'<line class="grid" x1="{L}" x2="{W - R}" y1="{zero:.1f}" y2="{zero:.1f}"/>',
        f'<polyline class="pl" points="{p_pts}"/>',
        f'<polyline class="fl" points="{f_pts}"/>',
        f'<circle class="pk" cx="{sx(pk["날짜"]):.1f}" cy="{sf(pk["외국인보유율"]):.1f}" r="3.2"/>',
        lab(L - 4, T + 8, f"{phi:+.0f}%", "end", "ax p"),
        lab(L - 4, zero + 3, "0%", "end", "ax p"),
        lab(L - 4, T + ih, f"{plo:+.0f}%", "end", "ax p"),
        lab(W - R + 4, T + 8, f"{fhi:.1f}", "start", "ax f"),
        lab(W - R + 4, T + ih, f"{flo:.1f}", "start", "ax f"),
        lab(L, H - 6, dates[0][5:]),
        lab(W - R, H - 6, dates[-1][5:], "end"),
        "</svg>",
    ]
    return "".join(parts)


def main():
    today = datetime.now(core.KST).strftime("%Y-%m-%d") if hasattr(core, "KST") else core.today_kst_str()
    sec = tomllib.load(open(HERE / ".streamlit/secrets.toml", "rb"))["supabase"]
    url, key = sec["url"], sec.get("anon_key") or sec.get("key")
    ph = core.load_watchlist_history_db(url, key)
    fh = core.load_investor_flow_db(url, key)
    quotes, _ = core.fetch_quotes(list(ph["종목코드"].unique()))
    live = {c: v["price"] for c, v in quotes.items() if v.get("price")}
    cand = core.compute_link_candidates(ph, fh, live_quotes=live)

    # 종목별 현재 상태 + 착시(고점 대비 반납) 계산
    stats = {}
    for _, r in cand.iterrows():
        fs = foreign_series(fh, r["종목코드"], r["기준일"])
        if fs.empty:
            continue
        peak = float(fs["외국인보유율"].max() - r["기준외인비중"])
        cur = float(r["dF"])
        give = (peak - cur) / peak if peak > 0 else 0.0
        ti, tl = tier_of(r)
        stats[r["종목코드"]] = {
            **r.to_dict(), "고점dF": peak, "반납률": give, "단계번호": ti, "단계": tl,
            "상대증가": cur / r["기준외인비중"] * 100 if r["기준외인비중"] else None,
            "자격": (r["P"] < 0) and (cur > 0) and (give < GIVEBACK_LIMIT),
            "착시": (r["P"] < 0) and (cur > 0) and (give >= GIVEBACK_LIMIT),
        }

    def strength(code):
        s = stats[code]
        return (s["단계번호"], -s["dF"])

    OUT.mkdir(exist_ok=True)
    (OUT / "daily").mkdir(exist_ok=True)
    hist = pd.read_csv(ROSTER, dtype={"종목코드": str}) if ROSTER.exists() else pd.DataFrame(columns=ROSTER_COLS)
    prev_dates = sorted(d for d in hist["날짜"].unique() if d < today)
    prev = hist[hist["날짜"] == prev_dates[-1]] if prev_dates else hist.iloc[0:0]
    entry = {}  # 종목코드 → (진입일, 진입가): 지금까지 명단에 있던 이력 중 현재 연속 구간 시작
    for _, r in prev.iterrows():
        entry[r["종목코드"]] = (r["진입일"], r["진입가"])

    dropped = []
    members = []
    for _, r in prev.sort_values("순위").iterrows():
        c = r["종목코드"]
        s = stats.get(c)
        if s is None or not s["자격"]:
            reason = "착시(외인 고점 대비 반납)" if s and s["착시"] else "자격 상실(주가 반등 또는 외인 감소)"
            dropped.append((r["종목명"], reason))
        else:
            members.append(c)
    pool = sorted((c for c, s in stats.items() if s["자격"] and c not in members), key=strength)
    added, swapped = [], []
    while len(members) < ROSTER_SIZE and pool:
        c = pool.pop(0)
        members.append(c)
        added.append(c)
    while pool:
        weakest = max(members, key=strength)
        if strength(pool[0]) < strength(weakest):
            c = pool.pop(0)
            members.remove(weakest)
            members.append(c)
            added.append(c)
            swapped.append((stats[weakest]["종목명"], stats[c]["종목명"]))
        else:
            break
    members.sort(key=strength)
    prev_rank = {r["종목코드"]: int(r["순위"]) for _, r in prev.iterrows()}

    rows = []
    for i, c in enumerate(members, 1):
        s = stats[c]
        e = entry.get(c) if c not in added else None
        ein, epx = e if e else (today, s["현재가"])
        rows.append({"날짜": today, "순위": i, "종목코드": c, "종목명": s["종목명"], "단계": s["단계"],
                     "단계번호": s["단계번호"], "P": round(s["P"], 2), "기준외인비중": s["기준외인비중"],
                     "현재외인비중": s["현재외인비중"], "dF": round(s["dF"], 2), "고점dF": round(s["고점dF"], 2),
                     "반납률": round(s["반납률"], 3), "상대증가": round(s["상대증가"] or 0, 1),
                     "현재가": s["현재가"], "진입일": ein, "진입가": epx})
    today_df = pd.DataFrame(rows, columns=ROSTER_COLS)
    hist = pd.concat([hist[hist["날짜"] != today], today_df], ignore_index=True)
    hist.to_csv(ROSTER, index=False, encoding="utf-8-sig")

    # 추적: 지금까지 명단에 들어왔던 모든 종목의 진입 이후 주가
    holds_n = set(core.load_holdings()["종목명"])
    mp = HERE.parent / "meritz" / "portfolio_data.csv"
    holds_m = set(pd.read_csv(mp)["종목명"]) if mp.exists() else set()
    track = []
    for c, g in hist.groupby("종목코드"):
        first = g.sort_values("날짜").iloc[0]
        now_px = live.get(c) or (stats[c]["현재가"] if c in stats else None)
        if now_px and first["진입가"]:
            track.append((first["종목명"], first["날짜"], float(first["진입가"]), float(now_px),
                          (float(now_px) / float(first["진입가"]) - 1) * 100, c in members))
    track.sort(key=lambda t: t[1])

    illusion = sorted((v for v in stats.values() if v["착시"]), key=lambda v: -v["고점dF"])[:8]
    write_html(today, today_df, stats, ph, fh, live, prev_rank, added, swapped, dropped,
               track, holds_n, holds_m, len(prev_dates) == 0, illusion)
    print(f"[완료] {today} 명단 {len(today_df)}개 · 신규 {len(added)} · 탈락 {len(dropped)}")
    for _, r in today_df.iterrows():
        print(f"  {r['순위']:>2}. {r['종목명']} [{r['단계']}] P {r['P']:+.1f}% dF {r['dF']:+.2f}%p 반납 {r['반납률']:.0%}")


def write_html(today, df, stats, ph, fh, live, prev_rank, added, swapped, dropped, track,
               holds_n, holds_m, first_run, illusion):
    esc = html.escape
    def sign(v, d=1, unit="%"):
        cls = "up" if v > 0 else ("dn" if v < 0 else "")
        return f'<span class="{cls}">{v:+.{d}f}{unit}</span>'

    rows_html, cards = [], []
    for _, r in df.iterrows():
        c = r["종목코드"]
        pr = prev_rank.get(c)
        if first_run:
            mv = ""
        elif c in added or pr is None:
            mv = '<span class="new">NEW</span>'
        elif pr > r["순위"]:
            mv = f'<span class="up">▲{pr - r["순위"]}</span>'
        elif pr < r["순위"]:
            mv = f'<span class="dn">▼{r["순위"] - pr}</span>'
        else:
            mv = '<span class="mut">-</span>'
        own = " ".join(t for t, ok in (("N", r["종목명"] in holds_n), ("M", r["종목명"] in holds_m)) if ok)
        rows_html.append(
            f'<tr><td class="rk">{r["순위"]}</td><td class="mv">{mv}</td>'
            f'<td class="nm">{esc(r["종목명"])}<span class="own">{own}</span></td>'
            f'<td><span class="tier t{r["단계번호"]}">{r["단계"]}</span></td>'
            f'<td>{sign(r["P"])}</td><td>{r["기준외인비중"]:.2f} → {r["현재외인비중"]:.2f}</td>'
            f'<td>{sign(r["dF"], 2, "%p")}</td><td>{r["상대증가"]:+.0f}%</td>'
            f'<td>{r["반납률"]:.0%}</td></tr>')
        s = stats[c]
        chart = svg_chart(price_series(ph, c, s["기준일"]), foreign_series(fh, c, s["기준일"]), live.get(c), today)
        cards.append(
            f'<div class="card"><div class="ch"><span class="rk2">{r["순위"]}</span> {esc(r["종목명"])}'
            f'<span class="meta">기준일 {s["기준일"][5:]} · 외인 {r["기준외인비중"]:.2f}% → 고점 {r["기준외인비중"] + r["고점dF"]:.2f}% → 지금 {r["현재외인비중"]:.2f}%</span></div>'
            f'{chart}</div>')

    ev = []
    if first_run:
        ev.append("<li>첫 명단이라 순위 변동은 내일부터 표시돼요.</li>")
    else:
        for n in added:
            ev.append(f"<li>새로 들어옴: <b>{esc(stats[n]['종목명'])}</b></li>")
        for a, b in swapped:
            ev.append(f"<li>교체: {esc(a)} → <b>{esc(b)}</b> (더 센 신호)</li>")
        for n, why in dropped:
            ev.append(f"<li>탈락: {esc(n)} · {why}</li>")
        if not ev:
            ev.append("<li>어제와 명단 구성이 같아요.</li>")

    tr = "".join(
        f'<tr><td class="nm">{esc(n)}{"" if on else " <span class=mut>(명단 밖)</span>"}</td><td>{d[5:]}</td>'
        f'<td>{p0:,.0f}</td><td>{p1:,.0f}</td><td>{sign(ch)}</td></tr>'
        for n, d, p0, p1, ch, on in track)

    ill = "".join(
        f'<tr><td class="nm">{esc(v["종목명"])}</td><td>{sign(v["P"])}</td>'
        f'<td>{v["기준외인비중"]:.2f} → <b>{v["기준외인비중"] + v["고점dF"]:.2f}</b> → {v["현재외인비중"]:.2f}</td>'
        f'<td>{v["반납률"]:.0%}</td></tr>'
        for v in illusion) or '<tr><td class="nm mut" colspan="4">없음</td></tr>'
    tiers_doc = " · ".join(f"{lab} 외인{b}%↑ 주가{p}%↓ +{d}%p↑" for lab, b, p, d in TIERS)
    page = f"""<title>Link Sample</title>
<style>
:root{{--bg:#f6f7f9;--card:#fff;--ink:#191b21;--soft:#5b606b;--faint:#8b909c;--rule:#e0e3ea;
--up:#c9313d;--dn:#2c5fc4;--fl:#15794c;--new:#15794c;--t0:#7a1f8f;--t1:#c9313d;--t3:#b4690e;--t5:#5b606b;--t8:#8b909c}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--bg:#13151a;--card:#1a1d24;--ink:#e8eaef;--soft:#a2a7b3;--faint:#6e7480;--rule:#2b2f38;--up:#f0555f;--dn:#6f9bef;--fl:#4cc38a;--new:#4cc38a;--t0:#c98ae0;--t1:#f0555f;--t3:#e0a45e;--t5:#a2a7b3;--t8:#6e7480;color-scheme:dark}}}}
:root[data-theme="dark"]{{--bg:#13151a;--card:#1a1d24;--ink:#e8eaef;--soft:#a2a7b3;--faint:#6e7480;--rule:#2b2f38;--up:#f0555f;--dn:#6f9bef;--fl:#4cc38a;--new:#4cc38a;--t0:#c98ae0;--t1:#f0555f;--t3:#e0a45e;--t5:#a2a7b3;--t8:#6e7480;color-scheme:dark}}
body{{background:var(--bg);color:var(--ink);font-family:"Pretendard","Apple SD Gothic Neo","Malgun Gothic",system-ui,sans-serif;margin:0;line-height:1.6}}
.wrap{{max-width:820px;margin:0 auto;padding:28px 16px 64px}}
h1{{font-size:1.5rem;margin:0}} .sub{{color:var(--soft);font-size:13px;margin:4px 0 0}}
h2{{font-size:1rem;margin:30px 0 8px;padding-top:14px;border-top:1px solid var(--rule)}}
.scroll{{overflow-x:auto}} table{{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}}
th,td{{padding:7px 8px;border-bottom:1px solid var(--rule);text-align:right;white-space:nowrap}}
th{{color:var(--soft);font-weight:600;font-size:12px}} td.nm,th.nm{{text-align:left}} td.rk{{font-weight:700}} td.mv{{text-align:center}}
.up{{color:var(--up)}} .dn{{color:var(--dn)}} .mut{{color:var(--faint)}} .new{{color:var(--new);font-weight:700;font-size:11px}}
.own{{font-size:10px;color:var(--faint);margin-left:5px;letter-spacing:.05em}}
.tier{{font-size:11px;font-weight:700;padding:1px 7px;border-radius:999px;border:1px solid currentColor}}
.t0{{color:var(--t0)}} .t1,.t2{{color:var(--t1)}} .t3,.t4{{color:var(--t3)}} .t5,.t6,.t7{{color:var(--t5)}} .t8{{color:var(--t8)}}
.grid2{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:10px}}
.card{{background:var(--card);border:1px solid var(--rule);border-radius:10px;padding:10px 10px 4px;min-width:0}}
.ch{{font-weight:600;font-size:13.5px}} .rk2{{color:var(--faint);margin-right:2px}}
.meta{{display:block;font-weight:400;font-size:11.5px;color:var(--soft)}}
svg{{width:100%;height:auto;display:block}} .pl{{fill:none;stroke:var(--dn);stroke-width:1.8}} .fl{{fill:none;stroke:var(--fl);stroke-width:1.8}}
.pk{{fill:var(--fl)}} .grid{{stroke:var(--rule);stroke-dasharray:3 3}} .ax{{font-size:9.5px;fill:var(--faint)}} .ax.p{{fill:var(--dn)}} .ax.f{{fill:var(--fl)}}
.legend{{font-size:12px;color:var(--soft);margin:4px 0 10px}} .legend i{{display:inline-block;width:14px;height:3px;vertical-align:middle;margin:0 4px 0 10px}}
ul{{padding-left:18px;font-size:13.5px}} .note{{font-size:12px;color:var(--faint)}}
.nochart{{font-size:12px;color:var(--faint);padding:20px 0}}
</style>
<div class="wrap">
<h1>Link Sample</h1>
<p class="sub">{today} · 주가는 빠지는데 외국인은 모으는 종목 10 · 착시(외인 고점 대비 {GIVEBACK_LIMIT:.0%} 이상 반납) 제외</p>

<h2>오늘의 명단</h2>
<div class="scroll"><table>
<thead><tr><th>#</th><th></th><th class="nm">종목</th><th>단계</th><th>주가</th><th>외인 비중</th><th>외인 증가</th><th>상대</th><th>고점 반납</th></tr></thead>
<tbody>{''.join(rows_html)}</tbody></table></div>
<p class="note">N = new1 보유 · M = meritz 보유. 주가·외인은 기준일(가격추적 시작일) 대비. 주가는 지금 시세, 외인은 DB 최신일.</p>

<h2>변화</h2>
<ul>{''.join(ev)}</ul>

<h2>추이</h2>
<div class="legend"><i style="background:var(--dn)"></i>주가(기준일 대비 %, 왼쪽) <i style="background:var(--fl)"></i>외인 보유율(%, 오른쪽) · 점 = 외인 고점</div>
<div class="grid2">{''.join(cards)}</div>

<h2>착시로 걸러진 종목</h2>
<p class="note">지금 숫자만 보면 외국인이 모은 것 같지만, 기간 중 고점에서 이미 {GIVEBACK_LIMIT:.0%} 넘게 덜어낸 종목.</p>
<div class="scroll"><table>
<thead><tr><th class="nm">종목</th><th>주가</th><th>외인 비중 (기준 → <b>고점</b> → 지금)</th><th>고점 대비 반납</th></tr></thead>
<tbody>{ill}</tbody></table></div>

<h2>추적: 명단에 들어온 뒤 주가</h2>
<div class="scroll"><table>
<thead><tr><th class="nm">종목</th><th>진입일</th><th>진입가</th><th>지금</th><th>변화</th></tr></thead>
<tbody>{tr}</tbody></table></div>

<p class="note">단계: {esc(tiers_doc)} · 보충 = P&lt;0, 외인&gt;0이지만 위 단계 미달(명단 채우기용)</p>
</div>
"""
    (OUT / "daily" / f"{today}.html").write_text(page, encoding="utf-8")
    (OUT / "latest.html").write_text(page, encoding="utf-8")


if __name__ == "__main__":
    main()
