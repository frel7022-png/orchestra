"""친구 창구 브리프 — 텔레그램 허브(Desktop/new1_friend)용, 읽기 전용 (2026-10-15 신설, 대표 프레임).

앱 첫 화면에서 보이는 "사실"만 평문으로 낸다. 분석·추천(Fishing/Up-Down/Basecamp/Foreigner/Link/CFG,
Watering Detect, Alarm, Pit Stop, Claude's Read, 아침 리포트)은 다루지 않는다 — 그건 대표 창구 몫.
다른 계좌 데이터는 읽지도 출력하지도 않는다. 파일에 아무것도 쓰지 않는다.

    python C:/Users/frel/Desktop/new1/friend_brief.py                 # 브리프 (평가손익 블록 + Today's Take + 오늘 매수·매도)
    python C:/Users/frel/Desktop/new1/friend_brief.py trades [날짜]    # 그날 매수·매도 (기본: 가장 최근 매매일)
    python C:/Users/frel/Desktop/new1/friend_brief.py period [N]       # 최근 N거래일(기본 5) 지수 vs 내 계좌·내 주식
    python C:/Users/frel/Desktop/new1/friend_brief.py holding 종목명   # 그 종목 보유 현황
    python C:/Users/frel/Desktop/new1/friend_brief.py month [YYYY-MM]  # 그달 실현손익
    python C:/Users/frel/Desktop/new1/friend_brief.py holdings [N]     # 보유종목 오늘 등락률 상위·하위 N개(기본 10)
    python C:/Users/frel/Desktop/new1/friend_brief.py holdings all     # 전체 보유 목록(섹터·수량·평단·손익·비중) — 그때그때 질문의 원자료
    python C:/Users/frel/Desktop/new1/friend_brief.py new [날짜]       # 그날 새로 들어온 종목(기본: 가장 최근 매매일)
    python C:/Users/frel/Desktop/new1/friend_brief.py stats            # 누적 매수·매도 건수·금액, 이익/손실 건수, 사이클 수
    python C:/Users/frel/Desktop/new1/friend_brief.py history 종목명   # 그 종목 매수·매도 이력 전부
    python C:/Users/frel/Desktop/new1/friend_brief.py sectors          # 섹터별 종목 수·금액·비중·손익
    python C:/Users/frel/Desktop/new1/friend_brief.py top [N]          # 많이 사고판 종목(앱 Top Traded)

숫자는 live_check.live_snapshot()(지수 확정 종가 + 오늘 실시간, 보유는 마지막 매매일지 반영분)을 그대로 쓴다.
"""
import sys
import warnings

warnings.filterwarnings("ignore")

import pandas as pd

import live_check
import portfolio_core as core
import tg_trades


def pct(v, d=2):
    return "-" if v is None else f"{v * 100:+.{d}f}%"


def head(s):
    print(f"[Orchestra] {s['asof']} {s['basis']} · 보유는 매매일지 {s['holdings_asof']} 반영분")


def c(v, unit="%", d=2, won=False):
    """부호 있는 숫자. v는 이미 % 또는 원 단위."""
    if v is None or pd.isna(v):
        return "-"
    return f"{v:+,.0f}원" if won else f"{v:+.{d}f}{unit}"


