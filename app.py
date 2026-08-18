"""
한국 주식 포트폴리오 트래커 (Streamlit) — Meritz Orchestra
------------------------------------------------------------------
    streamlit run app.py

시세는 네이버 금융 비공식 공개 API를 사용합니다(종목코드 자동 검색 포함).
데이터 계층(로드/저장/replay/시세조회)은 portfolio_core.py에 있음 —
일일 매매일지 반영 스크립트(ingest_daily.py)와 로직을 공유하기 위해서다.
"""

import calendar

import matplotlib.pyplot as plt
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from portfolio_core import (
    group_sector,
    now_kst, today_kst_str, now_kst_str,
    load_holdings, load_transactions,
    load_state,
    load_history, snapshot_history,
    load_sector_history, snapshot_sector_history,
    refresh_all_prices, fetch_index_quotes, get_current_prices_for_names, get_closed_out_last_sells,
    compute_metrics, compute_sector_weights,
)

UP_COLOR = "#d9364f"    # 국내 관례: 상승/이익 = 빨강
DOWN_COLOR = "#2b6cd4"  # 하락/손실 = 파랑
CASH_LABEL = "현금(예수금)"

SECTOR_PALETTE = [
    "#2DD4BF", "#F5A623", "#A78BFA", "#34D399", "#F472B6",
    "#FBBF24", "#60A5FA", "#F87171", "#C084FC", "#38BDF8", "#FB923C",
]

# 섹터별 목표 비중(주식 총자산 대비, %). 아직 정하지 않은 섹터는 포함하지 않음 — 추후 추가.
SECTOR_TARGETS = {
    "식품": 30.0,
    "소비재": 20.0,
}

THEMES = {
    "dark": {
        "bg": "#0a0c10", "card": "#12151c", "card2": "#20242e", "border": "#2b303c",
        "text": "#e8eaed", "muted": "#9aa4b2", "muted2": "#6b7280", "cash_dot": "#4b5563",
    },
    "light": {
        "bg": "#f4f5f7", "card": "#ffffff", "card2": "#eceef1", "border": "#e2e4e9",
        "text": "#1a1d23", "muted": "#5b6472", "muted2": "#7a8290", "cash_dot": "#9aa0ab",
    },
}


def theme() -> dict:
    return THEMES[st.session_state.get("theme", "light")]



# ------------------------------------------------------------------ #
# 페이지 설정
# ------------------------------------------------------------------ #
st.set_page_config(page_title="Meritz Orchestra", page_icon="◆", layout="centered")

if "theme" not in st.session_state:
    st.session_state["theme"] = "light"
T = theme()

