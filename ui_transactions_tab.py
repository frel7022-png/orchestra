"""거래 기록 탭 (실현손익 그래프, 거래 캘린더)."""

import calendar

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from constants import UP_COLOR, DOWN_COLOR
from portfolio_core import (
    now_kst, today_kst_str, load_history, load_index_history, load_market_cache,
    compute_index_vs_account, _index_day_moves,
)

KOSPI_COLOR = "#f59e0b"   # 지수 참조선(코스피) — 앰버
KOSDAQ_COLOR = "#14b8a6"  # 지수 참조선(코스닥) — 틸


def render_transactions_tab(state, tx, holdings, total_assets, unrealized_loss, T):
    cap_return = total_assets - state["initial"]
    cap_return_pct = (cap_return / state["initial"] * 100) if state["initial"] else 0
    c3 = UP_COLOR if cap_return >= 0 else DOWN_COLOR
    s3 = "+" if cap_return >= 0 else ""

    total_realized = pd.to_numeric(tx.loc[tx["구분"] == "매도", "실현손익"], errors="coerce").sum()
    rc = UP_COLOR if total_realized >= 0 else DOWN_COLOR
    rs = "+" if total_realized >= 0 else ""

    st.markdown(f"""
    <div class="summary-box">
        <div class="summary-label">최초 자본 {state['initial']:,.0f}원 대비</div>
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

    # ---- 지수 대비 계좌 (§6-17): 코스피/코스닥 vs 내 계좌·주식 수익 (누적 + 당일) ----
    #  · 코스피(노랑)/코스닥(초록) = anchor일 종가 대비 누적등락(0 중심)
    #  · 내 주식(검정)  = 보유주식 100% 투자로 환산한 누적수익 Rs — 지수와 1:1 비교 가능
    #  · 내 계좌(점선)  = 총자산/최초자본 - 1 (요약카드 값, 예수금이 눌러주는 완충선)
    #  위에 4줄 표로 각 선의 "누적 / 당일"을 같이 보여주고, 내 주식·내 계좌 값은 보유비중을
    #  반영한 혼합 지수(코스피·코스닥 가중평균)보다 높으면 빨강 / 낮으면 파랑으로 칠한다.
    idx_hist = load_index_history()

    # 내 보유주식의 코스피/코스닥 평가금액 비중 → 혼합 지수 가중치
    mc = load_market_cache()
    hv = holdings.copy()
    hv["_v"] = (pd.to_numeric(hv["수량"], errors="coerce").fillna(0)
                * pd.to_numeric(hv["현재가"], errors="coerce").fillna(0))
    hv["_m"] = hv["종목명"].map(mc)
    ks_val = float(hv.loc[hv["_m"] == "KOSPI", "_v"].sum())
    kq_val = float(hv.loc[hv["_m"] == "KOSDAQ", "_v"].sum())
    wk = ks_val / (ks_val + kq_val) if (ks_val + kq_val) > 0 else None

    _wtag = "" if wk is None else (
        f" <span style='font-size:11px;font-weight:400;color:{T['muted']}'>"
        f"보유비중 코스피 {wk * 100:.0f}% · 코스닥 {(1 - wk) * 100:.0f}%</span>"
    )
    st.markdown(f"##### 지수 대비 계좌{_wtag}", unsafe_allow_html=True)

    iva = compute_index_vs_account(tx, hist, idx_hist, state["initial"],
                                    state.get("fee_rate", 0.0), kospi_weight=wk)
    me, idxc, latest = iva["me"], iva["index"], iva["latest"]

    if me.empty or idxc.empty:
        st.info("시세를 새로고침하면 지수·자산 스냅샷이 쌓여서 그래프가 그려집니다.")
    else:
        def _pct(v):
            return "—" if v is None else f"{v * 100:+.2f}%"

        bench = latest.get("벤치") or (None, None)

        def _color_vs_bench(v, ref):
            if v is None or ref is None:
                return T["text"]
            return UP_COLOR if v >= ref else DOWN_COLOR  # 벤치 이겼으면 빨강, 졌으면 파랑

        def _row(label, dot_color, dashed, key, color_by_bench):
            cum, day = latest.get(key, (None, None))
            if color_by_bench:
                cc, dc = _color_vs_bench(cum, bench[0]), _color_vs_bench(day, bench[1])
            else:
                cc = dc = T["muted"]
            mark = "┈" if dashed else "●"
            return (
                f"<tr><td style='color:{dot_color}'>{mark}&nbsp;{label}</td>"
                f"<td style='text-align:right;color:{cc}'>{_pct(cum)}</td>"
                f"<td style='text-align:right;color:{dc}'>{_pct(day)}</td></tr>"
            )

        st.markdown(
            "<table style='width:100%;font-size:12px;border-collapse:collapse;margin:-2px 0 4px'>"
            f"<tr style='color:{T['muted2']};font-size:10px'>"
            "<th style='text-align:left'>&nbsp;</th><th style='text-align:right'>누적</th>"
            "<th style='text-align:right'>당일</th></tr>"
            + _row("코스피", KOSPI_COLOR, False, "코스피", False)
            + _row("코스닥", KOSDAQ_COLOR, False, "코스닥", False)
            + _row("내 주식", T["text"], False, "주식", True)
            + _row("내 계좌", T["muted2"], True, "계좌", True)
            + "</table>",
            unsafe_allow_html=True,
        )

        # 혼합지수(코스피·코스닥 합친) vs 내 주식 — 당일 한 줄
        b_cum, b_day = latest.get("벤치", (None, None))
        my_cum, my_day = latest.get("주식", (None, None))

        def _p(v):
            return "—" if v is None or pd.isna(v) else f"{v * 100:+.2f}%"

        st.markdown(
            f"<div style='font-size:11px;color:{T['muted']};margin:0 0 2px'>"
            f"당일  혼합지수 <b>{_p(b_day)}</b>  /  내 주식 <b>{_p(my_day)}</b>"
            f"<span style='color:{T['muted2']}'> · 누적 {_p(b_cum)} / {_p(my_cum)}</span></div>",
            unsafe_allow_html=True,
        )

        # 민감도 3종: 누적(전체 구간) / 최근(최근 5구간) / 당일(마지막 1구간)
        basis = iva["sensitivity_basis"]
        s_all, s_rec, s_tod = iva["sens_all"], iva["sens_recent"], iva["sens_today"]

        def _s(v):
            return "—" if v is None else f"{v:+.2f}"

        st.markdown(
            f"<div style='font-size:11px;color:{T['muted']};margin:0 0 6px'>"
            f"민감도 <span style='color:{T['muted2']}'>({basis})</span>  "
            f"누적 <b>{_s(s_all)}</b> · 최근5 <b>{_s(s_rec)}</b> · 당일 <b>{_s(s_tod)}</b></div>",
            unsafe_allow_html=True,
        )

        # hover: 각 줄 앞을 "누적"으로 통일(어느 선인지는 색점으로 구분). 지수는 그대로,
        # 내 주식·내 계좌는 누적/당일 각각을 벤치(혼합지수)와 비교해 이겼으면 빨강/졌으면 파랑.
        moves = _index_day_moves(idx_hist).set_index("날짜")
        kd_map = moves["코스피d"].to_dict()
        qd_map = moves["코스닥d"].to_dict()

        def _fmt(v):
            return "—" if v is None or pd.isna(v) else f"{v * 100:+.2f}%"

        def _cell(v, ref):
            if v is None or pd.isna(v):
                return "—"
            s = f"{v * 100:+.2f}%"
            if ref is None or pd.isna(ref):
                return s
            return f"<span style='color:{UP_COLOR if v >= ref else DOWN_COLOR}'>{s}</span>"

        HT = "%{x}<br>누적 %{customdata[0]}  ·  당일 %{customdata[1]}<extra></extra>"

        fig2 = go.Figure()
        fig2.add_trace(go.Scatter(
            x=idxc["날짜"], y=idxc["코스피"], name="코스피", mode="lines",
            line=dict(color=KOSPI_COLOR, width=1.6),
            customdata=[[_fmt(c), _fmt(kd_map.get(d))] for c, d in zip(idxc["코스피"], idxc["날짜"])],
            hovertemplate=HT,
        ))
        fig2.add_trace(go.Scatter(
            x=idxc["날짜"], y=idxc["코스닥"], name="코스닥", mode="lines",
            line=dict(color=KOSDAQ_COLOR, width=1.6),
            customdata=[[_fmt(c), _fmt(qd_map.get(d))] for c, d in zip(idxc["코스닥"], idxc["날짜"])],
            hovertemplate=HT,
        ))
        fig2.add_trace(go.Scatter(
            x=me["날짜"], y=me["주식수익"], name="내 주식", mode="lines+markers",
            line=dict(color=T["text"], width=2.8), marker=dict(size=5),
            customdata=[[_cell(cr, br), _cell(dr, bd)] for cr, dr, br, bd
                        in zip(me["주식수익"], me["주식당일"], me["벤치누적"], me["벤치당일"])],
            hovertemplate=HT,
        ))
        fig2.add_trace(go.Scatter(
            x=me["날짜"], y=me["계좌수익"], name="내 계좌", mode="lines",
            line=dict(color=T["muted2"], width=1.8, dash="dot"),
            customdata=[[_cell(cr, br), _cell(dr, bd)] for cr, dr, br, bd
                        in zip(me["계좌수익"], me["계좌당일"], me["벤치누적"], me["벤치당일"])],
            hovertemplate=HT,
        ))
        fig2.add_hline(y=0, line_dash="dash", line_color=T["muted2"], line_width=1)
        fig2.update_layout(
            height=290,
            margin=dict(l=10, r=10, t=10, b=45),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color=T["text"], size=11),
            legend=dict(orientation="h", yanchor="top", y=-0.2, xanchor="center", x=0.5,
                        bgcolor="rgba(0,0,0,0)"),
            xaxis=dict(showgrid=False, tickfont=dict(size=9, color=T["muted"]), fixedrange=True),
            yaxis=dict(showgrid=True, gridcolor=T["border"], zeroline=False,
                       tickfont=dict(size=9, color=T["muted"]), tickformat=".1%", fixedrange=True),
            hovermode="x unified",
            dragmode=False,
        )
        st.plotly_chart(fig2, width="stretch", config={
            "displayModeBar": False,
            "scrollZoom": False,
            "doubleClick": False,
        })

    st.divider()

    # ---- 거래 내역 (캘린더) ----
    # 캘린더 위에 누적 매수/매도(건수+금액) + 누적 실현손익(금액+원금 대비 %) 요약
    # (2026-08-25, 사용자 요청 — 한 줄엔 안 들어가서 두 줄로: 매수/매도 줄, 그 아래 실현손익 줄.
    # 글자 크기는 실현손익 그래프 범례랑 맞춤). 실현손익은 함수 맨 위에서 이미 계산해둔
    # total_realized 재사용(요약카드와 같은 숫자).
    buy_tx = tx[tx["구분"] == "매수"]
    sell_tx = tx[tx["구분"] == "매도"]
    buy_total = (pd.to_numeric(buy_tx["수량"], errors="coerce")
                 * pd.to_numeric(buy_tx["단가"], errors="coerce")).sum()
    sell_total = (pd.to_numeric(sell_tx["수량"], errors="coerce")
                  * pd.to_numeric(sell_tx["단가"], errors="coerce")).sum()
    # 실현손익 %는 매수총액이 아니라 원금(초기자본) 대비로 계산 (2026-08-26, 사용자 요청 —
    # 매수총액 기준이면 물타기로 매수총액 자체가 계속 불어나서 같은 실현손익이라도 %가
    # 작아 보이는 문제가 있었음).
    realized_pct = (total_realized / state["initial"] * 100) if state["initial"] else 0.0
    # "평균"은 금액이 아니라 횟수 — 누적건수 / 총 거래일(전체 거래 기록에 등장하는 날짜 수),
    # 소수점 버림(2026-08-25, 사용자가 금액 평균으로 오해한 걸 정정).
    total_trade_days = tx["날짜"].nunique() if not tx.empty else 0
    buy_avg = int(len(buy_tx) / total_trade_days) if total_trade_days else 0
    sell_avg = int(len(sell_tx) / total_trade_days) if total_trade_days else 0
    st.markdown(f"""
    <div class="tx-cum-summary">
        <span>누적 매수 <b>{len(buy_tx)}건</b>(일평균 {buy_avg}건) · {buy_total:,.0f}원</span>
        <span>누적 매도 <b>{len(sell_tx)}건</b>(일평균 {sell_avg}건) · {sell_total:,.0f}원</span>
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