def brief():
    s = live_check.live_snapshot()
    h, px = s["holdings"], s["px"]
    qty = pd.to_numeric(h["수량"])
    cost = float((qty * pd.to_numeric(h["평단가"])).sum())
    val = float((qty * px).sum())
    anchor_kospi = float(s["idx"]["KOSPI"].iloc[0])
    orch = anchor_kospi * (1 + s["stock"][0]) if s["stock"][0] is not None else None
    tx = core.load_transactions()
    t = tx[tx["날짜"] == s["today"]]
    sells_all = pd.to_numeric(tx.loc[tx["구분"] == "매도", "실현손익"], errors="coerce")
    realized = pd.to_numeric(t.loc[t["구분"] == "매도", "실현손익"], errors="coerce").sum()
    day = "오늘" if not s["closed"] else s["today"][5:]

    print(f"Orchestra · {s['asof'][5:]} {s['basis']}")
    print(f"(보유는 매매일지 {s['holdings_asof']} 반영분)")
    print()
    print(f"보유종목 평가손익 {s['eval_pl']:+,.0f}원({s['eval_pl'] / cost * 100:+.2f}%)")
    print(f"누적 실현손익 {sells_all.sum():+,.0f}원")
    if orch:
        print(f"Orchestra 지수 {orch:,.0f} ({c(s['stock'][1] * 100)})")
    print()
    print(f"예수금  {s['cash']:,.0f}원 ({s['cash'] / s['total'] * 100:.1f}%)")
    print(f"총매입  {cost:,.0f}원")
    print(f"총평가  {val:,.0f}원")
    print(f"총자산  {s['total']:,.0f}원")
    print(f"원금 대비  {c(s['total'] - s['initial'], won=True)} ({c((s['total'] / s['initial'] - 1) * 100)})")
    print(f"{day} 실현손익  {c(realized, won=True) if realized else '0원'}")
    print(f"보유  {s['n']}종목")
    print()
    ex_day, my_day = s["ex"][1], s["stock"][1]
    print("Today's Take")
    print(f"어제 대비  {c(s['acct_won'], won=True)} ({c(s['acct'][1] * 100)})")
    print(f"혼합지수(삼성·하이닉스 제외)  {c(ex_day * 100)}")
    print(f"내 지수  {c(my_day * 100)}")
    print(f"혼합지수 대비  {c((my_day - ex_day) * 100, unit='%p')}")
    print()
    if s["holdings_asof"] < s["today"]:
        print(f"{day} 매수·매도는 매매일지가 아직 반영 전이야 (마지막 반영 {s['holdings_asof']})")
        return

    prior = tx[(tx["날짜"] < s["today"]) & tx["구분"].isin(["매수", "매도"])].copy()
    prior["수량"] = pd.to_numeric(prior["수량"], errors="coerce").fillna(0)
    net = prior["수량"].where(prior["구분"] == "매수", -prior["수량"]).groupby(prior["종목명"]).sum()
    held_before = set(net[net > 1e-6].index)
    ever = set(prior["종목명"])

    for kind in ("매수", "매도"):
        g = t[t["구분"] == kind].copy()
        g["q"] = g["수량"].astype(float)
        g["amt"] = g["q"] * g["단가"].astype(float)
        g["rp"] = pd.to_numeric(g["실현손익"], errors="coerce").fillna(0)
        by = (g.groupby("종목명", sort=False).agg(q=("q", "sum"), amt=("amt", "sum"), rp=("rp", "sum"))
              .sort_values("amt", ascending=False))
        line = f"{'' if day == '오늘' else day + ' '}{kind} {len(g)}회 · {g['amt'].sum():,.0f}원"
        if kind == "매도" and len(g):
            line += f" · 실현 {c(g['rp'].sum(), won=True)}"
        print(line)
        for name, r in by.iterrows():
            if kind == "매수":
                tag = "물" if name in held_before else ("재" if name in ever else "신")
                print(f"{name}({tag}) {r['q']:g}주 · {r['amt']:,.0f}원")
            else:
                print(f"{name} {r['q']:g}주 · {r['amt']:,.0f}원 · 실현 {c(r['rp'], won=True)}")
        print()


def period(n=5):
    s = live_check.live_snapshot()
    idx = s["idx"]
    if len(idx) <= n:
        print(f"데이터가 {len(idx) - 1}거래일치뿐이야")
        return
    d0 = str(idx["날짜"].iloc[-1 - n])
    ex = core.fixed_index_levels(idx, s["bigdf"], "ex")
    wk = s["wk"]

    def chg(df, col):
        return float(df[col].iloc[-1]) / float(df[col].iloc[-1 - n]) - 1

    k, q = chg(idx, "KOSPI"), chg(idx, "KOSDAQ")
    blend = wk * k + (1 - wk) * q
    blend_ex = wk * chg(ex, "KOSPI") + (1 - wk) * q
    me = s["me"]
    acct = stock = None
    if me is not None and not me.empty:
        base = me[me["날짜"].astype(str) <= d0]
        if not base.empty and s["acct"][0] is not None:
            acct = (1 + s["acct"][0]) / (1 + float(base["계좌수익"].iloc[-1])) - 1
            stock = (1 + s["stock"][0]) / (1 + float(base["주식수익"].iloc[-1])) - 1
    head(s)
    print(f"최근 {n}거래일 ({d0} 종가 → 지금)")
    print(f"코스피 {pct(k)} · 코스닥 {pct(q)}")
    print(f"혼합지수 {pct(blend)} · 혼합지수(삼성·하이닉스 제외) {pct(blend_ex)}")
    print(f"내 주식 {pct(stock)} · 내 계좌 {pct(acct)}")
    if stock is not None:
        print(f"혼합지수(삼성·하이닉스 제외) 대비 내 주식 {(stock - blend_ex) * 100:+.2f}%p")