st.markdown(f"""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Sora:wght@600;700;800&display=swap');
    .stApp {{ background-color: {T['bg']}; }}
    .brand {{
        font-family: 'Sora', sans-serif;
        font-weight: 800;
        font-size: 18px;
        letter-spacing: 0.01em;
        color: {T['text']};
        padding: 6px 0;
        white-space: nowrap;
    }}
    .block-container {{ padding-top: 1.1rem; padding-bottom: 2rem; padding-left: 1rem; padding-right: 1rem; max-width: 480px; }}
    #MainMenu, footer, header {{ visibility: hidden; }}
    h1, h2, h3, h4, h5, p, span, label, div {{ color: {T['text']}; }}

    .summary-box {{ background:{T['card']}; border:1px solid {T['border']}; border-radius:14px; padding:18px 20px; margin-bottom:14px; }}
    .summary-label {{ color:{T['muted']}; font-size:13px; margin-bottom:4px; }}
    .summary-main {{ font-size:28px; font-weight:800; line-height:1.2; }}
    .summary-sub {{ font-size:14px; font-weight:600; margin-left:6px; }}
    .summary-grid {{ display:flex; flex-wrap:wrap; justify-content:space-between; margin-top:14px; gap:8px; }}
    .summary-grid div {{ font-size:12.5px; color:{T['muted']}; min-width:29%; }}
    .summary-grid b {{ display:block; font-size:15px; color:{T['text']}; margin-top:2px; }}
    .capital-line {{ margin-top:10px; padding-top:10px; border-top:1px solid {T['border']}; font-size:12.5px; color:{T['muted']}; }}
    .capital-line b {{ font-size:14px; }}

    .daily-trade-box {{ margin-top:10px; padding-top:10px; border-top:1px solid {T['border']}; font-size:12.5px; color:{T['muted']}; }}
    .daily-trade-count {{ font-size:13px; color:{T['text']}; font-weight:700; margin-bottom:6px; }}
    .daily-trade-count span {{ font-weight:400; color:{T['muted']}; margin-left:4px; }}
    .daily-trade-row {{ display:flex; flex-wrap:wrap; gap:6px 8px; align-items:baseline; margin-top:4px; }}
    .daily-trade-row .tag-label {{ font-size:12px; font-weight:700; min-width:30px; }}
    .trade-chip {{ font-size:12px; background:{T['bg']}; border:1px solid {T['border']}; border-radius:99px; padding:2px 9px; color:{T['text']}; }}
    .trade-chip b {{ font-weight:600; }}

    .legend-wrap {{ display:flex; flex-wrap:wrap; gap:7px 14px; margin-top:10px; justify-content:center; }}
    .legend-item {{ display:flex; align-items:center; gap:5px; font-size:12px; color:{T['text']}; }}
    .legend-dot {{ width:8px; height:8px; border-radius:99px; flex-shrink:0; }}
    .legend-pct {{ color:{T['muted']}; font-family: ui-monospace, monospace; }}

    .sector-bar-list {{ margin-top:10px; }}
    .sector-bar-row {{ display:flex; align-items:center; gap:6px; margin-bottom:14px; }}
    .sector-bar-label {{ font-size:11px; font-weight:600; color:{T['text']}; width:64px; flex-shrink:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
    .sector-bar-track {{ position:relative; flex:1; height:14px; background:{T['card']}; border:1px solid {T['border']}; border-radius:999px; overflow:visible; }}
    .sector-bar-fill {{ position:absolute; left:1px; top:1px; bottom:1px; border-radius:999px; }}
    .sector-target-marker {{ position:absolute; top:-2px; bottom:-2px; width:2px; background:{T['text']}; opacity:0.55; }}
    .sector-target-label {{ position:absolute; top:100%; margin-top:2px; transform:translateX(-50%); font-size:9.5px; color:{T['muted2']}; white-space:nowrap; }}
    .sector-bar-pct {{ font-size:12px; color:{T['muted']}; width:64px; flex-shrink:0; text-align:right; font-family: ui-monospace, monospace; white-space:nowrap; }}
    .sector-bar-pct .cur {{ font-weight:700; color:{T['text']}; }}
    .sector-bar-pct .delta {{ margin-left:3px; }}
    .sector-stock-names {{ font-size:10.5px; color:{T['muted2']}; margin:2px 0 0 2px; }}

    .updown-row {{ display:flex; align-items:center; gap:8px; padding:7px 2px; border-bottom:1px solid {T['border']}; font-size:12.5px; }}
    .updown-row:last-child {{ border-bottom:none; }}
    .updown-row .name {{ font-weight:700; color:{T['text']}; flex:1; }}
    .updown-row .pct {{ font-weight:700; font-family: ui-monospace, monospace; width:62px; text-align:right; }}
    .updown-row .detail {{ font-size:11px; color:{T['muted']}; font-family: ui-monospace, monospace; width:118px; text-align:right; }}


    .stock-card {{ background:{T['card']}; border:1px solid {T['border']}; border-radius:12px; padding:10px 16px; margin-bottom:7px; }}
    .stock-top {{ display:flex; justify-content:space-between; align-items:baseline; }}
    .stock-name {{ font-size:15px; font-weight:700; color:{T['text']}; }}
    .stock-weight-inline {{ font-size:11px; color:{T['muted']}; margin-left:6px; }}
    .sector-tag {{ font-size:10.5px; padding:2px 7px; border-radius:5px; font-weight:600; flex-shrink:0; }}
    .stock-grid {{ display:grid; grid-template-columns: 0.7fr 1.05fr 1.05fr 1.3fr; gap:6px; margin-top:7px; }}
    .cell .top {{ font-size:12.5px; font-weight:700; color:{T['text']}; }}
    .cell .bottom {{ font-size:11px; color:{T['muted']}; margin-top:2px; }}
    .stock-foot {{ display:flex; justify-content:flex-end; margin-top:6px; font-size:10px; color:{T['muted2']}; }}

    .tx-card {{ background:{T['card']}; border:1px solid {T['border']}; border-radius:10px; padding:10px 14px; margin-bottom:6px; display:flex; justify-content:space-between; align-items:center; }}
    .tx-left {{ font-size:13px; }}
    .tx-left .name {{ font-weight:700; color:{T['text']}; }}
    .tx-left .meta {{ color:{T['muted']}; font-size:11.5px; }}
    .tx-right {{ text-align:right; font-size:13px; font-weight:700; }}

    /* ---- 옅은/짙은 회색 버튼: 눌러도 색 안 바뀌게 강제 고정 ---- */
    div.stButton > button,
    div.stButton > button:hover,
    div.stButton > button:active,
    div.stButton > button:focus,
    div.stButton > button:focus:not(:active) {{
        background-color: {T['card2']} !important;
        color: {T['text']} !important;
        border: 1px solid {T['border']} !important;
        box-shadow: none !important;
        border-radius: 8px;
        font-weight: 600;
        font-size: 11px;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        padding: 0.2rem 0.35rem;
        min-height: 1.7rem;
    }}
    div.stButton > button p {{ color: {T['text']} !important; }}
    div.stFormSubmitButton > button,
    div.stFormSubmitButton > button:hover,
    div.stFormSubmitButton > button:active,
    div.stFormSubmitButton > button:focus {{
        background-color: {T['card2']} !important;
        color: {T['text']} !important;
        border: 1px solid {T['border']} !important;
        box-shadow: none !important;
    }}
    div[data-testid="stPopover"] > div > button,
    div[data-testid="stPopover"] > div > button:hover,
    div[data-testid="stPopover"] > div > button:active,
    div[data-testid="stPopover"] > div > button:focus {{
        background-color: {T['card2']} !important;
        color: {T['text']} !important;
        border: 1px solid {T['border']} !important;
        box-shadow: none !important;
    }}

    [data-testid="stExpander"],
    [data-testid="stExpander"] > details,
    [data-testid="stExpander"] summary,
    [data-testid="stExpander"] summary > div,
    [data-testid="stExpander"] div[data-testid="stExpanderDetails"] {{
        background-color: {T['card']} !important;
        border-color: {T['border']} !important;
        border-radius: 12px !important;
    }}
    [data-testid="stExpander"] summary,
    [data-testid="stExpander"] summary:hover,
    [data-testid="stExpander"] summary p,
    [data-testid="stExpander"] svg {{
        color: {T['text']} !important;
        fill: {T['text']} !important;
        background-color: {T['card']} !important;
    }}

    div[data-testid="stTextInput"] div,
    div[data-testid="stNumberInput"] div,
    div[data-testid="stSelectbox"] div,
    div[data-testid="stDateInput"] div,
    div[data-testid="stTextArea"] div,
    div[data-baseweb="input"],
    div[data-baseweb="base-input"],
    div[data-baseweb="textarea"],
    div[data-baseweb="select"] {{
        background-color: {T['card2']} !important;
        border-color: {T['border']} !important;
        box-shadow: none !important;
    }}
    div[data-testid="stTextInput"] input,
    div[data-testid="stNumberInput"] input,
    div[data-testid="stSelectbox"] input,
    div[data-testid="stDateInput"] input,
    div[data-testid="stTextArea"] textarea,
    input, textarea, select {{
        background-color: transparent !important;
        color: {T['text']} !important;
        border: none !important;
    }}
    div[data-baseweb="input"],
    div[data-baseweb="base-input"],
    div[data-baseweb="select"] > div:first-child {{
        border: 1px solid {T['border']} !important;
        border-radius: 8px !important;
    }}

    /* 드롭다운을 눌렀을 때 뜨는 목록(팝업)은 별도 레이어라 위 규칙이 안 먹어서 따로 지정 */
    div[data-baseweb="popover"],
    div[data-baseweb="popover"] div,
    div[data-baseweb="menu"],
    ul[role="listbox"] {{
        background-color: {T['card2']} !important;
    }}
    li[role="option"] {{
        background-color: {T['card2']} !important;
        color: {T['text']} !important;
    }}
    li[role="option"]:hover,
    li[role="option"][aria-selected="true"] {{
        background-color: {T['border']} !important;
        color: {T['text']} !important;
    }}
    button[data-testid="stNumberInputStepDown"],
    button[data-testid="stNumberInputStepUp"],
    button[data-testid="stNumberInputStepDown"]:hover,
    button[data-testid="stNumberInputStepUp"]:hover {{
        background-color: {T['card2']} !important;
        border: 1px solid {T['border']} !important;
        color: {T['text']} !important;
    }}
    svg {{ fill: {T['muted']} !important; }}

    /* 라디오/토글: 동그라미 표시를 완전히 숨기고 텍스트 알약(pill)만 남김 */
    div[data-testid="stToggle"] label,
    div[data-testid="stToggle"] span,
    div[data-testid="stToggle"] div,
    div[data-testid="stToggle"] [role="switch"] {{
        background-color: {T['card2']} !important;
        border-color: {T['border']} !important;
    }}
    div[data-testid="stToggle"] [role="switch"][aria-checked="true"] {{
        background-color: {T['muted2']} !important;
    }}
    div[data-testid="stToggle"] [role="switch"] > div {{
        background-color: #fff !important;
    }}
    div[role="radiogroup"] {{
        flex-wrap: nowrap !important;
        gap: 3px 4px !important;
    }}
    div[role="radiogroup"] label {{
        background-color: {T['card2']} !important;
        border: 1px solid {T['border']} !important;
        border-radius: 7px !important;
        padding: 2px 6px !important;
        margin: 0 !important;
        min-height: 0 !important;
        flex-shrink: 1 !important;
    }}
    div[role="radiogroup"] label > *:first-child,
    div[role="radiogroup"] label svg,
    div[role="radiogroup"] [data-baseweb="radio"] > div:first-child {{
        display: none !important;
        width: 0 !important;
        height: 0 !important;
    }}
    div[role="radiogroup"] label div[data-testid="stMarkdownContainer"] p {{
        font-size: 11px !important;
        color: {T['text']} !important;
        white-space: nowrap;
    }}
    div[role="radiogroup"] label[aria-checked="true"] {{
        border: 1.5px solid {T['muted2']} !important;
        font-weight: 700;
    }}
    /* 모든 가로 배치(달력 포함)를 어떤 화면 크기에서도 한 줄로 강제 */
    div[data-testid="stHorizontalBlock"] {{
        flex-direction: row !important;
        flex-wrap: nowrap !important;
        gap: 3px !important;
    }}
    div[data-testid="column"],
    div[data-testid="stColumn"] {{
        width: 0 !important;
        min-width: 0 !important;
        flex: 1 1 0 !important;
        padding: 0 1px !important;
    }}
    div.stButton > button {{
        padding-left: 0.2rem !important;
        padding-right: 0.2rem !important;
    }}
</style>
""", unsafe_allow_html=True)


