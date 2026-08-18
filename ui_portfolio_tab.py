"""포트폴리오 탭 (요약 카드, 섹터 비중, Up/Down, 종목별 보유현황)."""

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

from constants import UP_COLOR, DOWN_COLOR, CASH_LABEL, SECTOR_PALETTE, SECTOR_TARGETS
from portfolio_core import (
    group_sector, today_kst_str, now_kst_str,
    load_sector_history, get_current_prices_for_names, get_closed_out_last_sells,
    compute_sector_weights, load_watchlist, load_watchlist_prices, refresh_watchlist_prices,
    find_pattern_matches,
)


def render_portfolio_tab(holdings, state, tx, df, stock_valuation, total_assets, unrealized_loss, T):
    total_cost = df["매입금액"].sum()
    stock_profit = stock_valuation - total_cost
    stock_profit_pct = (stock_profit / total_cost * 100) if total_cost else 0

    capital_return = total_assets - state["initial"]
    capital_return_pct = (capital_return / state["initial"] * 100) if state["initial"] else 0

    today_str = today_kst_str()
    today_tx = tx[tx["날짜"].astype(str) == today_str]
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
        <div class="capital-line">최초 자본 10,000,000원 대비&nbsp;
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
    # 전일(=마지막으로 새로고침 누른 영업일) 대비 ±3% 이상 움직인 종목만 걸러서 보여준다.
    # 최초가는 이 종목이 처음 관측된 시점 가격으로 영구 보존(다시 안 바뀜), 기준가는 캘린더
    # 날짜가 바뀔 때마다 그 직전 최근가로 롤오버됨 — 자세한 건 refresh_watchlist_prices 참고.
    with st.expander("Fishing", expanded=False):
        watchlist = load_watchlist()
        if watchlist.empty:
            st.caption("관심종목이 없습니다. temporary/ 폴더에 리스트 CSV를 넣고 "
                       "import_watchlist.py로 반영해주세요.")
        else:
            if st.button("새로고침", key="fishing_refresh", use_container_width=True):
                with st.spinner("관심종목 시세 조회 중..."):
                    prices_df, quote_errors = refresh_watchlist_prices(watchlist)
                st.session_state["fishing_prices"] = prices_df
                for err in quote_errors:
                    st.warning(err)
                st.rerun()

            prices_df = st.session_state.get("fishing_prices")
            if prices_df is None:
                prices_df = load_watchlist_prices()

            if prices_df.empty:
                st.caption(f"총 {len(watchlist)}개 종목 등록됨. 새로고침을 누르면 추적을 시작합니다.")
            else:
                FISHING_THRESHOLD = 3.0
                flagged = []
                for _, r in prices_df.iterrows():
                    try:
                        origin, ref, last = float(r["최초가"]), float(r["기준가"]), float(r["최근가"])
                    except (TypeError, ValueError):
                        continue
                    if not ref:
                        continue
                    pct_ref = (last - ref) / ref * 100
                    if abs(pct_ref) < FISHING_THRESHOLD:
                        continue
                    pct_origin = (last - origin) / origin * 100 if origin else 0.0
                    flagged.append({"종목명": r["종목명"], "현재가": last, "pct_ref": pct_ref, "pct_origin": pct_origin})

                last_checked = prices_df["최근조회일시"].max() if "최근조회일시" in prices_df else ""
                if last_checked:
                    st.caption(f"마지막 조회: {last_checked} · 기준일 대비 ±{FISHING_THRESHOLD:.0f}% 이상만 표시")

                if not flagged:
                    st.caption("전일 대비 ±3% 이상 움직인 종목이 없습니다.")
                else:
                    flagged.sort(key=lambda x: abs(x["pct_ref"]), reverse=True)
                    rows_html = "".join(
                        f'<div class="updown-row"><span class="name">{f["종목명"]}</span>'
                        f'<span class="pct" style="color:{UP_COLOR if f["pct_origin"] >= 0 else DOWN_COLOR}">'
                        f'{"+" if f["pct_origin"] >= 0 else ""}{f["pct_origin"]:.1f}%</span>'
                        f'<span class="pct" style="color:{UP_COLOR if f["pct_ref"] >= 0 else DOWN_COLOR}">'
                        f'{"+" if f["pct_ref"] >= 0 else ""}{f["pct_ref"]:.1f}%</span></div>'
                        for f in flagged
                    )
                    st.markdown(rows_html, unsafe_allow_html=True)

            st.divider()

            # ---- 패턴 검색: "하락 후 횡보" 종목 찾기 (당일 스크리너와 별개, 가끔 탐색용) ----
            st.markdown("###### 패턴 검색")
            pc1, pc2 = st.columns(2)
            with pc1:
                decline_months = st.select_slider(
                    "하락 기간", options=[1, 2, 3, 6], value=2,
                    format_func=lambda m: f"{m}개월", key="pattern_decline_months")
                decline_pct = st.slider("하락폭 기준", 10, 40, 15, step=1,
                                         format="%d%%", key="pattern_decline_pct")
            with pc2:
                flat_weeks = st.select_slider(
                    "최근 횡보 기간", options=[1, 2, 3, 4], value=2,
                    format_func=lambda w: f"{w}주", key="pattern_flat_weeks")
                flat_pct = st.slider("횡보 범위 기준", 2, 15, 5, step=1,
                                      format="±%d%%", key="pattern_flat_pct")

            if st.button("검색", key="pattern_search", use_container_width=True):
                st.session_state["pattern_matches"] = find_pattern_matches(
                    decline_months, decline_pct, flat_weeks, flat_pct)

            matches = st.session_state.get("pattern_matches")
            if matches is None:
                st.caption("조건을 고르고 검색을 누르면 결과가 여기 표시됩니다 "
                           "(가격 히스토리가 며칠 이상 쌓여야 의미 있는 결과가 나옵니다).")
            elif not matches:
                st.caption("조건에 맞는 종목이 없습니다.")
            else:
                for m in matches:
                    series = m["series"]
                    lo, hi = min(series), max(series)
                    rng = hi - lo or 1
                    pts = " ".join(
                        f"{i / (len(series) - 1) * 100 if len(series) > 1 else 0:.1f},"
                        f"{30 - (v - lo) / rng * 28:.1f}"
                        for i, v in enumerate(series)
                    )
                    spark_color = DOWN_COLOR if series[-1] < series[0] else UP_COLOR
                    st.markdown(
                        f'<div class="updown-row" style="flex-direction:column;align-items:stretch;gap:2px;">'
                        f'<div style="display:flex;justify-content:space-between;">'
                        f'<span class="name">{m["종목명"]}</span>'
                        f'<span class="detail">고점대비 {m["drawdown_pct"]:.1f}% · '
                        f'최근{flat_weeks}주 ±{m["flat_range_pct"]:.1f}%</span></div>'
                        f'<svg viewBox="0 0 100 30" preserveAspectRatio="none" '
                        f'style="width:100%;height:32px;display:block;">'
                        f'<polyline points="{pts}" fill="none" stroke="{spark_color}" '
                        f'stroke-width="2" vector-effect="non-scaling-stroke" /></svg></div>',
                        unsafe_allow_html=True,
                    )

    # ---- 종목별 보유현황 ----
    SORT_OPTIONS = {"비중": "weight", "섹터": "sector", "현재가": "price",
                     "평가금액": "valuation", "손익": "profit"}
    if "sort_mode" not in st.session_state:
        st.session_state.sort_mode = "weight"

    last_updated = ""
    updated_vals = [v for v in df["업데이트시각"].tolist() if v]
    if updated_vals:
        last_updated = max(updated_vals)

    col_title2, col_updated = st.columns([2, 1.3])
    with col_title2:
        st.markdown("##### 종목별 보유현황")
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
    if mode == "sector":
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

    rows = df_sorted.to_dict("records")

    if not rows:
        st.info("보유 종목이 없습니다. '거래 기록' 탭에서 매수를 기록해보세요.")
    else:
        card_parts = []
        for r in rows:
            pc = UP_COLOR if r["손익"] >= 0 else DOWN_COLOR
            psign = "+" if r["손익"] >= 0 else ""
            cc = UP_COLOR if r["등락률"] >= 0 else DOWN_COLOR
            csign = "+" if r["등락률"] >= 0 else ""
            sc = sector_tag_color(r["섹터"])

            card_parts.append(f"""
            <div class="stock-card">
                <div class="stock-top">
                    <span><span class="stock-name">{r['종목명']}</span>
                        <span class="stock-weight-inline">비중 {r['비중']:.1f}%</span></span>
                    <span class="sector-tag" style="background:{sc}22;color:{sc}">{r['섹터']}</span>
                </div>
                <div class="stock-grid">
                    <div class="cell"><div class="top">{r['수량']:.0f}주</div></div>
                    <div class="cell"><div class="top">{r['현재가']:,.0f}</div><div class="bottom">{r['평단가']:,.0f}</div></div>
                    <div class="cell"><div class="top">{r['평가금액']:,.0f}</div><div class="bottom">{r['매입금액']:,.0f}</div></div>
                    <div class="cell">
                        <div class="top" style="color:{pc}">{psign}{r['손익']:,.0f}</div>
                        <div class="bottom"><span style="color:{pc}">{psign}{r['손익률']:.1f}%</span> <span style="color:{cc}">{csign}{r['등락률']:.1f}%</span></div>
                    </div>
                </div>
            </div>
            """)
        st.markdown("".join(card_parts), unsafe_allow_html=True)
