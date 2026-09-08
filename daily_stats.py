"""Claude's Read (§6-22) — 그날 일일 평가를 세션이 쓰기 위한 수치 덤프.

매매일지 반영 + sync_both_accounts.py 뒤에 실행:
    python daily_stats.py [YYYY-MM-DD]
그러면 세션이 이 출력만 보고 claude_daily_notes.csv에 (날짜, 별점, 코멘트) 한 줄을 append,
git commit/push. (LLM 호출은 앱이 아니라 세션이 함 — 하루 1회.)
"""

import sys

import pandas as pd

import portfolio_core as core


def _pct(v):
    return "—" if v is None or (isinstance(v, float) and pd.isna(v)) else f"{v * 100:+.2f}%"


def main() -> None:
    d = sys.argv[1] if len(sys.argv) > 1 else core.resolve_trading_date()
    tx = core.load_transactions()
    st = core.load_state()
    holdings = core.load_holdings()
    df, stock_val, total_assets, unreal = core.compute_metrics(holdings, st["cash"])

    # 혼합지수 가중치
    mc = core.load_market_cache()
    hv = holdings.copy()
    hv["_v"] = (pd.to_numeric(hv["수량"], errors="coerce").fillna(0)
                * pd.to_numeric(hv["현재가"], errors="coerce").fillna(0))
    hv["_m"] = hv["종목명"].map(mc)
    ksv = float(hv.loc[hv["_m"] == "KOSPI", "_v"].sum())
    kqv = float(hv.loc[hv["_m"] == "KOSDAQ", "_v"].sum())
    wk = ksv / (ksv + kqv) if (ksv + kqv) > 0 else None

    idx = core.load_index_history()
    ah = core.load_history()
    fr = st.get("fee_rate", 0.0)
    m = core.compute_index_vs_account(tx, ah, idx, st["initial"], fr, kospi_weight=wk)
    bg = core.load_bigcap_history()
    s = (core.compute_index_vs_account(tx, ah, core.synthetic_kospi_ex_bigcap(idx, bg),
                                       st["initial"], fr, kospi_weight=wk)
         if not bg.empty else None)

    def _cap_today(iva):
        sm = (iva or {}).get("cap", {}).get("stock", {})
        return sm.get("today_bucket"), sm.get("today")

    print(f"=== Claude's Read 입력 · {d} ===")
    lat = m.get("latest", {})
    print(f"[내 주식 어제대비]  {_pct(lat.get('주식', (None, None))[1])}")
    print(f"[내 계좌 어제대비]  {_pct(lat.get('계좌', (None, None))[1])}")
    print(f"[코스피 당일]  {_pct(lat.get('코스피', (None, None))[1])}   [코스닥 당일]  {_pct(lat.get('코스닥', (None, None))[1])}")
    print(f"[혼합지수 당일]  {_pct(lat.get('벤치', (None, None))[1])}")
    if s:
        print(f"[W/O SH 혼합지수 당일]  {_pct((s.get('latest', {}).get('벤치') or (None, None))[1])}")
    b, v = _cap_today(m)
    print(f"[DC/UC 오늘 · 기본]  바구니 {b or '—'}  값 {('%.2f' % v) if v is not None else '—'}")
    if s:
        bs, vs = _cap_today(s)
        print(f"[DC/UC 오늘 · W/O SH]  바구니 {bs or '—'}  값 {('%.2f' % vs) if vs is not None else '—'}")
    n = m.get("n", {})
    print(f"[누적 바구니]  하락 {n.get('down')} · 상승 {n.get('up')} · even {n.get('even')}")
    ca = m.get("cap", {}).get("stock", {})
    print(f"[누적 DC/UC (내 주식)]  DC {ca.get('dc')}  ERA {ca.get('era')}  UC {ca.get('uc')}  PCT {ca.get('pct')}")

    tday = tx[tx["날짜"].astype(str) == d]
    sell = tday[tday["구분"] == "매도"].copy()
    buy = tday[tday["구분"] == "매수"].copy()
    for _c in ("수량", "단가", "실현손익"):
        sell[_c] = pd.to_numeric(sell[_c], errors="coerce")
        if _c in buy:
            buy[_c] = pd.to_numeric(buy[_c], errors="coerce")
    print(f"\n[오늘 매수]  {len(buy)}건 · {(buy['수량'] * buy['단가']).sum():,.0f}원")
    for _, r in buy.iterrows():
        print(f"   + {r['종목명']}  {r['수량']:.0f}주 @ {r['단가']:,.0f}  = {r['수량'] * r['단가']:,.0f}원")
    print(f"[오늘 매도]  {len(sell)}건 · {(sell['수량'] * sell['단가']).sum():,.0f}원 · 실현손익 합 {sell['실현손익'].sum():,.0f}원")
    for _, r in sell.sort_values("실현손익", ascending=False).iterrows():
        amt = r["수량"] * r["단가"]
        pct = (r["실현손익"] / amt * 100) if amt else 0
        print(f"   − {r['종목명']}  {r['수량']:.0f}주 @ {r['단가']:,.0f}  실현 {r['실현손익']:,.0f}원 ({pct:+.1f}%)")

    print(f"\n[예수금]  {st['cash']:,.0f}원  (총자산 대비 {st['cash'] / total_assets * 100:.1f}%)")
    print(f"[총자산]  {total_assets:,.0f}원   [미실현손실]  -{unreal:,.0f}원")

    pa = core.compute_pnl_actions(tx, holdings)
    if pa.get("baskets"):
        bk, wd = pa["baskets"], pa["watering"]
        print(f"[P&L Actions]  FA {bk['FA']['realized']:,.0f}({bk['FA']['pct']:.0f}%) · "
              f"MO {bk['MO']['realized']:,.0f}({bk['MO']['pct']:.0f}%) · MA {bk['MA']['realized']:,.0f}({bk['MA']['pct']:.0f}%)")
        print(f"[Watering]  {wd['n_stock']}종목 {wd['n_extra_buys']}회 · 손익 {_pct((wd['pl_avg_pct'] or 0) / 100)} · "
              f"흡수 {wd['absorbed_pp']:+.2f}%p · 시드 ×{wd['seed_mult']:.2f}" if wd.get("seed_mult") else "[Watering] —")

    prior = tx[tx["날짜"].astype(str) < d].copy()
    prior["수량"] = pd.to_numeric(prior["수량"], errors="coerce").fillna(0)
    sq = prior["수량"].where(prior["구분"] == "매수", -prior["수량"])
    held_y = set(sq.groupby(prior["종목명"]).sum().pipe(lambda x: x[x > 1e-6]).index)
    new_today = sorted(set(df["종목명"]) - held_y)
    print(f"[오늘 신규 진입]  {', '.join(new_today) if new_today else '없음'}")


if __name__ == "__main__":
    main()