def check_password() -> bool:
    if "app_password" not in st.secrets:
        return True
    if st.session_state.get("authed"):
        return True
    st.markdown('<div class="brand">Meritz Orchestra</div>', unsafe_allow_html=True)
    pw = st.text_input("비밀번호를 입력하세요", type="password")
    if pw:
        if pw == st.secrets["app_password"]:
            st.session_state["authed"] = True
            st.rerun()
        else:
            st.error("비밀번호가 틀렸습니다.")
    return False


if not check_password():
    st.stop()

col_title, col_label, col_theme = st.columns([2.2, 1, 0.9])
with col_title:
    st.markdown('<div class="brand">Meritz Orchestra</div>', unsafe_allow_html=True)
with col_label:
    st.markdown(
        f"<div style='text-align:right;font-size:11px;color:{T['muted']};padding-top:12px;'>화면</div>",
        unsafe_allow_html=True,
    )
with col_theme:
    is_dark = st.session_state["theme"] == "dark"
    new_dark = st.toggle("다크", value=is_dark, key="theme_switch", label_visibility="collapsed")
    if new_dark != is_dark:
        st.session_state["theme"] = "dark" if new_dark else "light"
        st.rerun()

holdings = load_holdings()
state = load_state()
tx = load_transactions()

if "index_quotes" not in st.session_state:
    st.session_state["index_quotes"] = fetch_index_quotes()

