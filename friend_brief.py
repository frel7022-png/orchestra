"""친구 창구 브리프 — 텔레그램 허브(Desktop/new1_friend)용, 읽기 전용 (2026-10-15 신설, 대표 프레임).

앱 첫 화면에서 보이는 "사실"만 평문으로 낸다. 분석·추천(Fishing/Up-Down/Basecamp/Foreigner/Link/CFG,
Watering Detect, Alarm, Pit Stop, Claude's Read, 아침 리포트)은 다루지 않는다 — 그건 대표 창구 몫.
다른 계좌 데이터는 읽지도 출력하지도 않는다. 파일에 아무것도 쓰지 않는다.

    python C:/Users/frel/Desktop/new1/friend_brief.py                 # 브리프 (평가손익 블록 + Today's Take + 오늘 매수·매도)
    python C:/Users/frel/Desktop/new1/friend_brief.py trades [날짜]    # 그날 매수·매도 (기본: 가장 최근 매매일)
    python C:/Users/frel/Desktop/new1/friend_brief.py period [N]       # 최근 N거래일(기본 5) 지수 vs 내 계좌·내 주식
    python C:/Users/frel/Desktop/new1/friend_brief.py holding 종목명   # 그 종목 보유 현황
    python C:/Users/frel/Desktop/new1/friend_brief.py month [YYYY-MM]  # 그달 실현손익

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
    print(f"[Orchestra] {s['asof']} 기준 · 보유는 매매일지 {s['holdings_asof']} 반영분")


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
    realized = pd.to_numeric(t.loc[t["구분"] == "매도", "실현손익"], errors="coerce").sum()

    head(s)
    print()
    print(f"보유종목 평가손익 {s['eval_pl']:+,.0f}원 ({s['eval_pl'] / cost * 100:+.2f}%)"
          + (f" · Orchestra 지수 {orch:,.0f} ({pct(s['stock'][1])})" if orch else ""))
    print(f"예수금 {s['cash']:,.0f}원 · 총매입 {cost:,.0f}원 · 총평가 {val:,.0f}원 · 총자산 {s['total']:,.0f}원")
    print(f"오늘 실현손익 {realized:+,.0f}원 · 보유 {s['n']}종목 · 오늘 계좌 변동 {s['acct_won']:+,.0f}원")
    print()
    ex_day, my_day = s["ex"][1], s["stock"][1]
    print("Today's Take")
    print(f"혼합지수(삼성·하이닉스 제외) {pct(ex_day)} · 내 지수 {pct(my_day)} · "
          f"혼합지수 대비 {(my_day - ex_day) * 100:+.2f}%p")
    print()
    if s["holdings_asof"] < s["today"]:
        print(f"오늘 매수·매도: 오늘 매매일지가 아직 반영 전이야 (마지막 반영 {s['holdings_asof']})")
        return
    for kind in ("매수", "매도"):
        g = t[t["구분"] == kind]
        print(f"오늘 {kind} {len(g)}회")
        for _, r in g.iterrows():
            q, p = float(r["수량"]), float(r["단가"])
            print(f"  {r['종목명']} {q:g}주 ({q * p:,.0f}원)")


def period(n=5):
    s = live_check.live_snapshot()
    idx = s["idx"]
    if len(idx) <= n:
        print(f"데이터가 {len(idx) - 1}거래일치뿐이야")
        return
    d0 = str(idx["날짜"].iloc[-1 - n])
    ex = core.synthetic_kospi_ex_bigcap(idx, s["bigdf"])
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


def month(ym=None):
    tx = core.load_transactions()
    sells = tx[tx["구분"] == "매도"].copy()
    ym = ym or core.today_kst_str()[:7]
    m = sells[sells["날짜"].astype(str).str.startswith(ym)]
    rp = pd.to_numeric(m["실현손익"], errors="coerce")
    print(f"{ym} 실현손익 {rp.sum():+,.0f}원 · 매도 {len(m)}건 (이익 {int((rp > 0).sum())} · 손실 {int((rp < 0).sum())})")
    allrp = pd.to_numeric(sells["실현손익"], errors="coerce").sum()
    print(f"누적 실현손익 {allrp:+,.0f}원 (원금 {core.load_state()['initial']:,.0f}원 대비 {allrp / core.load_state()['initial'] * 100:+.2f}%)")


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
    elif a[0] == "month":
        month(a[1] if len(a) > 1 else None)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
