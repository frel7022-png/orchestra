"""거래 기록 탭 (실현손익 그래프, 거래 캘린더)."""

import calendar

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

from constants import UP_COLOR, DOWN_COLOR, NEW_COLOR
from portfolio_core import (
    now_kst, today_kst_str, load_history, load_index_history, load_market_cache,
    compute_index_vs_account, compute_pnl_actions, _index_day_moves, seed_engine_series,
    load_bigcap_history, synthetic_kospi_ex_bigcap, synthetic_kospi_sh_only,
    load_fund_nav_history, compute_vip_vs_orchestra,
)

KOSPI_COLOR = "#f59e0b"   # 지수 참조선(코스피) — 앰버
KOSDAQ_COLOR = "#14b8a6"  # 지수 참조선(코스닥) — 틸
_PA_COLORS = {"FA": UP_COLOR, "MO": "#22c55e", "MA": DOWN_COLOR}  # FA 빨강 / MO 녹색 / MA 파랑


def render_transactions_tab(state, tx, holdings, total_assets, unrealized_loss, T):
    cap_return = total_assets - state["initial"]
    cap_return_pct = (cap_return / state["initial"] * 100) if state["initial"] else 0
    c3 = UP_COLOR if cap_return >= 0 else DOWN_COLOR
    s3 = "+" if cap_return >= 0 else ""

    _sell = tx[tx["구분"] == "매도"]
    total_realized = pd.to_numeric(_sell["실현손익"], errors="coerce").sum()
    rc = UP_COLOR if total_realized >= 0 else DOWN_COLOR
    rs = "+" if total_realized >= 0 else ""
    # 누적 세금 = Σ 매도금액 × fee_rate (매도세 0.2%). 실현손익은 이미 이걸 뺀 값.
    total_tax = (pd.to_numeric(_sell["수량"], errors="coerce")
                 * pd.to_numeric(_sell["단가"], errors="coerce")).sum() * state.get("fee_rate", 0.0)

    st.markdown(f"""
    <div class="summary-box">
        <div class="summary-label">최초 자본 {state['initial']:,.0f}원 대비</div>
        <span class="summary-main" style="color:{c3}">{s3}{cap_return:,.0f}원</span>
        <span class="summary-sub" style="color:{c3}">{s3}{cap_return_pct:.2f}%</span>
        <span style="font-size:12px;color:{DOWN_COLOR};margin-left:8px">누적 세금 -{total_tax:,.0f}원</span>
        <div class="summary-grid">
            <div>현재 총자산<b>{total_assets:,.0f}원</b></div>
            <div>실현손익 누적<b style="color:{rc}">{rs}{total_realized:,.0f}원</b></div>
            <div>미실현 손실<b style="color:{DOWN_COLOR}">-{unrealized_loss:,.0f}원</b></div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ---- 실현손익 그래프: 누적 실현손익(호버 시 그날 실현손익도 표시) vs 미실현손실 ----
    st.markdown("##### Realized P&L")

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
            line=dict(color=UP_COLOR, width=1.6), marker=dict(size=3),
            customdata=daily_values,
            hovertemplate="%{x}<br>누적 실현손익 %{y:,.0f}원<br>이날 실현손익 %{customdata:,.0f}원<extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            x=unreal_dates, y=unreal_series, mode="lines+markers", name="미실현손실",
            line=dict(color=DOWN_COLOR, width=1.6), marker=dict(size=3),
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

    # ---- P&L Actions (§6-20): 실현손익을 매매 스타일 FA/MO/MA로 해부 ----
    with st.expander("P&L Actions", expanded=False):
        pa = compute_pnl_actions(tx, holdings)
        if not pa["baskets"]:
            st.caption("완료된 매매 사이클이 없어요.")
        else:
            bk, pst, wd = pa["baskets"], pa["status"], pa["watering"]

            def _won(v):
                return f"{v:,.0f}원"

            def _pct(v):
                return "—" if v is None else f"{v:+.2f}%"

            _lab = ["FA", "MO", "MA"]

            def _plcol(v):  # 손익률: 음수=파랑(손실), 양수=빨강
                return DOWN_COLOR if v is not None and v < 0 else UP_COLOR

            # --- 표: 이름 | 실현 | 비중 | 손익률 — 짧은 이름(FA/MO/MA)으로 모바일 폭에 딱 맞춤.
            #     풀네임은 밑에 캡션 한 줄로. ---
            _td = "white-space:nowrap"
            _trs = "".join(
                f"<tr><td style='color:{_PA_COLORS[b]};font-weight:700'>{b}</td>"
                f"<td style='text-align:right;{_td}'>{bk[b]['realized']:,.0f}</td>"
                f"<td style='text-align:right'>{bk[b]['pct']:.1f}%</td>"
                f"<td style='text-align:right;{_td}'>{bk[b]['avg_pct']:+.2f}%</td></tr>"
                for b in _lab
            )
            st.markdown(
                "<table style='width:100%;font-size:11px;border-collapse:collapse;margin:0 0 2px;table-layout:fixed'>"
                f"<tr style='font-size:10px;color:{T['muted2']}'>"
                "<th style='text-align:left;width:22%'>&nbsp;</th><th style='text-align:right'>실현</th>"
                "<th style='text-align:right;width:20%'>비중</th><th style='text-align:right'>손익률</th></tr>"
                + _trs
                + f"<tr style='border-top:1px solid {T['border']};color:{T['text']};font-weight:700'>"
                  f"<td>Total</td><td style='text-align:right;{_td}'>{pa['total']:,.0f}</td>"
                  "<td style='text-align:right'>100%</td><td style='text-align:right'>—</td></tr>"
                "</table>"
                f"<div style='font-size:9.5px;color:{T['muted2']};margin:0 0 4px'>"
                "FA=First in, All out · MO=Multiple Out · MA=Multiple in, All out</div>",
                unsafe_allow_html=True,
            )

            # --- 도넛: 실현손익 버킷별. 글씨 전부 하얀색. expander 안에선 st.plotly_chart가
            #     폭 0으로 깨져서 components.html(iframe)+responsive로 렌더 ---
            if any(bk[b]["realized"] < 0 for b in _lab):
                st.caption("버킷 중 순손실이 있어 도넛 생략 — 위 표 참고.")
            else:
                # 작은 슬라이스(비중 < 12%)는 라벨 아예 빈 문자열 — 바깥으로 삐져나오지 않게.
                # domain 꽉 채우고 margin 최소 → 도넛이 iframe 정중앙.
                _slice_txt = [(f"{b} {bk[b]['pct']:.1f}%" if bk[b]["pct"] >= 12 else "") for b in _lab]
                fig_d = go.Figure(go.Pie(
                    labels=_lab, values=[bk[b]["realized"] for b in _lab],
                    hole=0.40, sort=False, direction="clockwise",
                    marker=dict(colors=[_PA_COLORS[b] for b in _lab]),
                    text=_slice_txt, textinfo="text", textposition="inside",
                    insidetextorientation="horizontal", textfont=dict(color="#ffffff", size=12),
                    domain=dict(x=[0, 1], y=[0, 1]),
                    hovertemplate="%{label}  %{value:,.0f}원 · %{percent}<extra></extra>",
                    hoverlabel=dict(bgcolor="#ffffff", bordercolor=T["border"],
                                    font=dict(color=T["text"], size=12)),  # 슬라이스색 무관 흰 박스로 통일
                ))
                fig_d.update_layout(
                    height=240, margin=dict(l=6, r=6, t=6, b=6),
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color=T["text"], size=11), showlegend=False,
                )
                components.html(
                    "<style>body{margin:0;background:transparent}</style>"
                    + fig_d.to_html(include_plotlyjs="cdn", full_html=False, default_width="100%",
                                    config={"displayModeBar": False, "responsive": True}),
                    height=244,
                )

            # --- 상태 테이블 (Numbers | Ratio(%)) — 값 한 줄로 ---
            _tot = pst["n_total"]
            fa_n, ma_n, mo_n = pst["FA"][0], pst["MA"][0], pst["MO"][0]
            h_n, opn_n = pst["holds"]
            w_n, _ = pst["watering"]

            def _sr(label, num, ratio):
                return (f"<tr><td style='color:{T['muted']};{_td}'>{label}</td>"
                        f"<td style='text-align:right;{_td}'>{num}</td>"
                        f"<td style='text-align:right;color:{T['muted2']};{_td}'>{ratio}</td></tr>")

            def _ratio_pl(n, tot_, pl):
                c = _plcol(pl)
                return (f"{n / tot_ * 100:.1f}%(<span style='color:{c}'>"
                        f"{'—' if pl is None else f'{pl:+.2f}%'}</span>)")

            rows = (
                _sr("총 횟수", _tot, "—")
                + _sr("FA", f"{fa_n}/{_tot}", f"{fa_n / _tot * 100:.1f}%")
                + _sr("MA", f"{ma_n}/{_tot}", f"{ma_n / _tot * 100:.1f}%")
                + _sr("MO", f"({pst['MO_closed']}/{mo_n})/{_tot}", f"{mo_n / _tot * 100:.1f}%")
                + _sr("Holds", f"{h_n}/{opn_n}", _ratio_pl(h_n, opn_n, pst["holds_pl_pct"]))
                + _sr("Watering", f"{w_n}/{opn_n}", _ratio_pl(w_n, opn_n, pst["watering_pl_pct"]))
            )
            st.markdown(
                "<div style='overflow-x:auto'>"
                "<table style='width:100%;font-size:12px;border-collapse:collapse;margin:6px 0 0'>"
                f"<tr style='font-size:10px;color:{T['muted2']}'>"
                "<th style='text-align:left'>&nbsp;</th><th style='text-align:right'>Numbers</th>"
                "<th style='text-align:right'>Ratio(%)</th></tr>"
                + rows + "</table></div>",
                unsafe_allow_html=True,
            )

            # --- Watering 상세 (3줄) ---
            _absorbed = "—" if wd["absorbed_pp"] is None else f"{wd['absorbed_pp']:+.2f}%p"
            _mult = "—" if wd["seed_mult"] is None else f"×{wd['seed_mult']:.2f}"
            _pf = "" if wd["pl_first_pct"] is None else f" (최초 진입가 기준 {wd['pl_first_pct']:+.2f}%)"
            st.markdown(
                f"<div style='font-size:11px;color:{T['muted']};margin:6px 0 0'>"
                f"<b>Watering</b> {wd['n_stock']}종목에 물타기(추가매수) {wd['n_extra_buys']}회 진행 중</div>"
                f"<div style='font-size:11px;color:{T['muted']};margin:1px 0'>"
                f"총 손익률 <b style='color:{_plcol(wd['pl_avg_pct'])}'>{_pct(wd['pl_avg_pct'])}</b>"
                f"<span style='color:{T['muted2']}'>{_pf}</span></div>"
                f"<div style='font-size:11px;color:{T['muted']};margin:1px 0 2px'>"
                f"물타기 흡수율 <b style='color:{UP_COLOR}'>{_absorbed}</b> · "
                f"시드 {wd['seed_first']:,.0f}원 → {wd['seed_now']:,.0f}원 <b>({_mult})</b></div>",
                unsafe_allow_html=True,
            )

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
    def _render_iva_panel(iva, idx_hist_local, kospi_label, carousel_id):
        """'지수 대비 계좌' 한 벌 = 2장짜리 스와이프 캐러셀. 1장 = [5줄 지수 표 + 선그래프],
        2장 = [하락/상승/even 캡처 표 3개 + 일별 캡처 막대]. 밑에 점 2개(클릭·좌우키로도 전환).
        캐러셀을 쓰는 이유: SamHynix expander 안에선 `st.plotly_chart`가 폭 0으로 깨지는데,
        components.html(iframe) + plotly `responsive:true`면 expander 열릴 때 알아서 리플로우됨
        (2026-09-07 재도입 — 세로 스택으로 바꿨다가 그래프 안 보이는 문제로 되돌림).
        메인(코스피)과 'SamHynix extracted'가 공유 — 표시 라벨만 kospi_label."""
        me, idxc, latest = iva["me"], iva["index"], iva["latest"]
        if me.empty or idxc.empty:
            st.info("시세를 새로고침하면 지수·자산 스냅샷이 쌓여서 그래프가 그려집니다.")
            return

        def _pct(v):
            return "—" if v is None else f"{v * 100:+.2f}%"

        bench = latest.get("벤치") or (None, None)

        def _color_vs_bench(v, ref):
            if v is None or ref is None:
                return T["text"]
            return UP_COLOR if v >= ref else DOWN_COLOR  # 벤치 이겼으면 빨강, 졌으면 파랑

        # 최근 5영업일(=시계열 5행 전 대비) 수익률: 코스피/코스닥은 매 거래일 점이라 5거래일,
        # 벤치/내 주식/내 계좌는 스냅샷 5구간.
        _s5 = {
            "코스피": idxc["코스피"] if "코스피" in idxc else None,
            "코스닥": idxc["코스닥"] if "코스닥" in idxc else None,
            "벤치": me["벤치누적"] if "벤치누적" in me else None,
            "주식": me["주식수익"] if "주식수익" in me else None,
            "계좌": me["계좌수익"] if "계좌수익" in me else None,
        }

        def _recent5(key):
            s = _s5.get(key)
            if s is None or len(s) < 6:
                return None
            a, b = s.iloc[-1], s.iloc[-6]
            if pd.isna(a) or pd.isna(b):
                return None
            return (1 + a) / (1 + b) - 1

        bench_r5 = _recent5("벤치")

        def _row(label, dot_color, dashed, key, color_by_bench):
            cum, day = latest.get(key, (None, None))
            rec = _recent5(key)
            if color_by_bench:
                cc = _color_vs_bench(cum, bench[0])
                rcc = _color_vs_bench(rec, bench_r5)
                dc = _color_vs_bench(day, bench[1])
            else:
                cc = rcc = dc = T["muted"]
            mark = "┈" if dashed else "●"
            return (
                f"<tr><td style='color:{dot_color}'>{mark}&nbsp;{label}</td>"
                f"<td style='text-align:right;color:{cc}'>{_pct(cum)}</td>"
                f"<td style='text-align:right;color:{rcc}'>{_pct(rec)}</td>"
                f"<td style='text-align:right;color:{dc}'>{_pct(day)}</td></tr>"
            )

        idx_table_html = (
            "<table style='width:100%;font-size:12px;border-collapse:collapse;margin:3px 0 4px'>"
            f"<tr style='color:{T['muted2']};font-size:10px'>"
            "<th style='text-align:left'>&nbsp;</th><th style='text-align:right'>누적</th>"
            "<th style='text-align:right'>5일</th><th style='text-align:right'>당일</th></tr>"
            + _row(kospi_label, KOSPI_COLOR, False, "코스피", False)
            + _row("코스닥", KOSDAQ_COLOR, False, "코스닥", False)
            + _row("혼합지수", DOWN_COLOR, False, "벤치", False)
            + _row("내 주식", T["text"], False, "주식", True)
            + _row("내 계좌", UP_COLOR, False, "계좌", True)
            + "</table>"
        )

        # ---- 하락 / 상승 캡처 + even 초과수익 + 승률 (2026-09-07, CR 대체) ----
        # 바구니마다 미니 표: 행 = 내 계좌 / 내 주식, 열 = 누적 / 당일 / Pct(승률).
        #   DC 누적 = Σ내당일/Σ벤치당일 (하락일 전체 — 일별 비율 평균이 아님, 튐 방지),
        #   UC 누적 = Σ내당일/Σ벤치당일 (상승일 전체),  even 누적 = even일 초과수익(%p) 단순평균.
        #   당일 = 오늘이 그 바구니면 그날 값(하락/상승은 일별 c, even은 %p), 아니면 —.
        #   Pct = ERA(하락 c<1) / 승률(상승 c>=1) / even 승률(e>=+0.1%).
        # 숫자 색: 하락 표 = 빨강, 상승 표 = 파랑(사용자 지정, 국내 관례 반대), even 표 = 회색.
        # "합친 지수" 없음(사용자 판단 2026-09-07) — 값들을 같이 읽음. 이 표들은 아래 fig2(선그래프)
        # 밑, fig_s(일별 캡처 막대) 바로 위에 렌더링됨 — 캡처 막대의 데이터 짝(사용자 요청 2026-09-07).
        cap_a, cap_s = iva["cap"]["acct"], iva["cap"]["stock"]
        nn = iva["n"]

        def _cap_tbl(title, color, want, avg_key, wr_key, is_pp):
            def _num(v):
                if v is None or (isinstance(v, float) and pd.isna(v)):
                    return "—"
                return f"{v * 100:+.2f}%" if is_pp else f"{v:.2f}"

            def _today(sm):
                return _num(sm.get("today")) if sm.get("today_bucket") == want else "—"

            def _wr(sm):
                tp = sm.get(wr_key)
                return "—" if not tp or tp[1] == 0 else f"{tp[0]}/{tp[1]}"

            rows = ""
            for lbl, sm in (("내 계좌", cap_a), ("내 주식", cap_s)):
                rows += (f"<tr><td style='color:{color}'>{lbl}</td>"
                         f"<td style='text-align:right;color:{color}'>{_num(sm.get(avg_key))}</td>"
                         f"<td style='text-align:right;color:{color}'>{_today(sm)}</td>"
                         f"<td style='text-align:right;color:{T['muted2']}'>{_wr(sm)}</td></tr>")
            return (
                "<table style='width:100%;font-size:12px;border-collapse:collapse;margin:3px 0 0'>"
                f"<tr style='font-size:10px'>"
                f"<th style='text-align:left;color:{color}'>{title}</th>"
                f"<th style='text-align:right;color:{T['muted2']}'>누적</th>"
                f"<th style='text-align:right;color:{T['muted2']}'>당일</th>"
                f"<th style='text-align:right;color:{T['muted2']}'>Pct</th></tr>"
                f"{rows}</table>"
            )

        caps_html = (
            _cap_tbl("DC ERA", DOWN_COLOR, "하락", "dc", "era", False)
            + _cap_tbl("UC ERA", UP_COLOR, "상승", "uc", "pct", False)
            + _cap_tbl("even 평균 · 승률 (±0.1%)", T["muted2"], "even", "even", "evr", True)
            + f"<div style='font-size:10px;color:{T['muted2']};margin:3px 0 4px'>"
              f"하락 {nn['down']} · 상승 {nn['up']} · even {nn['even']}</div>"
        )

        # hover(x unified): 실현손익 그래프와 같은 방식 — 날짜를 누르면 한 박스에 선별로
        # "이름  누적 X · 당일 Y" 한 줄씩. 내 주식·내 계좌 값은 벤치(혼합지수) 대비 이겼으면
        # 빨강 / 졌으면 파랑으로 칠함.
        moves = _index_day_moves(idx_hist_local).set_index("날짜")
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

        def _ht(label):
            # x-unified라 날짜는 박스 제목으로 이미 뜸 → %{x} 안 넣음(넣으면 줄마다 날짜 반복)
            return ("<b>" + label + "</b>  누적 %{customdata[0]} · 당일 %{customdata[1]}"
                    "<extra></extra>")

        fig2 = go.Figure()
        fig2.add_trace(go.Scatter(
            x=idxc["날짜"], y=idxc["코스피"], name=kospi_label, mode="lines",
            line=dict(color=KOSPI_COLOR, width=1.6),
            customdata=[[_fmt(c), _fmt(kd_map.get(d))] for c, d in zip(idxc["코스피"], idxc["날짜"])],
            hovertemplate=_ht(kospi_label),
        ))
        fig2.add_trace(go.Scatter(
            x=idxc["날짜"], y=idxc["코스닥"], name="코스닥", mode="lines",
            line=dict(color=KOSDAQ_COLOR, width=1.6),
            customdata=[[_fmt(c), _fmt(qd_map.get(d))] for c, d in zip(idxc["코스닥"], idxc["날짜"])],
            hovertemplate=_ht("코스닥"),
        ))
        # 혼합지수 = wk·코스피 + (1−wk)·코스닥 (보유비중 가중). wk 없으면 코스피 단독.
        _bw = wk if wk is not None else 1.0
        _blend_cum = [_bw * k + (1 - _bw) * q for k, q in zip(idxc["코스피"], idxc["코스닥"])]

        def _blend_day(d):
            k, q = kd_map.get(d), qd_map.get(d)
            if k is None or q is None or pd.isna(k) or pd.isna(q):
                return None
            return _bw * k + (1 - _bw) * q

        fig2.add_trace(go.Scatter(
            x=idxc["날짜"], y=_blend_cum, name="혼합지수", mode="lines",
            line=dict(color=DOWN_COLOR, width=1.6),
            customdata=[[_fmt(c), _fmt(_blend_day(d))] for c, d in zip(_blend_cum, idxc["날짜"])],
            hovertemplate=_ht("혼합지수"),
        ))
        fig2.add_trace(go.Scatter(
            x=me["날짜"], y=me["주식수익"], name="내 주식", mode="lines",
            line=dict(color=T["text"], width=1.6),
            customdata=[[_cell(cr, br), _cell(dr, bd)] for cr, dr, br, bd
                        in zip(me["주식수익"], me["주식당일"], me["벤치누적"], me["벤치당일"])],
            hovertemplate=_ht("내 주식"),
        ))
        fig2.add_trace(go.Scatter(
            x=me["날짜"], y=me["계좌수익"], name="내 계좌", mode="lines",
            line=dict(color=UP_COLOR, width=1.4),
            customdata=[[_cell(cr, br), _cell(dr, bd)] for cr, dr, br, bd
                        in zip(me["계좌수익"], me["계좌당일"], me["벤치누적"], me["벤치당일"])],
            hovertemplate=_ht("내 계좌"),
        ))
        fig2.add_hline(y=0, line_dash="dash", line_color=T["muted2"], line_width=1)
        fig2.update_layout(
            height=315,
            margin=dict(l=40, r=8, t=8, b=30),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color=T["text"], size=11),
            showlegend=False,  # 위 표(●코스피 ●코스닥 ●혼합지수 ●내 주식 ┈내 계좌)가 곧 범례
            hoverlabel=dict(bgcolor=T["card"], bordercolor=T["border"], align="left",
                            font=dict(size=11, color=T["text"])),
            xaxis=dict(showgrid=False, tickfont=dict(size=9, color=T["muted"]), fixedrange=True),
            yaxis=dict(showgrid=True, gridcolor=T["border"], zeroline=False,
                       tickfont=dict(size=9, color=T["muted"]), tickformat=".1%", fixedrange=True),
            hovermode="x unified",
            dragmode=False,
        )
        # ---- 일별 캡처 막대 (2026-09-07) : 하락일 빨강 / 상승일 파랑, y = 캡처 c, y=1 얇은 선 ----
        # 내 계좌 기준. even일은 그래프에서 완전히 제외(단위가 %p라 캡처 축과 안 섞임 — 값은 리스트로).
        # 하락일(DC) 파랑이 1 아래 = 방어 잘함, 상승일(UC) 빨강이 1 위 = 참여 잘함.
        _xs, _ys, _cols, _cd = [], [], [], []
        for dt, c, bk, bd, ad in zip(me["날짜"], me["캡처계좌"], me["바구니"],
                                     me["벤치당일"], me["계좌당일"]):
            if bk not in ("하락", "상승") or pd.isna(c):
                continue
            _xs.append(dt)
            _ys.append(float(c))
            _cols.append(DOWN_COLOR if bk == "하락" else UP_COLOR)
            _cd.append((bk, f"{bd * 100:+.2f}%" if pd.notna(bd) else "—",
                        f"{ad * 100:+.2f}%" if pd.notna(ad) else "—"))
        fig_s = go.Figure()
        fig_s.add_trace(go.Bar(
            x=_xs, y=_ys, marker_color=_cols, marker_line_width=0, customdata=_cd,
            hovertemplate=("<b>%{customdata[0]}일</b> 캡처 %{y:.2f}"
                           "<br>혼합지수 %{customdata[1]} · 내 계좌 %{customdata[2]}<extra></extra>"),
        ))
        fig_s.add_hline(y=1, line_dash="dash", line_color=T["muted2"], line_width=1)
        fig_s.update_layout(
            height=315,
            margin=dict(l=40, r=8, t=8, b=30),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color=T["text"], size=11),
            showlegend=False,  # 빨강=하락일·파랑=상승일, 위 리스트가 곧 범례
            bargap=0.3,
            hoverlabel=dict(bgcolor=T["card"], bordercolor=T["border"], align="left",
                            font=dict(size=11, color=T["text"])),
            xaxis=dict(showgrid=False, tickfont=dict(size=9, color=T["muted"]), fixedrange=True),
            yaxis=dict(showgrid=True, gridcolor=T["border"], zeroline=True, zerolinecolor=T["border"],
                       range=[-1.5, 3.0], dtick=1.0,
                       tickfont=dict(size=9, color=T["muted"]), tickformat=".1f", fixedrange=True),
            dragmode=False,
        )

        # ---- 2장 캐러셀: [지수 표 + 선그래프]  ↔  [캡처 표 3개 + 캡처 막대] ----
        _cfg = {"displayModeBar": False, "responsive": True, "scrollZoom": False, "doubleClick": False}
        _g1 = fig2.to_html(include_plotlyjs="cdn", full_html=False, config=_cfg, default_width="100%")
        _g2 = fig_s.to_html(include_plotlyjs=False, full_html=False, config=_cfg, default_width="100%")
        components.html(f"""
<div id="{carousel_id}">
  <div class="trk">
    <div class="sl">{idx_table_html}{_g1}</div>
    <div class="sl">{caps_html}{_g2}</div>
  </div>
  <div class="dt"><span class="d on"></span><span class="d"></span></div>
</div>
<style>
  body {{ margin:0; background:transparent; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
  /* iframe엔 Streamlit 기본 표 CSS가 없어서 셀 테두리가 사라짐 → 여기서 되살림 */
  #{carousel_id} table {{ border-collapse:collapse; color:{T['text']}; }}
  #{carousel_id} table td, #{carousel_id} table th {{ border:1px solid {T['border']}; padding:3px 7px; }}
  #{carousel_id} .trk {{ display:flex; overflow-x:auto; scroll-snap-type:x mandatory; overscroll-behavior-x:contain;
    -webkit-overflow-scrolling:touch; scrollbar-width:none; }}
  #{carousel_id} .trk::-webkit-scrollbar {{ display:none; }}
  #{carousel_id} .sl {{ flex:0 0 100%; min-width:0; scroll-snap-align:center; scroll-snap-stop:always;
    display:flex; flex-direction:column; justify-content:center; padding-top:4px; box-sizing:border-box; }}
  #{carousel_id} .dt {{ display:flex; justify-content:center; gap:11px; padding:8px 0 4px; }}
  #{carousel_id} .d {{ width:9px; height:9px; border-radius:50%; background:{T['muted2']}; opacity:.45;
    cursor:pointer; transition:opacity .18s, background .18s; }}
  #{carousel_id} .d.on {{ opacity:1; background:{T['text']}; }}