def holding(name):
    h = core.load_holdings()
    r = h[h["종목명"] == name]
    if r.empty:
        print(f"{name}: 지금 보유 안 함 (매매일지 반영분 기준)")
        return
    r = r.iloc[0]
    code = str(r["종목코드"])
    q, _ = core.fetch_quotes([code])
    px = (q.get(code) or {}).get("price") or float(r["현재가"])
    chg = (q.get(code) or {}).get("change_pct")
    qty, avg = float(r["수량"]), float(r["평단가"])
    tx = core.load_transactions()
    pts = core.get_holding_trade_points(tx, name)
    buys = pts[pts["구분"] == "매수"]
    first = buys.iloc[0] if not buys.empty else None
    print(f"{name} ({r['섹터']})")
    print(f"{qty:g}주 · 평단 {avg:,.0f}원 · 지금 {px:,.0f}원 (오늘 {chg:+.2f}%)" if chg is not None
          else f"{qty:g}주 · 평단 {avg:,.0f}원 · 지금 {px:,.0f}원")
    print(f"평가손익 {(px - avg) * qty:+,.0f}원 ({(px / avg - 1) * 100:+.2f}%) · 평가금액 {px * qty:,.0f}원")
    if first is not None:
        print(f"이번에 처음 산 날 {first['날짜']} ({float(first['단가']):,.0f}원) · 매수 {len(buys)}회")


def _live_holdings():
    """보유종목 + 실시간가로 다시 계산한 표(종목·섹터·수량·평단·현재가·오늘등락·손익률·평가금액·비중)."""
    s = live_check.live_snapshot()
    h = s["holdings"].copy()
    h["현재가"] = s["px"].values
    h["오늘"] = s["chg"].values
    h["수량"] = pd.to_numeric(h["수량"])
    h["평단가"] = pd.to_numeric(h["평단가"])
    h["평가금액"] = h["수량"] * h["현재가"]
    h["손익"] = (h["현재가"] - h["평단가"]) * h["수량"]
    h["손익률"] = (h["현재가"] / h["평단가"] - 1) * 100
    h["비중"] = h["평가금액"] / s["total"] * 100
    return s, h


def _row(r):
    return (f"  {r['종목명']} 오늘 {r['오늘']:+.2f}% · 지금 {r['현재가']:,.0f}원 · "
            f"내 손익 {r['손익률']:+.1f}% ({r['손익']:+,.0f}원)")


def holdings(n=10):
    s, h = _live_holdings()
    head(s)
    up, dn = h.sort_values("오늘", ascending=False), h.sort_values("오늘")
    print(f"보유 {len(h)}종목 · 오늘 상승 {int((h['오늘'] > 0).sum())} · 하락 {int((h['오늘'] < 0).sum())} · 보합 {int((h['오늘'] == 0).sum())}")
    day = "오늘" if not s["closed"] else s["today"][5:]
    print(f"{day} 많이 오른 {min(n, len(h))}")
    for _, r in up.head(n).iterrows():
        print(_row(r))
    print(f"{day} 많이 빠진 {min(n, len(h))}")
    for _, r in dn.head(n).iterrows():
        print(_row(r))


