"""
한국 주식 포트폴리오 트래커 (Streamlit) — Meritz Orchestra
------------------------------------------------------------------
    streamlit run app.py

시세는 네이버 금융 비공식 공개 API를 사용합니다(종목코드 자동 검색 포함).
데이터 계층(로드/저장/replay/시세조회)은 portfolio_core.py에 있음 —
일일 매매일지 반영 스크립트(ingest_daily.py)와 로직을 공유하기 위해서다.

화면 렌더링은 탭별로 ui_portfolio_tab.py / ui_transactions_tab.py에 있음 —
이 파일은 페이지 설정, 로그인, 데이터 로드, 새로고침 버튼, 탭 조립만 담당한다.
"""

import streamlit as st

from constants import THEMES
from portfolio_core import (
    load_holdings, load_transactions, load_state,
    snapshot_history, snapshot_sector_history,
    refresh_all_prices, fetch_index_quotes,
    compute_metrics, compute_sector_weights,
)
from ui_portfolio_tab import render_portfolio_tab
from ui_transactions_tab import render_transactions_tab


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
    .stock-title-group {{ flex:1 1 auto; min-width:0; max-width:calc(100% - 195px); }}
    .stock-name {{ font-size:15px; font-weight:700; color:{T['text']}; }}
    .sector-tag {{ font-size:10.5px; padding:2px 7px; border-radius:5px; font-weight:600; flex-shrink:0; }}
    .stock-grid {{ display:grid; grid-template-columns: 0.7fr 1.05fr 1.05fr 1.3fr; gap:6px; margin-top:7px; }}
    .cell .top {{ font-size:12.5px; font-weight:700; color:{T['text']}; }}
    .cell .bottom {{ font-size:11px; color:{T['muted']}; margin-top:2px; }}
    .stock-foot {{ display:flex; justify-content:flex-end; margin-top:6px; font-size:10px; color:{T['muted2']}; }}
    .trade-summary {{ display:flex; flex-wrap:wrap; gap:3px 16px; font-size:11px; color:{T['muted']}; margin:10px 2px 6px; }}
    .trade-summary b {{ color:{T['text']}; font-weight:700; }}

    /* 보유종목 카드 우측상단 "WATERING" 칩(=매수/매도 내역·물타기 그래프 토글) —
       카드 밑에 항상 펼쳐진 버튼 줄을 두던 걸 없애고, 카드 내부에 얹는 방식으로 바꿈
       (2026-08-24). st.container(key=...)가 만들어주는 st-key-* 클래스로 CSS만으로
       버튼을 카드 우측상단(섹터태그 왼쪽)에 절대위치시킨다 — 카드 HTML 자체는 그대로 두고
       버튼만 그 위에 얹는 것이라, 카드 높이가 내용에 따라 달라져도 안 깨진다. */
    [class*="st-key-holding_wrap_"] {{ position:relative; }}
    [class*="st-key-watering_"] {{
        position:absolute; top:11px; right:108px; z-index:5; width:auto !important;
    }}
    [class*="st-key-watering_"] button {{
        padding:2px 7px !important; min-height:0 !important;
        height:auto !important; border-radius:5px !important;
        line-height:1.4 !important; border:none !important;
        box-shadow:none !important;
    }}
    [class*="st-key-watering_"] button p {{
        font-size:10.5px !important; font-weight:400 !important; line-height:1.4 !important;
    }}
    [class*="st-key-watering_"] button[kind="secondary"] {{
        background:{T['muted']}22 !important; color:{T['text']} !important;
    }}
    [class*="st-key-watering_"] button[kind="secondary"] p {{ color:{T['text']} !important; }}
    [class*="st-key-watering_"] button[kind="primary"] {{
        background:{T['muted']} !important; color:#fff !important;
    }}
    [class*="st-key-watering_"] button[kind="primary"] p {{ color:#fff !important; }}

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

# 두 탭이 동일한 holdings/cash로 각자 compute_metrics를 다시 부르던 걸 여기서 한 번만 계산해서 공유
df, stock_valuation, total_assets, unrealized_loss = compute_metrics(holdings, state["cash"])

tab_port, tab_tx = st.tabs(["포트폴리오", "거래 기록"])

with tab_port:
    render_portfolio_tab(holdings, state, tx, df, stock_valuation, total_assets, unrealized_loss, T)

with tab_tx:
    render_transactions_tab(state, tx, total_assets, unrealized_loss, T)