</style>
<script>
  (function() {{
    var trk = document.querySelector('#{carousel_id} .trk');
    var ds = document.querySelectorAll('#{carousel_id} .d');
    function sync() {{
      var i = Math.round(trk.scrollLeft / Math.max(trk.clientWidth, 1));
      ds.forEach(function(x, j) {{ x.classList.toggle('on', j === i); }});
    }}
    trk.addEventListener('scroll', sync, {{passive: true}});
    ds.forEach(function(x, j) {{ x.addEventListener('click', function() {{
      trk.scrollTo({{left: j * trk.clientWidth, behavior: 'smooth'}}); }}); }});
    trk.setAttribute('tabindex', '0');
    trk.addEventListener('keydown', function(e) {{
      var cur = Math.round(trk.scrollLeft / Math.max(trk.clientWidth, 1));
      if (e.key === 'ArrowRight') trk.scrollTo({{left: (cur + 1) * trk.clientWidth, behavior: 'smooth'}});
      if (e.key === 'ArrowLeft') trk.scrollTo({{left: (cur - 1) * trk.clientWidth, behavior: 'smooth'}});
    }});
  }})();
</script>
""", height=565)

    # ---- KOSPI 2-Track Trend: 일반(빨강) vs 삼성·삼성우·하이닉스 제외(파랑). 실제 지수 포인트로
    #      표시, hover엔 그 시점의 전일 대비 등락률(%). (2026-09-08: Account:Index 자리로 옮김 —
    #      Account:Index는 밑에서 expander로 접힘.) ----
    _bg_k = load_bigcap_history()
    if not idx_hist.empty and not _bg_k.empty:
        _ih = idx_hist.sort_values("날짜").reset_index(drop=True)
        _sh = synthetic_kospi_ex_bigcap(idx_hist, _bg_k).sort_values("날짜").reset_index(drop=True)
        _k = pd.to_numeric(_ih["KOSPI"], errors="coerce")
        _ke = pd.to_numeric(_sh["KOSPI"], errors="coerce")
        _kd = ["—" if pd.isna(d) else f"{d:+.2%}" for d in _k.pct_change()]
        _ked = ["—" if pd.isna(d) else f"{d:+.2%}" for d in _ke.pct_change()]
        st.markdown("##### KOSPI 2-Track Trend", unsafe_allow_html=True)
        fig_k = go.Figure()
        fig_k.add_trace(go.Scatter(
            x=_ih["날짜"], y=_k, name="코스피", mode="lines",
            line=dict(color=UP_COLOR, width=1.8), customdata=_kd,
            hovertemplate="<b>코스피</b> %{y:,.0f} · 전일 %{customdata}<extra></extra>"))
        fig_k.add_trace(go.Scatter(
            x=_sh["날짜"], y=_ke, name="삼성·하이닉스 제외", mode="lines",
            line=dict(color=DOWN_COLOR, width=1.8), customdata=_ked,
            hovertemplate="<b>삼성·하이닉스 제외</b> %{y:,.0f} · 전일 %{customdata}<extra></extra>"))
        fig_k.update_layout(
            height=210, margin=dict(l=48, r=8, t=6, b=24),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color=T["text"], size=11),
            legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0, font=dict(size=10)),
            hovermode="x unified",
            hoverlabel=dict(bgcolor=T["card"], bordercolor=T["border"],
                            font=dict(size=11, color=T["text"])),
            xaxis=dict(showgrid=False, tickfont=dict(size=9, color=T["muted"]), fixedrange=True),
            yaxis=dict(showgrid=True, gridcolor=T["border"], zeroline=False, tickformat=",.0f",
                       tickfont=dict(size=9, color=T["muted"]), fixedrange=True),
            dragmode=False,
        )
        st.plotly_chart(fig_k, use_container_width=True, config={"displayModeBar": False})

    # ---- Account : Index (§6-17): 코스피/코스닥 vs 내 주식·내 계좌. 2026-09-08부터 expander로
    #      접힘(SamHynix extracted 위, 같은 포맷). iva는 밑 VIP 패널도 쓰므로 expander 밖에서 계산. ----
    iva = compute_index_vs_account(tx, hist, idx_hist, state["initial"],
                                    state.get("fee_rate", 0.0), kospi_weight=wk,
                                    fund_nav_hist=load_fund_nav_history())
    with st.expander("Account : Index", expanded=False):
        if _wtag:
            st.markdown(f"<div style='margin:-4px 0 2px'>{_wtag.strip()}</div>",
                        unsafe_allow_html=True)
        _render_iva_panel(iva, idx_hist, "코스피", "cwrap")

    # ---- SamHynix extracted (§6-19): 혼합지수의 코스피 다리를 '삼성전자·삼성전자우·SK하이닉스
    #      제외 코스피'로 바꾼 버전. ----
    with st.expander("SamHYnix extracted", expanded=False):
        st.markdown(
            f"<div style='font-size:10px;color:{T['muted2']};white-space:nowrap;"
            f"overflow:hidden;text-overflow:ellipsis;margin:-2px 0 4px'>"
            f"혼합지수에 '삼성전자·삼성전자우·SK하이닉스 제외 코스피' 반영</div>",
            unsafe_allow_html=True)
        _bg = load_bigcap_history()
        if _bg.empty:
            st.caption("bigcap_history.csv 비어있음 — `python backfill_bigcap_history.py` 먼저.")
        else:
            _syn = synthetic_kospi_ex_bigcap(idx_hist, _bg)
            _iva_ex = compute_index_vs_account(tx, hist, _syn, state["initial"],
                                                state.get("fee_rate", 0.0), kospi_weight=wk)
            _render_iva_panel(_iva_ex, _syn, "삼성·하이닉스 제외", "cwrap_ex")

    # ---- Sour Grapes (§6-26): SamHynix extracted의 정반대 — 코스피 다리를 '삼성전자·
    #      삼성전자우·SK하이닉스만 담은 시총가중 바스켓(SH)'으로 바꾼 뒤, SH 지수와 내 계좌
    #      두 선만 8/14=0 기준으로 비교. 표/선 SH=파랑·내 계좌=빨강. 이름 뜻 = 여우와 신포도
    #      (안 담은 이 바스켓이 오르는 걸 보며 FOMO 유발 — 못 딴 포도가 시다). ----
    with st.expander("Sour Grapes", expanded=False):
        _bg2 = load_bigcap_history()
        if _bg2.empty or idx_hist.empty:
            st.caption("bigcap_history.csv 비어있음 — `python backfill_bigcap_history.py` 먼저.")
        else:
            _sh_only = synthetic_kospi_sh_only(idx_hist, _bg2)
            _iva_sh = compute_index_vs_account(tx, hist, _sh_only, state["initial"],
                                               state.get("fee_rate", 0.0), kospi_weight=wk)
            _me_sh, _idxc_sh, _lat_sh = _iva_sh["me"], _iva_sh["index"], _iva_sh["latest"]
            if _me_sh.empty or _idxc_sh.empty or "코스피" not in _idxc_sh:
                st.info("시세를 새로고침하면 지수·자산 스냅샷이 쌓여서 그래프가 그려집니다.")
            else:
                def _p2(v):
                    return "—" if v is None or pd.isna(v) else f"{v * 100:+.2f}%"

                def _r5(s):
                    if s is None or len(s) < 6:
                        return None
                    a, b = s.iloc[-1], s.iloc[-6]
                    if pd.isna(a) or pd.isna(b):
                        return None
                    return (1 + a) / (1 + b) - 1

                _sh_cum, _sh_day = _lat_sh.get("코스피", (None, None))
                _ac_cum, _ac_day = _lat_sh.get("계좌", (None, None))
                _sh_r5 = _r5(_idxc_sh["코스피"])
                _ac_r5 = _r5(_me_sh["계좌수익"] if "계좌수익" in _me_sh else None)

                def _trow2(label, color, cum, day, r5):
                    return (f"<tr><td style='color:{color}'>● {label}</td>"
                            f"<td style='text-align:right;color:{color}'>{_p2(cum)}</td>"
                            f"<td style='text-align:right;color:{color}'>{_p2(r5)}</td>"
                            f"<td style='text-align:right;color:{color}'>{_p2(day)}</td></tr>")

                _tbl = (
                    "<table style='width:100%;font-size:12px;border-collapse:collapse;margin:2px 0 6px'>"
                    f"<tr style='color:{T['muted2']};font-size:10px'>"
                    "<th style='text-align:left'>&nbsp;</th><th style='text-align:right'>누적</th>"
                    "<th style='text-align:right'>5일</th><th style='text-align:right'>당일</th></tr>"
                    + _trow2("SH", DOWN_COLOR, _sh_cum, _sh_day, _sh_r5)
                    + _trow2("내 계좌", UP_COLOR, _ac_cum, _ac_day, _ac_r5)
                    + "</table>"
                )

                fig_sh = go.Figure()
                fig_sh.add_trace(go.Scatter(
                    x=_idxc_sh["날짜"], y=_idxc_sh["코스피"], name="SH", mode="lines",
                    line=dict(color=DOWN_COLOR, width=1.8),
                    hovertemplate="<b>SH</b> %{y:+.2%}<extra></extra>"))
                fig_sh.add_trace(go.Scatter(
                    x=_me_sh["날짜"], y=_me_sh["계좌수익"], name="내 계좌", mode="lines",
                    line=dict(color=UP_COLOR, width=1.6),
                    hovertemplate="<b>내 계좌</b> %{y:+.2%}<extra></extra>"))
                fig_sh.add_hline(y=0, line_dash="dash", line_color=T["muted2"], line_width=1)
                fig_sh.update_layout(
                    height=250, margin=dict(l=44, r=8, t=8, b=26),
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color=T["text"], size=11), showlegend=False,
                    hovermode="x unified",
                    hoverlabel=dict(bgcolor=T["card"], bordercolor=T["border"],
                                    font=dict(size=11, color=T["text"])),
                    xaxis=dict(showgrid=False, tickfont=dict(size=9, color=T["muted"]), fixedrange=True),
                    yaxis=dict(showgrid=True, gridcolor=T["border"], zeroline=False, tickformat=".1%",
                               tickfont=dict(size=9, color=T["muted"]), fixedrange=True),
                    dragmode=False,
                )
                st.markdown(_tbl, unsafe_allow_html=True)
                components.html(
                    "<style>body{margin:0;background:transparent}</style>"
                    + fig_sh.to_html(include_plotlyjs="cdn", full_html=False, default_width="100%",
                                     config={"displayModeBar": False, "responsive": True}),
                    height=262,
                )

    # ---- VIP vs Orchestra (§6-21): VIP 펀드 vs new1 계좌(Orchestra), 둘 다 8/14=0.
    #      Orchestration(meritz)은 여기선 안 보여줌 — meritz 앱에서만 3-way (사용자 요청 2026-09-08).
    #      both_accounts.csv / sync_both_accounts.py 인프라는 meritz가 읽으므로 그대로 유지. ----
    with st.expander("VIP vs Orchestra", expanded=False):
        vo = compute_vip_vs_orchestra(iva)
        if not vo:
            st.caption("fund_nav_history.csv 비어있음 — 세션에 펀드 기준가를 알려주세요.")
        else:
            def _p(x):
                return "—" if x is None else f"{x * 100:+.2f}%"

            def _trow(label, dot, cum, day):
                return (f"<tr><td style='color:{dot}'>● {label}</td>"
                        f"<td style='text-align:right;color:{T['text']}'>{_p(cum)}</td>"
                        f"<td style='text-align:right;color:{T['text']}'>{_p(day)}</td></tr>")

            _rows = (_trow("VIP", DOWN_COLOR, *vo["vip"])
                     + _trow("Orchestra", UP_COLOR, *vo["orch"]))
            if vo.get("orchn_line"):
                _rows += _trow("Orchestration", NEW_COLOR, *vo["orchn"])
            st.markdown(
                "<table style='width:100%;font-size:12px;border-collapse:collapse;margin:2px 0 6px'>"
                f"<tr style='color:{T['muted2']};font-size:10px'>"
                "<th style='text-align:left'>&nbsp;</th><th style='text-align:right'>누적</th>"
                "<th style='text-align:right'>당일</th></tr>" + _rows + "</table>",
                unsafe_allow_html=True,
            )

            fig_vo = go.Figure()
            fig_vo.add_trace(go.Scatter(
                x=[d for d, _ in vo["vip_line"]], y=[y for _, y in vo["vip_line"]],
                name="VIP", mode="lines", line=dict(color=DOWN_COLOR, width=1.8),
                hovertemplate="<b>VIP</b> %{y:+.2%}<extra></extra>"))
            fig_vo.add_trace(go.Scatter(
                x=[d for d, _ in vo["orch_line"]], y=[y for _, y in vo["orch_line"]],
                name="Orchestra", mode="lines", line=dict(color=UP_COLOR, width=1.8),
                hovertemplate="<b>Orchestra</b> %{y:+.2%}<extra></extra>"))
            if vo.get("orchn_line"):
                fig_vo.add_trace(go.Scatter(
                    x=[d for d, _ in vo["orchn_line"]], y=[y for _, y in vo["orchn_line"]],
                    name="Orchestration", mode="lines", line=dict(color=NEW_COLOR, width=1.8),
                    hovertemplate="<b>Orchestration</b> %{y:+.2%}<extra></extra>"))
            fig_vo.add_hline(y=0, line_dash="dash", line_color=T["muted2"], line_width=1)
            fig_vo.update_layout(
                height=250, margin=dict(l=44, r=8, t=8, b=26),
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color=T["text"], size=11), showlegend=False,
                hovermode="x unified",
                hoverlabel=dict(bgcolor=T["card"], bordercolor=T["border"],
                                font=dict(size=11, color=T["text"])),
                xaxis=dict(showgrid=False, tickfont=dict(size=9, color=T["muted"]), fixedrange=True),
                yaxis=dict(showgrid=True, gridcolor=T["border"], zeroline=False, tickformat=".1%",
                           tickfont=dict(size=9, color=T["muted"]), fixedrange=True),
                dragmode=False,
            )
            components.html(
                "<style>body{margin:0;background:transparent}</style>"
                + fig_vo.to_html(include_plotlyjs="cdn", full_html=False, default_width="100%",
                                 config={"displayModeBar": False, "responsive": True}),
                height=262,
            )

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

    # ---- Seed Engine (§6-27): 빨강(Cost Basis)은 우상향, 파랑(W Fuel=예수금)은 평행이어야 정상.
    #      녹색(W/o Fuel=씨앗 없었으면 남았을 현금)이 파랑보다 더 가파르게 떨어짐 — 파랑과 녹색의
    #      간격 = 씨앗(실현손익)이 채워준 연료. 우리가 보려는 건 그 둘(≈270 vs ≈315)의 관계. ----
    _se = seed_engine_series(tx, state["initial"], state.get("fee_rate", 0.0), hist)
    if len(_se) >= 2:
        st.markdown("##### Seed Engine")
        _ta = _se["총자산"].replace(0, pd.NA)

        def _rat(col):  # 그 값이 총자산의 몇 %
            return (_se[col] / _ta * 100).fillna(0).tolist()

        fig_se = go.Figure()
        fig_se.add_trace(go.Scatter(
            x=_se["날짜"], y=_se["총매입"], name="Cost Basis", mode="lines",
            line=dict(color=UP_COLOR, width=2), customdata=_rat("총매입"),
            hovertemplate="총매입 %{y:,.0f}원 (%{customdata:.0f}% 총매입/총자산)<extra></extra>"))
        fig_se.add_trace(go.Scatter(
            x=_se["날짜"], y=_se["예수금"], name="W Fuel", mode="lines",
            line=dict(color=DOWN_COLOR, width=2), customdata=_rat("예수금"),
            hovertemplate="W Fuel %{y:,.0f}원 (%{customdata:.0f}% 예수금/총자산)<extra></extra>"))
        fig_se.add_trace(go.Scatter(
            x=_se["날짜"], y=_se["무연료예수금"], name="W/o Fuel", mode="lines",
            line=dict(color=NEW_COLOR, width=1.6, dash="dot"), customdata=_rat("무연료예수금"),
            hovertemplate="W/o Fuel 씨앗없을시 %{y:,.0f}원 (%{customdata:.0f}% 씨앗없을시/총자산)<extra></extra>"))
        fig_se.update_layout(
            height=250, margin=dict(l=48, r=8, t=8, b=26),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color=T["text"], size=11), showlegend=False,
            hovermode="x unified",
            hoverlabel=dict(bgcolor=T["card"], bordercolor=T["border"], font=dict(size=11, color=T["text"])),
            xaxis=dict(showgrid=False, tickfont=dict(size=9, color=T["muted"]), fixedrange=True),
            yaxis=dict(showgrid=True, gridcolor=T["border"], zeroline=False, tickformat=",.0f",
                       tickfont=dict(size=9, color=T["muted"]), fixedrange=True),
            dragmode=False,
        )
        st.plotly_chart(fig_se, use_container_width=True, config={"displayModeBar": False})

    st.markdown("##### History Calendar")

    if "cal_year" not in st.session_state:
        st.session_state.cal_year = now_kst().year
        st.session_state.cal_month = now_kst().month
    if "selected_tx_date" not in st.session_state:
        st.session_state.selected_tx_date = today_kst_str()

    tx_dates = set(tx["날짜"].astype(str))
    year, month = st.session_state.cal_year, st.session_state.cal_month

    # 달력이 보여주는 그 달의 실현손익 합계(그 달 매도 실현손익 합) — 달을 넘기면 같이 바뀜
    _mo_prefix = f"{year:04d}-{month:02d}"
    _mo_sell = tx[(tx["구분"] == "매도") & (tx["날짜"].astype(str).str.startswith(_mo_prefix))]
    _mo_realized = pd.to_numeric(_mo_sell["실현손익"], errors="coerce").sum()
    _mo_rc = UP_COLOR if _mo_realized >= 0 else DOWN_COLOR
    _mo_rs = "+" if _mo_realized >= 0 else ""

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
            f"<div style='text-align:center;font-weight:700;padding-top:2px;color:{T['text']}'>"
            f"{year}년 {month}월"
            f"<span style='display:block;font-weight:600;font-size:12px;color:{_mo_rc}'>"
            f"{_mo_rs}{_mo_realized:,.0f}원</span></div>",
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
            memo_html = f' · {r["메모"]}' if str(r["메모"]) not in ("", "nan") else ""
            if r["구분"] in ("입금", "출금"):
                amt = float(r["수량"]) * float(r["단가"])
                sign = "+" if r["구분"] == "입금" else "-"
                right_html = f'<span style="color:{UP_COLOR if r["구분"] == "입금" else DOWN_COLOR}">{sign}{amt:,.0f}원</span>'
                card_parts.append(f"""
            <div class="tx-card">
                <div class="tx-left">
                    <span class="name">{str(r["메모"]) if str(r["메모"]) not in ("", "nan") else r["구분"]}</span>
                    <span class="meta">{r['구분']}</span>
                </div>
                <div class="tx-right">{right_html}</div>
            </div>
            """)
                continue
            if r["구분"] == "매도" and str(realized) not in ("", "nan"):
                rv = float(realized)
                trc = UP_COLOR if rv >= 0 else DOWN_COLOR
                trs = "+" if rv >= 0 else ""
                right_html = f'<span style="color:{trc}">{trs}{rv:,.0f}원</span>'
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