def holdings_all():
    """전체 보유 목록 — 친구의 그때그때 질문(섹터별 종목, 제일 많이 든 종목 등)에 세션이 보고 답하는 원자료."""
    s, h = _live_holdings()
    head(s)
    print(f"예수금 {s['cash']:,.0f}원 ({s['cash'] / s['total'] * 100:.1f}%) · 총자산 {s['total']:,.0f}원")
    print("종목 | 섹터 | 수량 | 평단 | 현재가 | 오늘 | 손익률 | 평가금액 | 비중(총자산 대비)")
    for _, r in h.sort_values("평가금액", ascending=False).iterrows():
        print(f"{r['종목명']} | {r['섹터']} | {r['수량']:g} | {r['평단가']:,.0f} | {r['현재가']:,.0f} | "
              f"{r['오늘']:+.2f}% | {r['손익률']:+.1f}% | {r['평가금액']:,.0f} | {r['비중']:.1f}%")


def new_entries(day=None):
    """그날 새로 들어온 종목 — 전날 종가 기준 보유 0주였다가 그날 산 것(예전에 팔았다가 다시 산 것 포함). 앱 Holdings 초록 표시와 같은 정의."""
    tx = core.load_transactions()
    tx = tx[tx["구분"].isin(["매수", "매도"])].copy()
    trade_days = sorted(tx["날짜"].unique())
    day = day or trade_days[-1]
    tx["수량"] = pd.to_numeric(tx["수량"], errors="coerce").fillna(0)
    prior = tx[tx["날짜"] < day]
    net = prior["수량"].where(prior["구분"] == "매수", -prior["수량"]).groupby(prior["종목명"]).sum()
    held_before = set(net[net > 1e-6].index)
    buys = tx[(tx["날짜"] == day) & (tx["구분"] == "매수")]
    new = [n for n in dict.fromkeys(buys["종목명"]) if n not in held_before]
    print(f"[{day}] 새로 들어온 종목 {len(new)}개 (매매일지 반영분 기준, 마지막 반영일 {trade_days[-1]})")
    for n in new:
        g = buys[buys["종목명"] == n]
        q = float(g["수량"].sum())
        amt = float((g["수량"] * g["단가"].astype(float)).sum())
        again = " · 예전에 팔았다가 다시 산 것" if (prior["종목명"] == n).any() else " · 처음 산 것"
        print(f"  {n} {q:g}주 ({amt:,.0f}원){again}")


def month(ym=None):
    tx = core.load_transactions()
    sells = tx[tx["구분"] == "매도"].copy()
    ym = ym or core.today_kst_str()[:7]
    m = sells[sells["날짜"].astype(str).str.startswith(ym)]
    rp = pd.to_numeric(m["실현손익"], errors="coerce")
    print(f"{ym} 실현손익 {rp.sum():+,.0f}원 · 매도 {len(m)}건 (이익 {int((rp > 0).sum())} · 손실 {int((rp < 0).sum())})")
    allrp = pd.to_numeric(sells["실현손익"], errors="coerce").sum()
    print(f"누적 실현손익 {allrp:+,.0f}원 (원금 {core.load_state()['initial']:,.0f}원 대비 {allrp / core.load_state()['initial'] * 100:+.2f}%)")


def stats():
    """앱 거래 기록 탭 누적 요약과 같은 정의 + 사이클(한 종목 처음 산 날~전부 판 날) 수."""
    tx = core.load_transactions()
    buy, sell = tx[tx["구분"] == "매수"], tx[tx["구분"] == "매도"]
    days = tx["날짜"].nunique()
    amt = lambda g: float((pd.to_numeric(g["수량"]) * pd.to_numeric(g["단가"])).sum())
    rp = pd.to_numeric(sell["실현손익"], errors="coerce")
    init = core.load_state()["initial"]
    fa = core.compute_fa_win_rate(tx)
    print(f"첫 거래 {tx['날짜'].min()} · 거래한 날 {days}일")
    print(f"누적 매수 {len(buy)}건 (하루 평균 {int(len(buy) / days) if days else 0}건) · {amt(buy):,.0f}원")
    print(f"누적 매도 {len(sell)}건 (하루 평균 {int(len(sell) / days) if days else 0}건) · {amt(sell):,.0f}원")
    print(f"전체 거래 {len(buy) + len(sell)}건 중 매도 {len(sell)}건")
    print(f"매도 {len(sell)}건 중 이익 {int((rp > 0).sum())}건 · 손실 {int((rp < 0).sum())}건")
    print(f"누적 실현손익 {rp.sum():+,.0f}원 (원금 {init:,.0f}원 대비 {rp.sum() / init * 100:+.2f}%)")
    print(f"지금까지 거래한 종목 {tx.loc[tx['구분'].isin(['매수', '매도']), '종목명'].nunique()}개 · 지금 보유 {len(core.load_holdings())}개")
    print(f"사이클(처음 산 날~전부 판 날) {fa['total']}번 중 전부 팔고 끝난 것 {fa['n_out']}번 · 아직 들고 있는 것 {fa['total'] - fa['n_out']}번")


