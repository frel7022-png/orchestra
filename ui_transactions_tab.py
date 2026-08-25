"""거래 기록 탭 (실현손익 그래프, 거래 캘린더)."""

import calendar

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from constants import UP_COLOR, DOWN_COLOR
from portfolio_core import now_kst, today_kst_str, load_history


def render_transactions_tab(state, tx, total_assets, unrealized_loss, T):
    cap_return = total_assets - state["initial"]
    cap_return_pct = (cap_return / state["initial"] * 100) if state["initial"] else 0
    c3 = UP_COLOR if cap_return >= 0 else DOWN_COLOR
    s3 = "+" if cap_return >= 0 else ""

    total_realized = pd.to_numeric(tx.loc[tx["구분"] == "매도", "실현손익"], errors="coerce").sum()
    rc = UP_COLOR if total_realized >= 0 else DOWN_COLOR
    rs = "+" if total_realized >= 0 else ""

    st.markdown(f"""
    <div class="summary-box">
        <div class="summary-label">최초 자본 10,000,000원 대비</div>
        <span class="summary-main" style="color:{c3}">{s3}{cap_return:,.0f}원</span>
        <span class="summary-sub" style="color:{c3}">{s3}{cap_return_pct:.2f}%</span>
        <div class="summary-grid">
            <div>현재 총자산<b>{total_assets:,.0f}원</b></div>
            <div>실현손익 누적<b style="color:{rc}">{rs}{total_realized:,.0f}원</b></div>
            <div>미실현 손실<b style="color:{DOWN_COLOR}">-{unrealized_loss:,.0f}원</b></div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ---- 실현손익 그래프: 누적 실현손익(호버 시 그날 실현손익도 표시) vs 미실현손실 ----
    st.markdown("##### 실현손익 그래프")

    tx_realized = tx[tx["구분"] == "매도"].copy()
    tx_realized["실현손익"] = pd.to_numeric(tx_realized["실현손익"], errors="coerce").fillna(0)
    hist = load_history()

    if tx_realized.empty and hist.empty:
        st.info("거래 기록이 쌓이거나 시세를 새로고침하면 그래프가 그려집니다.")
    else:
        start_candidates = []
        if not tx_realized.empty:
            start_candidates.append(tx_realized["날짜"].min())
        if not hist.empty:
            start_candidates.append(hist["날짜"].min())
        all_dates = pd.date_range(min(start_candidates), today_kst_str()).strftime("%Y-%m-%d").tolist()

        daily_realized = tx_realized.groupby("날짜")["실현손익"].sum()
        daily_values = [float(daily_realized.get(d, 0.0)) for d in all_dates]
        cum_values = list(pd.Series(daily_values).cumsum())

        hist_sorted = hist.sort_values("날짜")
        unreal_dates = hist_sorted["날짜"].tolist()
        unreal_series = (hist_sorted["조정자산"] - hist_sorted["총자산"]).tolist()

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=all_dates, y=cum_values, mode="lines+markers", name="실현손익(누적)",
            line=dict(color=UP_COLOR, width=2.5), marker=dict(size=5),
            customdata=daily_values,
            hovertemplate="%{x}<br>누적 실현손익 %{y:,.0f}원<br>이날 실현손익 %{customdata:,.0f}원<extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            x=unreal_dates, y=unreal_series, mode="lines+markers", name="미실현손실",
            line=dict(color=DOWN_COLOR, width=2.5), marker=dict(size=5),
            hovertemplate="%{x}<br>미실현손실 %{y:,.0f}원<extra></extra>",
        ))
        fig.add_hline(y=0, line_dash="dash", line_color=T["muted2"], line_width=1)
        fig.update_layout(
            height=280,
            margin=dict(l=10, r=10, t=10, b=45),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color=T["text"], size=11),
            legend=dict(orientation="h", yanchor="top", y=-0.2, xanchor="center", x=0.5,
                        bgcolor="rgba(0,0,0,0)"),
            xaxis=dict(showgrid=False, tickfont=dict(size=9, color=T["muted"]), fixedrange=True),
            yaxis=dict(showgrid=True, gridcolor=T["border"], zeroline=False,
                       tickfont=dict(size=9, color=T["muted"]), tickformat=",.0f", fixedrange=True),
            hovermode="x unified",
            dragmode=False,
        )
        st.plotly_chart(fig, width="stretch", config={
            "displayModeBar": False,
            "scrollZoom": False,
            "doubleClick": False,
        })

    st.divider()

    # ---- 거래 내역 (캘린더) ----
    # 캘린더 위에 누적 매수/매도(건수+금액) + 누적 실현손익(금액+매수 대비 %) 요약
    # (2026-08-25, 사용자 요청 — 한 줄엔 안 들어가서 두 줄로: 매수/매도 줄, 그 아래 실현손익 줄.
    # 글자 크기는 실현손익 그래프 범례랑 맞춤). 실현손익은 함수 맨 위에서 이미 계산해둔
    # total_realized 재사용(요약카드와 같은 숫자).
    buy_tx = tx[tx["구분"] == "매수"]
    sell_tx = tx[tx["구분"] == "매도"]
    buy_total = (pd.to_numeric(buy_tx["수량"], errors="coerce")
                 * pd.to_numeric(buy_tx["단가"], errors="coerce")).sum()
    sell_total = (pd.to_numeric(sell_tx["수량"], errors="coerce")
                  * pd.to_numeric(sell_tx["단가"], errors="coerce")).sum()
    realized_pct = (total_realized / buy_total * 100) if buy_total else 0.0
    st.markdown(f"""
    <div class="tx-cum-summary">
        <span>누적 매수 <b>{len(buy_tx)}건</b> · {buy_total:,.0f}원</span>
        <span>누적 매도 <b>{len(sell_tx)}건</b> · {sell_total:,.0f}원</span>
    </div>
    <div class="tx-cum-summary">
        <span>누적 실현손익 <b style="color:{rc}">{rs}{total_realized:,.0f}원 ({rs}{realized_pct:.2f}%)</b></span>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("##### 거래 내역")

    if "cal_year" not in st.session_state:
        st.session_state.cal_year = now_kst().year
        st.session_state.cal_month = now_kst().month
    if "selected_tx_date" not in st.session_state:
        st.session_state.selected_tx_date = today_kst_str()

    tx_dates = set(tx["날짜"].astype(str))
    year, month = st.session_state.cal_year, st.session_state.cal_month

    nav1, nav2, nav3 = st.columns([1, 3, 1])
    with nav1:
        if st.button("◀", key="cal_prev", use_container_width=True):
            m, y = month - 1, year
            if m < 1:
                m, y = 12, y - 1
            st.session_state.cal_month, st.session_state.cal_year = m, y
            st.rerun()
    with nav2:
        st.markdown(
            f"<div style='text-align:center;font-weight:700;padding-top:6px;color:{T['text']}'>"
            f"{year}년 {month}월</div>",
            unsafe_allow_html=True,
        )
    with nav3:
        if st.button("▶", key="cal_next", use_container_width=True):
            m, y = month + 1, year
            if m > 12:
                m, y = 1, y + 1
            st.session_state.cal_month, st.session_state.cal_year = m, y
            st.rerun()

    last_day = calendar.monthrange(year, month)[1]

    if st.session_state.selected_tx_date.startswith(f"{year:04d}-{month:02d}"):
        cur_day = int(st.session_state.selected_tx_date.split("-")[2])
    else:
        cur_day = min(now_kst().day, last_day) if (year, month) == (now_kst().year, now_kst().month) else 1

    st.markdown('<div class="cal-grid">', unsafe_allow_html=True)
    wd_cols = st.columns(7)
    for i, wd in enumerate(["일", "월", "화", "수", "목", "금", "토"]):
        wd_cols[i].markdown(
            f"<div style='text-align:center;font-size:10.5px;color:{T['muted2']}'>{wd}</div>",
            unsafe_allow_html=True,
        )

    cal_obj = calendar.Calendar(firstweekday=6)
    weeks = cal_obj.monthdayscalendar(year, month)
    for week in weeks:
        cols = st.columns(7)
        for i, day in enumerate(week):
            with cols[i]:
                if day == 0:
                    st.write("")
                    continue
                d_str = f"{year:04d}-{month:02d}-{day:02d}"
                has_tx = d_str in tx_dates
                is_sel = day == cur_day
                label = f"{day}●" if has_tx else f"{day}"
                if st.button(label, key=f"day_{d_str}", use_container_width=True,
                             type="primary" if is_sel else "secondary"):
                    st.session_state.selected_tx_date = d_str
                    st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

    st.divider()

    sel = st.session_state.selected_tx_date
    day_tx = tx[tx["날짜"].astype(str) == sel]
    day_realized = pd.to_numeric(day_tx.loc[day_tx["구분"] == "매도", "실현손익"], errors="coerce").sum()

    head_html = f"<b style='color:{T['text']}'>{sel}</b>"
    if day_realized:
        drc = UP_COLOR if day_realized >= 0 else DOWN_COLOR
        drs = "+" if day_realized >= 0 else ""
        head_html += f" <span style='color:{drc};font-size:13px'>({drs}{day_realized:,.0f}원)</span>"
    st.markdown(head_html, unsafe_allow_html=True)

    if day_tx.empty:
        st.info("이 날짜엔 기록된 거래가 없습니다.")
    else:
        card_parts = []
        for _, r in day_tx.iterrows():
            realized = r["실현손익"]
            right_html = ""
            if r["구분"] == "매도" and str(realized) not in ("", "nan"):
                rv = float(realized)
                trc = UP_COLOR if rv >= 0 else DOWN_COLOR
                trs = "+" if rv >= 0 else ""
                right_html = f'<span style="color:{trc}">{trs}{rv:,.0f}원</span>'
            memo_html = f' · {r["메모"]}' if str(r["메모"]) not in ("", "nan") else ""
            card_parts.append(f"""
            <div class="tx-card">
                <div class="tx-left">
                    <span class="name">{r['종목명']}</span>
                    <span class="meta">{r['구분']} {float(r['수량']):.0f}주 @ {float(r['단가']):,.0f}원{memo_html}</span>
                </div>
                <div class="tx-right">{right_html}</div>
            </div>
            """)
        st.markdown("".join(card_parts), unsafe_allow_html=True)
