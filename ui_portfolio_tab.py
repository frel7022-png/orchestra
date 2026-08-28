"""포트폴리오 탭 (요약 카드, 섹터 비중, Up/Down, 종목별 보유현황)."""

import matplotlib.pyplot as plt
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from constants import UP_COLOR, DOWN_COLOR, NEW_COLOR, CASH_LABEL, SECTOR_PALETTE, SECTOR_TARGETS
from portfolio_core import (
    group_sector, today_kst_str, now_kst_str,
    load_sector_history, get_current_prices_for_names, get_closed_out_last_sells,
    compute_sector_weights, load_watchlist, refresh_watchlist_prices,
    get_holding_trade_summary, get_holding_trade_summary_all_time,
    get_holding_trade_points, get_holding_avg_price_path,
    load_investor_flow_db, load_market_flow_db, load_watchlist_history_db,
    compute_volume_flags, compute_foreign_flags, compute_market_flow_baseline,
)


def _render_holding_detail(r: dict, tx: pd.DataFrame, T: dict):
    """보유종목 카드를 눌렀을 때 펼쳐지는 상세 — 매수/매도 요약 + "물타기 적정성" 그래프.
    그래프에 연속된 일별 시세선은 없음(보유종목엔 그런 히스토리가 없음 — Fishing 관심종목만
    Supabase에 매일 쌓이는 중, 2026-08-21 기준 나흘치뿐이라 아직 못 씀. 장기적으로 여기도
    DB 시세로 연결할 수 있음). 대신 최초매입일→오늘 두 점을 직선으로 잇고, 그 위에 실제
    매수/매도 시점을 점으로 찍어서 "내가 얼마나 현재가를 따라 물을 탔는지"를 보여준다."""
    name = r["종목명"]
    trades = get_holding_trade_points(tx, name)
    buys = trades[trades["구분"] == "매수"]
    if buys.empty:
        st.caption("매수 기록을 찾을 수 없습니다.")
        return

    all_time = get_holding_trade_summary_all_time(tx, name)
    all_time_color = UP_COLOR if all_time["realized_pnl"] >= 0 else DOWN_COLOR
    summary = get_holding_trade_summary(tx, name)
    realized_color = UP_COLOR if summary["realized_pnl"] >= 0 else DOWN_COLOR
    st.markdown(f"""
    <div class="trade-summary">
        <span class="trade-summary-label">누적</span>
        <span>매수 <b>{all_time['buy_count']}건</b> · {all_time['buy_amount']:,.0f}원</span>
        <span>매도 <b>{all_time['sell_count']}건</b> · {all_time['sell_amount']:,.0f}원
            (실현손익 <span style="color:{all_time_color}">{all_time['realized_pnl']:,.0f}원</span>)</span>
    </div>
    <div class="trade-summary">
        <span class="trade-summary-label">이번 사이클</span>
        <span>매수 <b>{summary['buy_count']}건</b> · {summary['buy_amount']:,.0f}원</span>
        <span>매도 <b>{summary['sell_count']}건</b> · {summary['sell_amount']:,.0f}원
            (실현손익 <span style="color:{realized_color}">{summary['realized_pnl']:,.0f}원</span>)</span>
    </div>
    """, unsafe_allow_html=True)

    entry_date = buys.iloc[0]["날짜"]
    entry_price = float(buys.iloc[0]["단가"])
    current_price = float(r["현재가"])
    avg_price = float(r["평단가"])
    today = today_kst_str()
    sells = trades[trades["구분"] == "매도"]

    avg_path = get_holding_avg_price_path(tx, name)
    avg_x = list(avg_path["날짜"]) + [today]
    avg_y = list(avg_path["평단가"]) + [avg_price]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=[entry_date, today], y=[entry_price, current_price], mode="lines+markers",
        name="현재가", line=dict(color=T["muted"], width=2, dash="dot"),
        marker=dict(size=6, color=T["muted"]),
        hovertemplate="%{x}<br>%{y:,.0f}원<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=avg_x, y=avg_y, mode="lines", name="평단가",
        line=dict(color=DOWN_COLOR, width=2, shape="hv"),
        hovertemplate="%{x}<br>평단가 %{y:,.0f}원<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=buys["날짜"], y=buys["단가"], mode="markers", name="매수",
        marker=dict(size=11, color=DOWN_COLOR, symbol="triangle-up"),
        customdata=buys["수량"],
        hovertemplate="%{x}<br>매수 %{y:,.0f}원 · %{customdata:.0f}주<extra></extra>",
    ))
    if not sells.empty:
        fig.add_trace(go.Scatter(
            x=sells["날짜"], y=sells["단가"], mode="markers", name="매도",
            marker=dict(size=11, color=UP_COLOR, symbol="triangle-down"),
            customdata=sells["수량"],
            hovertemplate="%{x}<br>매도 %{y:,.0f}원 · %{customdata:.0f}주<extra></extra>",
        ))
    fig.add_hline(y=entry_price, line_dash="dash", line_color=T["muted2"], line_width=1,
                  annotation_text="최초진입가", annotation_font_size=10,
                  annotation_font_color=T["muted2"])
    fig.update_layout(
        height=260,
        margin=dict(l=10, r=10, t=20, b=30),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=T["text"], size=11),
        showlegend=True,
        legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="center", x=0.5,
                    bgcolor="rgba(0,0,0,0)"),
        xaxis=dict(showgrid=False, tickfont=dict(size=9, color=T["muted"]), fixedrange=True),
        yaxis=dict(showgrid=True, gridcolor=T["border"], tickfont=dict(size=9, color=T["muted"]),
                   tickformat=",.0f", fixedrange=True),
        hovermode="closest",
        dragmode=False,
    )
    st.plotly_chart(fig, width="stretch", config={
        "displayModeBar": False, "scrollZoom": False, "doubleClick": False,
    }, key=f"holding_chart_{r['종목코드']}")

    pct_current = (current_price - entry_price) / entry_price * 100 if entry_price else 0.0
    pct_avg = (avg_price - entry_price) / entry_price * 100 if entry_price else 0.0
    cur_c = UP_COLOR if pct_current >= 0 else DOWN_COLOR
    avg_c = UP_COLOR if pct_avg >= 0 else DOWN_COLOR
    st.markdown(
        f"<div style='font-size:12px;color:{T['muted']};display:flex;justify-content:space-between;"
        f"margin-bottom:12px;'>"
        f"<span>현재가는 최초진입가 대비 <span style='color:{cur_c}'>{pct_current:+.1f}%</span></span>"
        f"<span>내 평단가는 최초진입가 대비 <span style='color:{avg_c}'>{pct_avg:+.1f}%</span></span>"
        f"</div>", unsafe_allow_html=True)