top_l, top_r = st.columns([5, 2])
with top_r:
    refresh_clicked_top = st.button("시세 새로고침", use_container_width=True, key="refresh_btn_top")

if refresh_clicked_top:
    with st.spinner("종목명으로 시세를 찾는 중..."):
        holdings, refresh_report = refresh_all_prices(holdings)
        st.session_state["index_quotes"] = fetch_index_quotes()
        df_top, stock_val_top, total_assets_top, unreal_top = compute_metrics(holdings, state["cash"])
        snapshot_history(total_assets_top, total_assets_top + unreal_top)
        snapshot_sector_history(compute_sector_weights(df_top))
    if refresh_report["updated"]:
        st.toast(f"{refresh_report['updated']}개 종목 시세 갱신 완료")
    if refresh_report["unresolved"]:
        st.warning("종목명을 찾지 못했어요(직접 입력 필요): " + ", ".join(refresh_report["unresolved"]))
    if refresh_report["failed"]:
        st.warning("시세를 못 가져왔어요(직접 입력 필요): " + ", ".join(refresh_report["failed"]))
    for err in refresh_report["quote_errors"]:
        st.warning(err)
    st.rerun()

tab_port, tab_tx = st.tabs(["포트폴리오", "거래 기록"])

# ==================================================================== #
# 탭 1: 포트폴리오
# ==================================================================== #
with tab_port:
    df, stock_valuation, total_assets, unrealized_loss = compute_metrics(holdings, state["cash"])
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

    # ---- 섹터 비중 도넛 + 목표 비중 관리 ----
    with st.expander("섹터 비중 보기", expanded=False):
        include_cash = st.toggle("예수금 포함", value=st.session_state.get("include_cash", True), key="cash_toggle")
        st.session_state["include_cash"] = include_cash

        # 도넛/막대 공통 색상: 주식(예수금 제외) 비중 기준으로 순위를 매겨 고정 배정
        stock_weights = compute_sector_weights(df)  # {섹터그룹: 주식 총자산 대비 %}
        stock_weight_rank = sorted(stock_weights.items(), key=lambda x: x[1], reverse=True)
        color_map = {name: SECTOR_PALETTE[i % len(SECTOR_PALETTE)] for i, (name, _) in enumerate(stock_weight_rank)}

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
                color = color_map.get(name, "#888")
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
                        f'<div class="sector-bar-fill" style="background:{color}; width:{width_pct}%"></div>'
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
                        parts.append(f'<polyline points="{poly}" fill="none" stroke="{color}" stroke-width="2.5" />')
                        for x, y, v, d in zip(xs, ys, vals, dates):
                            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{color}" />')
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

    sector_color_map = {}
    for i, s in enumerate(df.sort_values("평가금액", ascending=False)["섹터"].unique()):
        sector_color_map[s] = SECTOR_PALETTE[i % len(SECTOR_PALETTE)]

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
        for r in rows:
            pc = UP_COLOR if r["손익"] >= 0 else DOWN_COLOR
            psign = "+" if r["손익"] >= 0 else ""
            cc = UP_COLOR if r["등락률"] >= 0 else DOWN_COLOR
            csign = "+" if r["등락률"] >= 0 else ""
            sc = sector_color_map.get(r["섹터"], "#6b7280")

            st.markdown(f"""
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
            """, unsafe_allow_html=True)