def history(name):
    tx = core.load_transactions()
    g = tx[(tx["종목명"] == name) & tx["구분"].isin(["매수", "매도"])].sort_values("날짜")
    if g.empty:
        print(f"{name}: 거래한 적 없음")
        return
    held = name in set(core.load_holdings()["종목명"])
    rp = pd.to_numeric(g["실현손익"], errors="coerce")
    print(f"{name} 거래 {len(g)}건 (매수 {int((g['구분'] == '매수').sum())} · 매도 {int((g['구분'] == '매도').sum())}) · "
          f"실현손익 합 {rp.sum():+,.0f}원 · {'지금 보유 중' if held else '지금 보유 안 함'}")
    for (_, r), v in zip(g.iterrows(), rp):
        q, p = float(r["수량"]), float(r["단가"])
        tail = f" · 실현 {v:+,.0f}원" if r["구분"] == "매도" and pd.notna(v) else ""
        print(f"{r['날짜']} {r['구분']} {q:g}주 @ {p:,.0f}원{tail}")


def sectors():
    s, h = _live_holdings()
    head(s)
    g = (h.groupby("섹터").agg(종목수=("종목명", "size"), 평가금액=("평가금액", "sum"), 손익=("손익", "sum"))
         .sort_values("평가금액", ascending=False))
    print(f"예수금 {s['cash']:,.0f}원 ({s['cash'] / s['total'] * 100:.1f}%)")
    for sec, r in g.iterrows():
        top = h[h["섹터"] == sec].sort_values("평가금액", ascending=False)["종목명"].iloc[0]
        print(f"{sec} {int(r['종목수'])}종목 · {r['평가금액']:,.0f}원 (총자산의 {r['평가금액'] / s['total'] * 100:.1f}%) · "
              f"손익 {r['손익']:+,.0f}원 · 제일 많은 건 {top}")


def top(n=10):
    """앱 Statistics 탭 Top Traded와 같은 함수 — 전부 판 사이클이 많은 종목 순."""
    t = core.top_traded_stocks(core.load_transactions())
    print(f"많이 사고판 종목 (전부 판 횟수 순, 상위 {min(n, len(t))})")
    for i, r in enumerate(t.head(n).itertuples(), 1):
        print(f"{i}. {r.종목명} {r.매도횟수}회 · 누적 실현 {r.누적실현손익:+,.0f}원 ({r.누적실현손익률:+.1f}%)")


def main():
    a = sys.argv[1:]
    if not a:
        brief()
    elif a[0] == "trades":
        tg_trades.print_trades(a[1] if len(a) > 1 else None)
    elif a[0] == "period":
        period(int(a[1]) if len(a) > 1 else 5)
    elif a[0] == "holding" and len(a) > 1:
        holding(" ".join(a[1:]))
    elif a[0] == "holdings":
        if len(a) > 1 and a[1] == "all":
            holdings_all()
        else:
            holdings(int(a[1]) if len(a) > 1 else 10)
    elif a[0] == "new":
        new_entries(a[1] if len(a) > 1 else None)
    elif a[0] == "stats":
        stats()
    elif a[0] == "history" and len(a) > 1:
        history(" ".join(a[1:]))
    elif a[0] == "sectors":
        sectors()
    elif a[0] == "top":
        top(int(a[1]) if len(a) > 1 else 10)
    elif a[0] == "month":
        month(a[1] if len(a) > 1 else None)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