def render_portfolio_tab(holdings, state, tx, df, stock_valuation, total_assets, unrealized_loss, T):
    total_cost = df["매입금액"].sum()
    stock_profit = stock_valuation - total_cost
    stock_profit_pct = (stock_profit / total_cost * 100) if total_cost else 0

    capital_return = total_assets - state["initial"]
    capital_return_pct = (capital_return / state["initial"] * 100) if state["initial"] else 0

    today_str = today_kst_str()
    today_tx = tx[tx["날짜"].astype(str) == today_str]

    # 오늘 신규 진입한 종목("어제 종가 기준 보유수량이 0이었던 종목") — 카드 정렬 최상단 +
    # 초록색 강조에 씀. 처음 사보는 건지(ESR켄달스퀘어리츠/앱클론) 예전에 샀다가 전량매도한
    # 뒤 오늘 다시 산 건지(삼성전자/NAVER/두산에너빌리티 같은 단타 종목)는 구분하지 않는다 —
    # 관건은 "어제는 안 갖고 있었는데 오늘 갖게 됐냐"뿐(2026-08-27 사용자가 명확히 함).
    # 어제까지의 전체 거래(날짜 < 오늘)를 합산해서 순보유수량을 구하고, 그게 0 이하인
    # 종목만 "어제 미보유"로 판정 — 매수/매도 순서는 최종 순수량엔 영향 없으므로 재생 없이
    # 합산만으로 충분하다. 오늘 팔지 않고 남아있으면, 내일은 "어제(=오늘) 보유"로 잡혀
    # 자동으로 일반 종목과 동일해짐(별도 상태 저장 없음).
    prior_tx = tx[tx["날짜"].astype(str) < today_str].copy()
    prior_tx["수량"] = pd.to_numeric(prior_tx["수량"], errors="coerce").fillna(0)
    signed_qty = prior_tx["수량"].where(prior_tx["구분"] == "매수", -prior_tx["수량"])
    net_qty_yesterday = signed_qty.groupby(prior_tx["종목명"]).sum()
    held_yesterday = set(net_qty_yesterday[net_qty_yesterday > 1e-6].index)
    new_today_names = set(df["종목명"]) - held_yesterday
    daily_pnl = pd.to_numeric(
        today_tx.loc[today_tx["구분"] == "매도", "실현손익"], errors="coerce"
    ).sum()

    color = UP_COLOR if stock_profit >= 0 else DOWN_COLOR
    sign = "+" if stock_profit >= 0 else ""
    cap_color = UP_COLOR if capital_return >= 0 else DOWN_COLOR
    cap_sign = "+" if capital_return >= 0 else ""
    daily_color = UP_COLOR if daily_pnl > 0 else (DOWN_COLOR if daily_pnl < 0 else T["muted"])
    daily_sign = "+" if daily_pnl > 0 else ""

    # ---- 오늘의 거래 요약 (매수/매도 총금액) ----
    buy_tx = today_tx[today_tx["구분"] == "매수"].copy()
    sell_tx = today_tx[today_tx["구분"] == "매도"].copy()
    buy_total_amt = (pd.to_numeric(buy_tx["수량"], errors="coerce") * pd.to_numeric(buy_tx["단가"], errors="coerce")).sum()
    sell_total_amt = (pd.to_numeric(sell_tx["수량"], errors="coerce") * pd.to_numeric(sell_tx["단가"], errors="coerce")).sum()
    total_trade_count = len(today_tx)

    daily_trade_html = f"""
    <div class="daily-trade-box">
        <div class="daily-trade-count">일일거래 총 {total_trade_count}회
            <span>(매수 {len(buy_tx)}건 · 매도 {len(sell_tx)}건)</span>
        </div>
        <div class="daily-trade-row"><span class="tag-label" style="color:{UP_COLOR}">매수</span>
            <span class="trade-chip"><b>{buy_total_amt:,.0f}원</b></span></div>
        <div class="daily-trade-row"><span class="tag-label" style="color:{DOWN_COLOR}">매도</span>
            <span class="trade-chip"><b>{sell_total_amt:,.0f}원</b></span></div>
    </div>
    """

    st.markdown(f"""
    <div class="summary-box">
        <div class="summary-label">보유종목 평가손익</div>
        <span class="summary-main" style="color:{color}">{sign}{stock_profit:,.0f}원</span>
        <span class="summary-sub" style="color:{color}">{sign}{stock_profit_pct:.2f}%</span>
        <div class="summary-grid">
            <div>예수금<b>{state['cash']:,.0f}원</b></div>
            <div>총 매입<b>{total_cost:,.0f}원</b></div>
            <div>총 평가<b>{stock_valuation:,.0f}원</b></div>
            <div>총자산<b>{total_assets:,.0f}원</b></div>
            <div>일일손익<b style="color:{daily_color}">{daily_sign}{daily_pnl:,.0f}원</b></div>
            <div>보유종목<b>{len(df)}개</b></div>
        </div>
        <div class="capital-line">최초 자본 {state['initial']:,.0f}원 대비&nbsp;
            <b style="color:{cap_color}">{cap_sign}{capital_return:,.0f}원 ({cap_sign}{capital_return_pct:.2f}%)</b>
        </div>
        {daily_trade_html}
    </div>
    """, unsafe_allow_html=True)

    # ---- 섹터별 색상: 파이차트/막대/종목카드 태그가 전부 같은 배정을 쓰도록 여기서 한 번만 계산 ----
    # (주식 총자산 대비 비중 기준 순위로 고정 배정 — 예전에는 이 매핑이 두 벌 따로 있어서
    #  종목카드 섹터 태그 색이 파이차트/막대와 다르게 나오는 경우가 있었음)
    stock_weights = compute_sector_weights(df)  # {섹터그룹: 주식 총자산 대비 %}
    stock_weight_rank = sorted(stock_weights.items(), key=lambda x: x[1], reverse=True)
    color_map = {name: SECTOR_PALETTE[i % len(SECTOR_PALETTE)] for i, (name, _) in enumerate(stock_weight_rank)}

    # ---- 섹터 비중 도넛 + 목표 비중 관리 ----
    with st.expander("섹터 비중 보기", expanded=False):
        include_cash = st.toggle("예수금 포함", value=st.session_state.get("include_cash", True), key="cash_toggle")
        st.session_state["include_cash"] = include_cash

        df_grp = df.copy()
        df_grp["섹터그룹"] = df_grp["섹터"].apply(group_sector)
        sector_val = df_grp.groupby("섹터그룹")["평가금액"].sum().to_dict()
        if include_cash and state["cash"] > 0:
            sector_val[CASH_LABEL] = state["cash"]
        sector_items = sorted(sector_val.items(), key=lambda x: x[1], reverse=True)
        denom = sum(v for _, v in sector_items)

        if denom > 0 and sector_items:
            labels = [s for s, _ in sector_items]
            values = [v for _, v in sector_items]
            colors = [color_map.get(lbl, T["cash_dot"] if lbl == CASH_LABEL else T["muted2"]) for lbl in labels]

            fig, ax = plt.subplots(figsize=(4.6, 4.6))
            fig.patch.set_alpha(0)
            ax.pie(values, colors=colors, startangle=90, counterclock=False,
                   wedgeprops=dict(width=0.38, edgecolor=T["card"], linewidth=1.2))
            ax.set(aspect="equal")
            st.pyplot(fig, use_container_width=True)
            plt.close(fig)

            legend_html = '<div class="legend-wrap">'
            for lbl, val, c in zip(labels, values, colors):
                pct = val / denom * 100
                legend_html += (f'<div class="legend-item"><span class="legend-dot" '
                                 f'style="background:{c}"></span>{lbl} '
                                 f'<span class="legend-pct">{pct:.1f}%</span></div>')
            legend_html += "</div>"
            st.markdown(legend_html, unsafe_allow_html=True)
        else:
            st.info("종목/예수금 데이터가 있으면 섹터 비중이 표시됩니다.")

        # ---- 섹터별 현재 비중 막대 (주식 총자산 대비, 예수금 제외) + 목표 비중 ----
        if stock_weight_rank:
            sec_stocks = df_grp.groupby("섹터그룹")["종목명"].apply(lambda s: ", ".join(s)).to_dict()
            sec_hist = load_sector_history()
            prev_weights = {}
            if not sec_hist.empty:
                today_str_ = today_kst_str()
                past_dates = sorted(d for d in sec_hist["날짜"].unique() if d < today_str_)
                if past_dates:
                    prev_date = past_dates[-1]
                    prev_weights = sec_hist[sec_hist["날짜"] == prev_date].set_index("섹터그룹")["비중"].to_dict()

            if st.session_state.get("sector_trend_pick") not in stock_weights:
                st.session_state.sector_trend_pick = None

            SCALE_MAX = 40.0  # 종목 특성상 한 섹터가 40%를 넘지 않는다는 전제의 고정 스케일(배터리 게이지 방식)

            for name, pct in stock_weight_rank:
                bar_color = color_map.get(name, "#888")
                width_pct = max(min(pct / SCALE_MAX * 100, 100), 0)
                target = SECTOR_TARGETS.get(name)
                target_marker = ""
                target_sublabel = ""
                if target is not None:
                    target_pos = max(min(target / SCALE_MAX * 100, 100), 0)
                    target_marker = f'<div class="sector-target-marker" style="left:{target_pos}%"></div>'
                    target_sublabel = f'<div class="sector-target-label" style="left:{target_pos}%">{target:.0f}%</div>'
                delta_html = ""
                if name in prev_weights:
                    delta = pct - prev_weights[name]
                    if abs(delta) >= 0.05:
                        dcolor = UP_COLOR if delta > 0 else DOWN_COLOR
                        dsign = "+" if delta > 0 else ""
                        delta_html = f'<span class="delta" style="color:{dcolor}">{dsign}{delta:.1f}%p</span>'

                is_open = st.session_state.sector_trend_pick == name
                c1, c2, c3 = st.columns([1.05, 3.1, 1.4])
                with c1:
                    label = f"▾ {name}" if is_open else name
                    if st.button(label, key=f"sector_pick_{name}", use_container_width=True):
                        st.session_state.sector_trend_pick = None if is_open else name
                        st.rerun()
                with c2:
                    st.markdown(
                        f'<div class="sector-bar-track">'
                        f'<div class="sector-bar-fill" style="background:{bar_color}; width:{width_pct}%"></div>'
                        f'{target_marker}{target_sublabel}</div>',
                        unsafe_allow_html=True,
                    )
                with c3:
                    st.markdown(
                        f'<div class="sector-bar-pct"><span class="cur">{pct:.1f}%</span>{delta_html}</div>',
                        unsafe_allow_html=True,
                    )

                if is_open:
                    st.markdown(
                        f'<div class="sector-stock-names">{sec_stocks.get(name, "")}</div>',
                        unsafe_allow_html=True,
                    )
                    if not sec_hist.empty and name in sec_hist["섹터그룹"].unique():
                        series = sec_hist[sec_hist["섹터그룹"] == name].sort_values("날짜")
                        dates = series["날짜"].tolist()
                        vals = series["비중"].tolist()

                        svg_w, svg_h = 600, 150
                        pad_l, pad_r, pad_t, pad_b = 12, 12, 22, 22
                        plot_w = svg_w - pad_l - pad_r
                        plot_h = svg_h - pad_t - pad_b
                        n = len(vals)
                        xs = [pad_l if n <= 1 else pad_l + plot_w * i / (n - 1) for i in range(n)]
                        ys = [pad_t + plot_h * (1 - min(v, 40) / 40) for v in vals]

                        poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
                        parts = [f'<svg viewBox="0 0 {svg_w} {svg_h}" style="width:100%;height:auto;display:block;">']
                        if target is not None:
                            ty = pad_t + plot_h * (1 - min(target, 40) / 40)
                            parts.append(f'<line x1="{pad_l}" y1="{ty:.1f}" x2="{svg_w - pad_r}" y2="{ty:.1f}" stroke="{T["muted2"]}" stroke-width="1" stroke-dasharray="4,3" />')
                        parts.append(f'<polyline points="{poly}" fill="none" stroke="{bar_color}" stroke-width="2.5" />')
                        for x, y, v, d in zip(xs, ys, vals, dates):
                            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{bar_color}" />')
                            parts.append(f'<text x="{x:.1f}" y="{y - 9:.1f}" font-size="10" fill="{T["text"]}" text-anchor="middle">{v:.1f}%</text>')
                            parts.append(f'<text x="{x:.1f}" y="{svg_h - 6}" font-size="9" fill="{T["muted"]}" text-anchor="middle">{d[5:]}</text>')
                        parts.append('</svg>')
                        st.markdown("".join(parts), unsafe_allow_html=True)
                    else:
                        st.info("시세 새로고침 또는 거래 기록을 하면 그날의 섹터 비중이 저장되어 추이가 쌓입니다.")

    # ---- Up/Down: 청산 종목 추적 ----
    with st.expander("Up/Down", expanded=False):
        updown_mode = st.radio("모드", ["DOWN", "UP"], horizontal=True,
                                label_visibility="collapsed", key="updown_mode")

        if st.button("새로고침", key="updown_refresh", use_container_width=True):
            closed = get_closed_out_last_sells(holdings, tx)
            results = []
            if not closed.empty:
                with st.spinner("청산 종목 현재가 조회 중..."):
                    prices = get_current_prices_for_names(closed["종목명"].tolist())
                for _, row in closed.iterrows():
                    cp = prices.get(row["종목명"])
                    if cp is None:
                        continue
                    pct = (cp - row["매도가"]) / row["매도가"] * 100
                    results.append({
                        "종목명": row["종목명"], "매도일": row["매도일"],
                        "매도가": row["매도가"], "현재가": cp, "pct": pct,
                    })
            st.session_state["updown_results"] = results
            st.session_state["updown_checked_at"] = now_kst_str()
            st.rerun()

        updown_results = st.session_state.get("updown_results")
        updown_checked_at = st.session_state.get("updown_checked_at")

        if updown_results is None:
            st.caption("새로고침을 누르면 청산(완전 매도)된 종목의 현재가를 마지막 매도가와 비교합니다.")
        else:
            if updown_checked_at:
                st.caption(f"마지막 조회: {updown_checked_at}")
            threshold = 3.0
            if updown_mode == "DOWN":
                filtered = sorted([r for r in updown_results if r["pct"] <= -threshold], key=lambda r: r["pct"])
                updown_color = DOWN_COLOR
            else:
                filtered = sorted([r for r in updown_results if r["pct"] >= threshold], key=lambda r: -r["pct"])
                updown_color = UP_COLOR

            if not filtered:
                st.caption("조건에 해당하는 종목이 없습니다.")
            else:
                rows_html = "".join(
                    f'<div class="updown-row"><span class="name">{r["종목명"]}</span>'
                    f'<span class="pct" style="color:{updown_color}">{"+" if r["pct"] >= 0 else ""}{r["pct"]:.1f}%</span>'
                    f'<span class="detail">{r["매도가"]:,.0f} → {r["현재가"]:,.0f}</span></div>'
                    for r in filtered
                )
                st.markdown(rows_html, unsafe_allow_html=True)

    # ---- Fishing: 관심종목 리스트 (보유/거래와 무관, 순수 관찰용) ----
    # 최초가(처음 관측된 시점의 전일 종가, 영구 보존)/전일대비(네이버가 주는 정식 전일 종가
    # 대비 등락률)를 기준으로 ±3% 이상 움직인 종목만 걸러서 보여준다 — 자세한 건
    # refresh_watchlist_prices 참고.
    with st.expander("Fishing", expanded=False):
        sb_secrets = st.secrets.get("supabase", {})
        sb_url, sb_key = sb_secrets.get("url", ""), sb_secrets.get("anon_key", "")

        watchlist = load_watchlist()
        if watchlist.empty:
            st.caption("관심종목이 없습니다. temporary/ 폴더에 리스트 CSV를 넣고 "
                       "import_watchlist.py로 반영해주세요.")
        else:
            if st.button("새로고침", key="fishing_refresh", use_container_width=True):
                with st.spinner("관심종목 시세 조회 중..."):
                    prices_df, quote_errors = refresh_watchlist_prices(watchlist, sb_url, sb_key)
                st.session_state["fishing_prices"] = prices_df
                for err in quote_errors:
                    st.warning(err)
                st.rerun()

            prices_df = st.session_state.get("fishing_prices", pd.DataFrame())

            if prices_df.empty:
                st.caption(f"총 {len(watchlist)}개 종목 등록됨. 새로고침을 누르면 추적을 시작합니다.")
            else:
                all_rows = []
                for _, r in prices_df.iterrows():
                    try:
                        origin, last, pct_ref = float(r["최초가"]), float(r["최근가"]), float(r["전일대비"])
                    except (TypeError, ValueError):
                        continue
                    pct_origin = (last - origin) / origin * 100 if origin else 0.0
                    all_rows.append({"종목명": r["종목명"], "현재가": last, "pct_ref": pct_ref, "pct_origin": pct_origin})

                last_checked = prices_df["최근조회일시"].max() if "최근조회일시" in prices_df else ""
                if last_checked:
                    st.caption(f"마지막 조회: {last_checked}")

                # "누적" %가 어느 날짜를 기준으로 계산됐는지 표시 — 지금은 전 종목이 DB
                # 추적 시작일(2026-08-19)로 동일하지만, 나중에 새 종목이 추가되면 그 종목만
                # 기준일이 달라질 수 있어 그 경우도 대비함(2026-08-28 사용자 요청).
                if "기준일" in prices_df.columns:
                    origin_dates = sorted(set(d for d in prices_df["기준일"].dropna().tolist() if d))
                    if len(origin_dates) == 1:
                        st.caption(f"누적 기준일: {origin_dates[0]}")
                    elif len(origin_dates) > 1:
                        st.caption(f"누적 기준일: {origin_dates[0]} ~ {origin_dates[-1]} (종목별로 다름)")

                fc1, fc2 = st.columns(2)
                with fc1:
                    fishing_basis = st.radio("기준", ["누적", "전일"], horizontal=True,
                                              label_visibility="collapsed", key="fishing_basis")
                with fc2:
                    fishing_dir = st.radio("방향", ["DOWN", "UP"], horizontal=True,
                                            label_visibility="collapsed", key="fishing_dir")

                FISHING_THRESHOLD = 3.0
                basis_key = "pct_origin" if fishing_basis == "누적" else "pct_ref"
                if fishing_dir == "DOWN":
                    flagged = [f for f in all_rows if f[basis_key] <= -FISHING_THRESHOLD]
                else:
                    flagged = [f for f in all_rows if f[basis_key] >= FISHING_THRESHOLD]
                flagged.sort(key=lambda x: abs(x[basis_key]), reverse=True)

                if not flagged:
                    st.caption(f"{fishing_basis} 기준 {fishing_dir} ±{FISHING_THRESHOLD:.0f}% 이상 종목이 없습니다.")
                else:
                    rows_html = "".join(
                        f'<div class="updown-row"><span class="name">{f["종목명"]}</span>'
                        f'<span class="pct" style="color:{UP_COLOR if f["pct_origin"] >= 0 else DOWN_COLOR}">'
                        f'{"+" if f["pct_origin"] >= 0 else ""}{f["pct_origin"]:.1f}%</span>'
                        f'<span class="pct" style="color:{UP_COLOR if f["pct_ref"] >= 0 else DOWN_COLOR}">'
                        f'{"+" if f["pct_ref"] >= 0 else ""}{f["pct_ref"]:.1f}%</span></div>'
                        for f in flagged
                    )
                    st.markdown(rows_html, unsafe_allow_html=True)

    # ---- Volume / Foreigner: 거래량·외국인 수급이 평소보다 튀는 종목 (2026-08-24 신설) ----
    # investor_flow(종목별)/market_flow(시장 전체) 테이블을 조회해서 compute_volume_flags/
    # compute_foreign_flags로 "오늘 vs 그동안 쌓인 평균" 차이가 큰 순으로 보여준다.
    # §6-12 참고 — 필터 빌더처럼 DB엔 원시값만 쌓고 등락폭은 화면에서 계산.
    flow_hist = st.session_state.get("flow_hist")
    market_hist = st.session_state.get("market_hist")
    price_hist_flow = st.session_state.get("price_hist_flow")

    def _refresh_flow_data():
        st.session_state["flow_hist"] = load_investor_flow_db(sb_url, sb_key)
        st.session_state["market_hist"] = load_market_flow_db(sb_url, sb_key)
        st.session_state["price_hist_flow"] = load_watchlist_history_db(sb_url, sb_key)

    with st.expander("Volume", expanded=False):
        if st.button("새로고침", key="volume_refresh", use_container_width=True):
            with st.spinner("거래량 데이터 조회 중..."):
                _refresh_flow_data()
            st.rerun()

        if flow_hist is None:
            st.caption("새로고침을 누르면 153개 종목의 거래량이 평소 대비 얼마나 튀었는지 보여줍니다.")
        else:
            baseline = compute_market_flow_baseline(market_hist)
            if baseline:
                bits = [f"{m} 거래량 평균대비 {'+' if b['거래량vs평균pct'] >= 0 else ''}{b['거래량vs평균pct']:.0f}%"
                        for m, b in baseline.items()]
                st.caption(" · ".join(bits))

            vol_flags = compute_volume_flags(flow_hist, price_hist_flow)
            if not vol_flags:
                st.caption("비교할 데이터가 아직 부족합니다(최소 이틀치 필요).")
            else:
                rows_html = "".join(
                    f'<div class="updown-row flow-row"><span class="name">{r["종목명"]}</span>'
                    f'<span class="pct" style="color:{UP_COLOR if r["vs평균pct"] >= 0 else DOWN_COLOR}">'
                    f'{"+" if r["vs평균pct"] >= 0 else ""}{r["vs평균pct"]:.0f}%</span>'
                    f'<span class="detail">전일 '
                    f'{"+" if (r["vs어제pct"] or 0) >= 0 else ""}{r["vs어제pct"]:.0f}% 주가 '
                    + (f'{"+" if r["오늘등락률"] >= 0 else ""}{r["오늘등락률"]:.1f}%'
                       if r["오늘등락률"] is not None else "-")
                    + '</span></div>'
                    for r in vol_flags[:15]
                )
                st.markdown(rows_html, unsafe_allow_html=True)
                st.caption(f"평균 대비 변화가 큰 순 · 총 {len(vol_flags)}종목 중 상위 15개")

    with st.expander("Foreigner", expanded=False):
        if st.button("새로고침", key="foreigner_refresh", use_container_width=True):
            with st.spinner("외국인 수급 데이터 조회 중..."):
                _refresh_flow_data()
            st.rerun()

        if flow_hist is None:
            st.caption("새로고침을 누르면 153개 종목의 외국인 보유율이 평소 대비 얼마나 "
                       "움직였는지 보여줍니다.")
        else:
            baseline = compute_market_flow_baseline(market_hist)
            if baseline:
                bits = [f"{m} 외국인 순매수 {b['오늘외국인순매수']:,}억원(평균 {b['평균외국인순매수']:,.0f}억원)"
                        for m, b in baseline.items() if b["오늘외국인순매수"] is not None]
                if bits:
                    st.caption(" · ".join(bits))

            fx_flags = compute_foreign_flags(flow_hist, price_hist_flow)
            if not fx_flags:
                st.caption("비교할 데이터가 아직 부족합니다(최소 이틀치 필요).")
            else:
                rows_html = "".join(
                    f'<div class="updown-row flow-row"><span class="name">{r["종목명"]}</span>'
                    f'<span class="pct" style="color:{UP_COLOR if r["vs평균pp"] >= 0 else DOWN_COLOR}">'
                    f'{"+" if r["vs평균pp"] >= 0 else ""}{r["vs평균pp"]:.2f}%p</span>'
                    f'<span class="detail">전일 '
                    f'{"+" if r["vs어제pp"] >= 0 else ""}{r["vs어제pp"]:.2f}%p 주가 '
                    + (f'{"+" if r["오늘등락률"] >= 0 else ""}{r["오늘등락률"]:.1f}%'
                       if r["오늘등락률"] is not None else "-")
                    + '</span></div>'
                    for r in fx_flags[:15]
                )
                st.markdown(rows_html, unsafe_allow_html=True)
                st.caption(f"평균 대비 보유율 변화(%p)가 큰 순 · 총 {len(fx_flags)}종목 중 상위 15개")

    # ---- 종목별 보유현황 ----
    # "등락률"을 맨 앞에 둠 — 다른 정렬 기준(비중/섹터/현재가/평가금액/손익)보다 오늘 하루
    # 등락이 더 중요하다는 사용자 판단(2026-08-28)에 따라 우선순위가 가장 높은 자리에 배치.
    SORT_OPTIONS = {"등락률": "change", "비중": "weight", "섹터": "sector", "현재가": "price",
                     "평가금액": "valuation", "손익": "profit"}
    if "sort_mode" not in st.session_state:
        st.session_state.sort_mode = "weight"

    last_updated = ""
    updated_vals = [v for v in df["업데이트시각"].tolist() if v]
    if updated_vals:
        last_updated = max(updated_vals)

    col_title2, col_change_toggle, col_updated = st.columns([1.7, 1.0, 1.1])
    with col_title2:
        st.markdown("##### 종목별 보유현황")
    with col_change_toggle:
        # 매일 가장 먼저 확인하는 기준이라 "정렬 기준" 라디오까지 안 내려가도 타이틀 옆에서
        # 바로 토글할 수 있게 함(2026-08-28 사용자 요청) — 라디오와 같은 session_state를
        # 공유하므로, 여기서 누르면 아래 라디오도 "등락률"로 같이 선택된 채 보임.
        is_change_sort = st.session_state.sort_mode == "change"
        if st.button("등락률순", key="change_sort_toggle",
                     type="primary" if is_change_sort else "secondary",
                     use_container_width=True):
            st.session_state["sort_radio"] = "등락률"
            st.session_state.sort_mode = "change"
            st.rerun()
    with col_updated:
        st.markdown(
            f"<div style='text-align:right;font-size:11px;color:{T['muted2']};padding-top:10px;'>{last_updated}</div>",
            unsafe_allow_html=True,
        )

    # ---- 코스피 / 코스닥 지수 (상단 새로고침에 같이 갱신됨) ----
    idx = st.session_state.get("index_quotes") or {}
    if idx:
        idx_col1, idx_col2 = st.columns(2)
        for idx_col, (code, label) in zip((idx_col1, idx_col2), (("KOSPI", "코스피"), ("KOSDAQ", "코스닥"))):
            d = idx.get(code)
            if not d:
                continue
            ic = UP_COLOR if d["change"] >= 0 else DOWN_COLOR
            isign = "+" if d["change"] >= 0 else ""
            with idx_col:
                st.markdown(f"""
                <div style="background:{T['card']}; border:1px solid {T['border']}; border-radius:8px;
                            padding:5px 10px; margin-bottom:8px; display:flex; align-items:center;
                            justify-content:space-between; gap:6px;">
                    <span style="font-size:11px; color:{T['muted']}; flex-shrink:0;">{label}</span>
                    <span style="font-size:13px; font-weight:700; color:{T['text']};">{d['price']:,.2f}</span>
                    <span style="font-size:10px; color:{ic}; line-height:1.25; text-align:right; flex-shrink:0;">
                        {isign}{d['change']:,.1f}<br>{isign}{d['change_pct']:.2f}%
                    </span>
                </div>
                """, unsafe_allow_html=True)

    labels = list(SORT_OPTIONS.keys())
    cur_label = next(k for k, v in SORT_OPTIONS.items() if v == st.session_state.sort_mode)
    chosen = st.radio("정렬 기준", labels, index=labels.index(cur_label),
                       horizontal=True, label_visibility="collapsed", key="sort_radio")
    st.session_state.sort_mode = SORT_OPTIONS[chosen]

    # 종목카드 섹터 태그도 위에서 만든 color_map을 그대로 씀(그룹 기준) — 파이차트/막대와 색이 일치함
    def sector_tag_color(raw_sector: str) -> str:
        return color_map.get(group_sector(raw_sector), "#6b7280")

    mode = st.session_state.sort_mode
    if mode == "change":
        df_sorted = df.sort_values("등락률", ascending=False)
    elif mode == "sector":
        sector_totals = df.groupby("섹터")["평가금액"].sum().sort_values(ascending=False)
        sector_order = {s: i for i, s in enumerate(sector_totals.index)}
        df_sorted = df.copy()
        df_sorted["_rank"] = df_sorted["섹터"].map(sector_order)
        df_sorted = df_sorted.sort_values(["_rank", "평가금액"], ascending=[True, False])
    elif mode == "price":
        df_sorted = df.sort_values("현재가", ascending=False)
    elif mode == "valuation":
        df_sorted = df.sort_values("평가금액", ascending=False)
    elif mode == "profit":
        df_sorted = df.sort_values("손익", ascending=False)
    else:
        df_sorted = df.sort_values("비중", ascending=False)

    # 오늘 신규 진입 종목을 맨 위로 (그룹 내부 정렬 순서는 유지 — 안정적인 그룹 분리)
    if new_today_names:
        is_new = df_sorted["종목명"].isin(new_today_names)
        df_sorted = pd.concat([df_sorted[is_new], df_sorted[~is_new]])

    rows = df_sorted.to_dict("records")

    if not rows:
        st.info("보유 종목이 없습니다. '거래 기록' 탭에서 매수를 기록해보세요.")
    else:
        if "holding_detail_open" not in st.session_state:
            st.session_state.holding_detail_open = None

        for r in rows:
            pc = UP_COLOR if r["손익"] >= 0 else DOWN_COLOR
            psign = "+" if r["손익"] >= 0 else ""
            cc = UP_COLOR if r["등락률"] >= 0 else DOWN_COLOR
            csign = "+" if r["등락률"] >= 0 else ""
            sc = sector_tag_color(r["섹터"])
            name_style = f"color:{NEW_COLOR}" if r["종목명"] in new_today_names else ""

            code = r["종목코드"]
            is_open = st.session_state.holding_detail_open == code

            with st.container(key=f"holding_wrap_{code}"):
                st.markdown(f"""
                <div class="stock-card">
                    <div class="stock-top">
                        <span class="stock-title-group"><span class="stock-name" style="{name_style}">{r['종목명']}</span></span>
                        <span class="sector-tag" style="background:{sc}22;color:{sc}">{r['섹터']}</span>
                    </div>
                    <div class="stock-grid">
                        <div class="cell"><div class="top">{r['수량']:.0f}주</div><div class="bottom">{r['비중']:.1f}%</div></div>
                        <div class="cell"><div class="top">{r['현재가']:,.0f}</div><div class="bottom">{r['평단가']:,.0f}</div></div>
                        <div class="cell"><div class="top">{r['평가금액']:,.0f}</div><div class="bottom">{r['매입금액']:,.0f}</div></div>
                        <div class="cell">
                            <div class="top" style="color:{pc}">{psign}{r['손익']:,.0f}</div>
                            <div class="bottom"><span style="color:{pc}">{psign}{r['손익률']:.1f}%</span> <span style="color:{cc}">{csign}{r['등락률']:.1f}%</span></div>
                        </div>
                    </div>
                </div>
                """, unsafe_allow_html=True)

                if st.button("WATERING", key=f"watering_{code}",
                             type="primary" if is_open else "secondary"):
                    st.session_state.holding_detail_open = None if is_open else code
                    st.rerun()
            if is_open:
                _render_holding_detail(r, tx, T)