# ==================================================================== #
# 탭 2: 거래 기록 + 자산 추이
# ==================================================================== #
with tab_tx:
    df3, stock_val3, total_assets3, unreal3 = compute_metrics(holdings, state["cash"])
    cap_return3 = total_assets3 - state["initial"]
    cap_return_pct3 = (cap_return3 / state["initial"] * 100) if state["initial"] else 0
    c3 = UP_COLOR if cap_return3 >= 0 else DOWN_COLOR
    s3 = "+" if cap_return3 >= 0 else ""

    total_realized = pd.to_numeric(tx.loc[tx["구분"] == "매도", "실현손익"], errors="coerce").sum()
    rc = UP_COLOR if total_realized >= 0 else DOWN_COLOR
    rs = "+" if total_realized >= 0 else ""

    st.markdown(f"""
    <div class="summary-box">
        <div class="summary-label">최초 자본 10,000,000원 대비</div>
        <span class="summary-main" style="color:{c3}">{s3}{cap_return3:,.0f}원</span>
        <span class="summary-sub" style="color:{c3}">{s3}{cap_return_pct3:.2f}%</span>
        <div class="summary-grid">
            <div>현재 총자산<b>{total_assets3:,.0f}원</b></div>
            <div>실현손익 누적<b style="color:{rc}">{rs}{total_realized:,.0f}원</b></div>
            <div>미실현 손실<b style="color:{DOWN_COLOR}">-{unreal3:,.0f}원</b></div>
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
        rc = UP_COLOR if day_realized >= 0 else DOWN_COLOR
        rs = "+" if day_realized >= 0 else ""
        head_html += f" <span style='color:{rc};font-size:13px'>({rs}{day_realized:,.0f}원)</span>"
    st.markdown(head_html, unsafe_allow_html=True)

    if day_tx.empty:
        st.info("이 날짜엔 기록된 거래가 없습니다.")
    else:
        for _, r in day_tx.iterrows():
            realized = r["실현손익"]
            right_html = ""
            if r["구분"] == "매도" and str(realized) not in ("", "nan"):
                rv = float(realized)
                rc = UP_COLOR if rv >= 0 else DOWN_COLOR
                rs = "+" if rv >= 0 else ""
                right_html = f'<span style="color:{rc}">{rs}{rv:,.0f}원</span>'
            memo_html = f' · {r["메모"]}' if str(r["메모"]) not in ("", "nan") else ""
            st.markdown(f"""
            <div class="tx-card">
                <div class="tx-left">
                    <span class="name">{r['종목명']}</span>
                    <span class="meta">{r['구분']} {float(r['수량']):.0f}주 @ {float(r['단가']):,.0f}원{memo_html}</span>
                </div>
                <div class="tx-right">{right_html}</div>
            </div>
            """, unsafe_allow_html=True)
