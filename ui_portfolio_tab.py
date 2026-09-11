"""포트폴리오 탭 (요약 카드, 섹터 비중, Up/Down, 종목별 보유현황)."""

import matplotlib.pyplot as plt
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

from constants import UP_COLOR, DOWN_COLOR, NEW_COLOR, DIVIDEND_MID_COLOR, CASH_LABEL, SECTOR_PALETTE, SECTOR_TARGETS
from portfolio_core import (
    group_sector, today_kst_str, now_kst_str,
    load_sector_history, get_current_prices_for_names, get_closed_out_last_sells,
    compute_sector_weights, load_watchlist, refresh_watchlist_prices,
    get_watchlist_prev_day_ranks, load_dividend_cache,
    get_holding_trade_summary, get_holding_trade_summary_all_time,
    get_holding_trade_points, get_holding_avg_price_path,
    load_investor_flow_db, load_market_flow_db, load_watchlist_history_db,
    get_stock_price_history_db,
    compute_volume_flags, compute_foreign_flags, compute_market_flow_baseline,
    FLOW_BASIS_KEY, rank_flow_flags, get_flow_prev_day_ranks,
    compute_link_candidates, load_link_watch_log, link_watch_status, fetch_quotes,
    load_index_history, load_market_cache,
    load_history, compute_index_vs_account, load_bigcap_history, synthetic_kospi_ex_bigcap,
    load_claude_notes, seed_engine_series,
)

_CLAUDE_ORANGE = "#D97757"   # Claude 클레이 오렌지 — "Claude's Read" 마크·라벨·채운 별
_CLAUDE_MARK = ("<svg width='13' height='13' viewBox='0 0 24 24' style='vertical-align:-2px'>"
                "<g fill='#D97757'>"
                "<path d='M12 2l1.6 6.1L19 5.6l-3.1 4.9L22 12l-6.1 1.6L18.4 19l-4.9-3.1L12 22l-1.6-6.1L5 18.4l3.1-4.9L2 12l6.1-1.6L5.6 5z'/>"
                "</g></svg>")


def _claude_read_html(T: dict) -> str:
    """포트폴리오 요약카드 Today's Take 밑에 붙는 'Claude's Read' 블록(§6-22). 네이티브
    <details>라 클릭 시 rerun 없음. **오늘(마지막) 코멘트만** 보여준다 — 지난 것 붙이지 않음
    (2026-09-09 사용자 요청). claude_daily_notes.csv 비어있으면 빈 문자열."""
    notes = load_claude_notes()
    if notes.empty:
        return ""

    def _stars(n):
        n = max(0, min(5, int(n)))
        return (f"<span style='color:{_CLAUDE_ORANGE}'>{'★' * n}</span>"
                f"<span style='color:{T['muted2']}'>{'☆' * (5 - n)}</span>")

    def _body(txt):
        return str(txt).replace("\n", "<br>")

    cur = notes.iloc[-1]
    _sum = (f"list-style:none;cursor:pointer;font-size:13px;color:{_CLAUDE_ORANGE};"
            f"font-weight:600;display:flex;align-items:center;gap:6px")
    return (
        f"<details style='border-top:1px solid {T['border']};margin-top:10px;padding-top:9px'>"
        f"<summary style=\"{_sum}\">{_CLAUDE_MARK}<span>Claude's Read</span>"
        f"<span style='font-size:12px;letter-spacing:1px'>{_stars(cur['별점'])}</span>"
        f"<span style='font-size:11px;color:{T['muted']};font-weight:400;margin-left:auto'>"
        f"{str(cur['날짜'])[5:]}</span></summary>"
        f"<div style='font-size:12px;color:{T['text']};line-height:1.65;margin:8px 2px 4px'>"
        f"{_body(cur['코멘트'])}</div></details>"
    )


def _rank_delta_html(prev_rank, cur_rank) -> str:
    """전일 대비 순위 변동 배지 HTML — Fishing/Volume/Foreigner 공용.
    prev_rank None(어제 목록에 없었음) → NEW, 그 외 ▲N/▼N/- ."""
    if prev_rank is None:
        return f'<span class="rank-delta" style="color:{NEW_COLOR}">NEW</span>'
    delta = prev_rank - cur_rank
    if delta == 0:
        return '<span class="rank-delta">-</span>'
    color = UP_COLOR if delta > 0 else DOWN_COLOR
    arrow = "▲" if delta > 0 else "▼"
    return f'<span class="rank-delta" style="color:{color}">{arrow}{abs(delta)}</span>'


def _dividend_badge_html(code: str, dividend_cache: dict, show_period: bool = True) -> str:
    """배당수익률 배지 — 보유종목 카드와 Fishing 관심종목 줄에서 공유해서 쓴다(2026-09-01).
    색깔 구간(사용자 지정): 5% 초과 빨강, 3~5% 진한 녹색, 1~3% 검정, 1% 미만 파랑.
    괄호 안 날짜는 실제 배당락일이 아니라 네이버가 배당수익률 계산에 쓴 결산연월(예:
    "2025.12")이다 — 이 페이지엔 정확한 배당락일이 없어서 구할 수 있는 것 중 가장
    가까운 값을 대신 쓰기로 함(사용자 확인, 2026-09-01).

    show_period=False: Fishing 관심종목 줄은 한 줄짜리 리스트라 이 괄호까지 붙이면
    종목명이 잘리는 문제가 있어(2026-09-01 실제 확인) 퍼센트만 보여준다 — 보유종목
    카드는 배당 배지가 종목명 아래 전용 줄에 있어 자리가 넉넉하므로 계속 표시."""
    entry = dividend_cache.get(code)
    if not entry:
        return ""
    dv = entry["배당수익률"]
    if dv > 5:
        dvc = UP_COLOR
    elif dv >= 3:
        dvc = DIVIDEND_MID_COLOR
    elif dv >= 1:
        dvc = "#000000"
    else:
        dvc = DOWN_COLOR
    period_txt = ""
    if show_period:
        period = entry.get("배당기준월", "")
        period_txt = f" ({period})" if period else ""
    return f'<span class="dividend-tag" style="color:{dvc}">{dv:.1f}%{period_txt}</span>'


def _render_holding_detail(r: dict, tx: pd.DataFrame, T: dict):
    """보유종목 카드를 눌렀을 때 펼쳐지는 상세 — 매수/매도 요약 + "물타기 적정성" 그래프.
    "현재가" 선은 Supabase price_history(§6-9/§6-16)에서 그 종목의 최초매입일~오늘 구간 일별
    종가를 가져와 실제 등락 그대로 그린다(2026-09-11 갱신 — 예전엔 최초매입일→오늘 두 점을
    직선으로 이었는데, 그 사이 진짜로 오르내린 걸 사용자가 "꾸준히 내려온 것처럼 보인다"고
    지적함). DB에 그 구간 데이터가 없으면(아직 watchlist에 편입 안 된 신규 종목 등) 예전처럼
    두 점 직선으로 폴백 — 네이버를 그때그때 낱개로 조회하지 않고 DB만 조회한다. 그 위에 실제
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

    # "현재가" 선의 실제 데이터 — Supabase price_history에서 이 종목코드의 일별 종가를 세션당
    # 1회만 조회(카드를 열어둔 채 다른 위젯을 눌러 rerun돼도 재조회 안 함). 없으면 빈 DF.
    code = r["종목코드"]
    _hist_cache = st.session_state.setdefault("holding_price_hist_cache", {})
    if code not in _hist_cache:
        _sb = st.secrets.get("supabase", {})
        _hist_cache[code] = get_stock_price_history_db(code, _sb.get("url", ""), _sb.get("anon_key", ""))
    _price_hist = _hist_cache[code]
    if not _price_hist.empty:
        _ph = _price_hist[(_price_hist["날짜"] >= entry_date) & (_price_hist["날짜"] <= today)]
    else:
        _ph = _price_hist
    if len(_ph) >= 2:
        cur_x = list(_ph["날짜"])
        cur_y = [float(v) for v in _ph["종가"]]
        if cur_x[0] != entry_date:
            cur_x, cur_y = [entry_date] + cur_x, [entry_price] + cur_y
        else:
            cur_y[0] = entry_price  # 실제 체결가로 첫 점 고정(DB 종가와 살짝 다를 수 있음)
        if cur_x[-1] != today:
            cur_x, cur_y = cur_x + [today], cur_y + [current_price]
        else:
            cur_y[-1] = current_price  # 실시간가로 마지막 점 고정(DB는 전날 종가까지일 수 있음)
    else:
        cur_x, cur_y = [entry_date, today], [entry_price, current_price]

    # x축 눈금: 매수가 한 건이고 진입일이 오늘과 하루 이내면 plotly가 날짜축을 "하루 미만"
    # 범위로 보고 23:59:59.999 같은 시:분:초 눈금을 찍어버린다. 항상 날짜 눈금만 나오도록
    # dtick을 '며칠 단위'로 고정하고, 범위를 살짝 넓혀 눈금이 2~4개 찍히게 한다.
    _xs = pd.to_datetime(
        [entry_date, today] + avg_x + list(buys["날짜"])
        + (list(sells["날짜"]) if not sells.empty else []), errors="coerce")
    _xs = _xs.dropna()
    _xmin, _xmax = _xs.min(), _xs.max()
    _span = max(int((_xmax - _xmin).days), 1)
    _pad = pd.Timedelta(max(1, int(round(_span * 0.08))), "D")
    _dtick_ms = max(1, int(round(_span / 4))) * 86_400_000
    _xrange = [(_xmin - _pad).strftime("%Y-%m-%d"), (_xmax + _pad).strftime("%Y-%m-%d")]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=cur_x, y=cur_y, mode="lines",
        name="현재가", line=dict(color=T["muted"], width=1.8),
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
        xaxis=dict(showgrid=False, tickfont=dict(size=9, color=T["muted"]), fixedrange=True,
                   type="date", tickformat="%m/%d", dtick=_dtick_ms, range=_xrange),
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


# "Link"(연결고리) 패널 잠금 비밀번호 (구 포리너 프로젝트 패널과 동일 게이트 재사용,
# 사용자 지정 2026-09-03). 개인용 앱의 소프트 게이트 — check_password()의 앱 전체 로그인과
# 별개로 이 실험 패널만 추가로 가림.
LINK_PASSWORD = "653715"


def _render_link_panel(ph, fh, live_quotes, refresh_fn, T):
    """"Link" 패널(§포프 다음 단계, 2026-09-11) — 가격·외인비중이 반대 방향으로 크게 벌어진
    "엉뚱한 놈"만 골라 감시목록에 올리고 시간을 두고 지켜보는 실험 패널.
    **Volume/Foreigner와 데이터를 완전히 공유한다**(2026-09-11 갱신 — 사용자 지적: "결국
    데이터의 뿌리는 같아야 하고, 포리너의 데이터와 피싱의 데이터가 기반이 되어야 한다"). 그래서
    이 함수는 자체 DB 조회를 하지 않고, `render_portfolio_tab`이 Foreigner 섹션에서 이미 로드한
    `flow_hist`/`price_hist_flow`/`live_quotes`(session_state 공유)를 그대로 받는다 — Foreigner든
    Link든 어느 쪽 "새로고침"을 눌러도 **같은 `_refresh_flow_data()` 한 함수, 같은 DB 호출**을
    타므로 세 패널이 서로 어긋날 일이 없다. 외인 쪽 계산도 `compute_link_candidates` 내부에서
    `compute_foreign_flags`를 그대로 재사용(§6-28) — Foreigner에 뜨는 "기준일pp"와 Link의 dF는
    항상 같은 값이다.
    **감시목록(link_watch_log.csv) 편입은 이 UI에서 직접 하지 않는다** — §1-7대로 이 앱은
    CSV 반영이 유일한 데이터 입력 경로라, "이 종목 감시목록에 넣어줘"라고 채팅으로 요청하면
    세션이 add_link_watch_entry()를 스크립트로 실행하고 git commit한다(배포 서버 로컬 디스크에만
    쓰면 재배포 때 사라짐, §1-5). 이 패널은 감시목록 현황 + 후보 랭킹을 읽기 전용으로 보여줄 뿐."""
    st.caption("가격은 빠지는데 외인비중은 늘어난 종목만 골라 지켜보는 실험 패널(반대 방향, "
               "가격↑+외인↓인 흔한 차익실현은 후보에서 뺌). '외인이 사면 오른다'는 상관관계를 "
               "보려는 게 아니라, 이 예외 케이스를 찾아 한 달쯤 지켜보는 용도. Foreigner·Volume과 "
               "데이터·기준일을 그대로 공유함 — 아래서 새로고침해도, 위 Foreigner/Volume에서 "
               "새로고침해도 결과는 같다.")
    if st.button("새로고침", key="link_refresh"):
        with st.spinner("데이터 조회 중..."):
            refresh_fn()
        st.rerun()

    if ph is None or fh is None:
        st.caption("새로고침을 눌러 가격·외인 데이터를 불러오세요.")
        return
    lq = live_quotes or {}

    watch = load_link_watch_log()
    if not watch.empty:
        status = link_watch_status(watch, ph, fh, live_quotes=lq)
        st.markdown(f"<div style='font-size:12px;color:{T['muted']};font-weight:600;margin:4px 0 2px'>지켜보는 중</div>",
                    unsafe_allow_html=True)
        rows = ""
        for _, r in status.sort_values("플래그일", ascending=False).iterrows():
            cp, cf = r.get("가격변화"), r.get("외인변화")
            cp_s = f"{cp:+.1f}%" if cp is not None else "—"
            cf_s = f"{cf:+.2f}%p" if cf is not None else "—"
            cp_c = UP_COLOR if (cp or 0) >= 0 else DOWN_COLOR
            cf_c = UP_COLOR if (cf or 0) >= 0 else DOWN_COLOR
            days = r.get("경과일")
            days_s = f"{int(days)}일째" if days is not None else "—"
            rows += (f'<div class="updown-row flow-row"><span class="name">{r["종목명"]}</span>'
                     f'<span class="detail" style="color:{T["muted"]}">{r["기준일"]}~{days_s}</span>'
                     f'<span class="pct" style="color:{cp_c}">{cp_s}</span>'
                     f'<span class="pct" style="color:{cf_c}">{cf_s}</span></div>')
        st.markdown(rows, unsafe_allow_html=True)
        st.caption("기준일 이후 가격변화% · 외인비중변화%p (기준가/기준비중 = 플래그 시점에 기록된 고정값)")

    cands = compute_link_candidates(ph, fh, live_quotes=lq)
    if cands.empty:
        st.caption("아직 후보를 계산할 데이터가 부족합니다.")
        return
    watched_codes = set(watch["종목코드"]) if not watch.empty else set()

    def _quad_rows(df, n):
        parts = ""
        for _, r in df.head(n).iterrows():
            p_c = UP_COLOR if r["P"] >= 0 else DOWN_COLOR
            f_c = UP_COLOR if r["dF"] >= 0 else DOWN_COLOR
            mark = " ★" if r["종목코드"] in watched_codes else ""
            parts += (f'<div class="updown-row flow-row"><span class="name">{r["종목명"]}{mark}</span>'
                      f'<span class="pct" style="color:{p_c}">{r["P"]:+.1f}%</span>'
                      f'<span class="pct" style="color:{f_c}">{r["dF"]:+.2f}%p</span>'
                      f'<span class="detail" style="color:{T["muted"]}">score {r["score"]:.1f}</span></div>')
        return parts

    # 4분면(§6-28) 중 "다이버전스"(최우선)·"진행형"(차선)만 화면에 보여줌 — "이탈"/"차익실현"은
    # 매수 후보 성격이 아니라서(물타기 참고·관심 밖) 이 목록엔 안 띄움, compute_link_candidates
    # 결과에는 계속 남아있어 나중에 §6-10 WATERING 연동 등으로 쓸 수 있음.
    div = cands[cands["구간"] == "다이버전스"]
    prog = cands[cands["구간"] == "진행형"]

    st.markdown(f"<div style='font-size:12px;color:{T['muted']};font-weight:600;margin:10px 0 2px'>"
                f"다이버전스 — 가격↓ 외인↑, 곧 뭔가 일어난다</div>", unsafe_allow_html=True)
    if div.empty:
        st.caption("해당 없음")
    else:
        st.markdown(_quad_rows(div, 8), unsafe_allow_html=True)

    st.markdown(f"<div style='font-size:12px;color:{T['muted']};font-weight:600;margin:10px 0 2px'>"
                f"진행형 — 가격↑ 외인↑, 아직 덜 먹었다</div>", unsafe_allow_html=True)
    if prog.empty:
        st.caption("해당 없음")
    else:
        st.markdown(_quad_rows(prog, 5), unsafe_allow_html=True)

    st.caption(f"기준일({cands.iloc[0]['기준일']} 등, 종목별로 다름) 이후 가격변화% · "
               "외인비중변화%p · score=−(외인비중변화×가격변화). ★=이미 감시 중")


def render_portfolio_tab(holdings, state, tx, df, stock_valuation, total_assets, unrealized_loss, T):
    total_cost = df["매입금액"].sum()
    stock_profit = stock_valuation - total_cost
    stock_profit_pct = (stock_profit / total_cost * 100) if total_cost else 0

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
    daily_color = UP_COLOR if daily_pnl > 0 else (DOWN_COLOR if daily_pnl < 0 else T["muted"])
    daily_sign = "+" if daily_pnl > 0 else ""

    # 어제 대비 포트폴리오 총자산 변화 (직전 asset_history 스냅샷 대비). 이 화면은 포트폴리오
    # 현황용이라 "최초 자본 대비 누적손익"(그건 거래 기록 탭에도 나옴) 대신 전일 대비를 보여준다.
    # 오늘 실현한 이익도 총자산에 이미 반영돼 있으므로 자동으로 +로 잡힌다 —
    # "어제 대비 1만원 이익이 나서 매도했다"도 당일 +1만원으로 정당하게 평가됨 (사용자 요청).
    _hist = load_history()
    _prev = _hist[_hist["날짜"].astype(str) < today_str] if not _hist.empty else _hist
    prev_total = float(_prev["총자산"].iloc[-1]) if not _prev.empty else state["initial"]
    day_change = total_assets - prev_total
    day_color = UP_COLOR if day_change > 0 else (DOWN_COLOR if day_change < 0 else T["muted"])
    day_sign = "+" if day_change > 0 else ""

    # ---- Today's Take: 오늘 내 주식 성과 vs 시장(혼합지수·DC/UC), 기본 + 반도체 제외(W/O SH) ----
    # 원(내 주식 어제 대비)은 위 day_change 그대로, %는 Account:Index의 "내 주식 당일"(Rs).
    # 혼합지수 당일 / DC(하락일 c)·UC(상승일 c)·—(even) 를 기본 벤치와 삼성·하이닉스 제외 벤치
    # 두 가지로 보여준다. compute_index_vs_account를 여기서 2번 호출(가벼움).
    _idx_h = load_index_history()
    _asset_h = load_history()
    _mc = load_market_cache()
    _hv = holdings.copy()
    _hv["_v"] = (pd.to_numeric(_hv["수량"], errors="coerce").fillna(0)
                 * pd.to_numeric(_hv["현재가"], errors="coerce").fillna(0))
    _hv["_m"] = _hv["종목명"].map(_mc)
    _ksv = float(_hv.loc[_hv["_m"] == "KOSPI", "_v"].sum())
    _kqv = float(_hv.loc[_hv["_m"] == "KOSDAQ", "_v"].sum())
    _wk = _ksv / (_ksv + _kqv) if (_ksv + _kqv) > 0 else None
    _fr = state.get("fee_rate", 0.0)
    _iva_m = compute_index_vs_account(tx, _asset_h, _idx_h, state["initial"], _fr, kospi_weight=_wk)
    _bg_h = load_bigcap_history()
    _iva_s = (compute_index_vs_account(tx, _asset_h, synthetic_kospi_ex_bigcap(_idx_h, _bg_h),
                                       state["initial"], _fr, kospi_weight=_wk)
              if not _bg_h.empty else None)

    def _tt_dcuc(iva):
        sm = (iva or {}).get("cap", {}).get("stock", {})
        b, v = sm.get("today_bucket"), sm.get("today")
        if b == "하락" and v is not None:
            return f"DC {v:.2f}", DOWN_COLOR   # 하락일 방어 = 파랑
        if b == "상승" and v is not None:
            return f"UC {v:.2f}", UP_COLOR     # 상승일 참여 = 빨강
        return "—", T["muted"]

    def _tt_p(v):
        return "—" if v is None else f"{v * 100:+.2f}%"

    _stk_day = (_iva_m.get("latest", {}).get("주식") or (None, None))[1]
    _bench_m = (_iva_m.get("latest", {}).get("벤치") or (None, None))[1]
    _bench_s = ((_iva_s.get("latest", {}).get("벤치") if _iva_s else None) or (None, None))[1]
    _dc_m, _dc_m_c = _tt_dcuc(_iva_m)
    _dc_s, _dc_s_c = _tt_dcuc(_iva_s)
    _stk_c = UP_COLOR if (_stk_day or 0) > 0 else (DOWN_COLOR if (_stk_day or 0) < 0 else T["muted"])
    _tt_arrow = "▲" if day_change > 0 else ("▼" if day_change < 0 else "·")

    # ---- 오늘의 거래 요약 (매수/매도 총금액) ----
    buy_tx = today_tx[today_tx["구분"] == "매수"].copy()
    sell_tx = today_tx[today_tx["구분"] == "매도"].copy()
    buy_total_amt = (pd.to_numeric(buy_tx["수량"], errors="coerce") * pd.to_numeric(buy_tx["단가"], errors="coerce")).sum()
    sell_total_amt = (pd.to_numeric(sell_tx["수량"], errors="coerce") * pd.to_numeric(sell_tx["단가"], errors="coerce")).sum()
    total_trade_count = len(today_tx)

    daily_trade_html = f"""
    <div class="daily-trade-box">
        <div class="daily-trade-count">일일거래 총 {total_trade_count}회
            <span>(매수 {len(buy_tx)}건 · 매도 {len(sell_tx)}건)</span></div>
        <div style="font-size:12px;color:{T['muted']};margin-top:2px">
            <span style="color:{UP_COLOR}">매수</span> <b>{buy_total_amt:,.0f}원</b>
            &nbsp;&nbsp;·&nbsp;&nbsp;
            <span style="color:{DOWN_COLOR}">매도</span> <b>{sell_total_amt:,.0f}원</b></div>
    </div>
    """

    # ---- 요약 카드: 평가손익+그리드 (A) → Seed Engine 토글(§6-27, Claude's Read식 작은 글씨) →
    #      Today's Take + Claude's Read (B). 세 조각을 한 container로 묶고 CSS로 틈을 없애 카드 하나처럼. ----
    with st.container(key="summary_card_wrap"):
        st.markdown(f"""
        <div class="summary-box sc-top">
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
        </div>
        """, unsafe_allow_html=True)

        # ---- Pit Stop (§6-27, 구 Seed Engine): 빨강 Cost Basis↑ · 녹색 Refill(예수금, 씨앗이 채운
        #      것) 평행 · 파랑 No Refill(씨앗 없었으면 남았을 현금). 녹−파 간격 = 씨앗이 쌓아준 연료.
        #      진노랑 Surplus(우측 % 축, ±30 고정, +빨강/−파랑) = (Refill÷No Refill − 1)×100. ----
        with st.container(key="seed_engine_wrap"):
            with st.expander("⛽ Pit Stop", expanded=False):
                _se = seed_engine_series(tx, state["initial"], state.get("fee_rate", 0.0), load_history())
                if len(_se) < 2:
                    st.caption("거래가 쌓이면 궤적이 그려집니다.")
                else:
                    _ta = _se["총자산"].replace(0, pd.NA)

                    def _rat(col):
                        return (_se[col] / _ta * 100).fillna(0).tolist()

                    _FUEL_C = "#c99a00"  # 진한 노란색
                    # Surplus = Refill÷No Refill − 1 (%). 손절 많으면 음수 가능. No Refill≈0이면 발산
                    # → 300% 소프트캡(축에서 잘림). 우측 축은 0% 중앙, ±30 고정(peak가 25 넘으면 ±40).
                    _sp = [min((wf / wof - 1.0) * 100.0, 300.0) if wof > 1e-9 else 300.0
                           for wf, wof in zip(_se["예수금"], _se["무연료예수금"])]
                    _peak = max((abs(v) for v in _sp if abs(v) < 150), default=16.0)
                    _mb = 30.0 if _peak <= 25.0 else (int(_peak * 1.5 / 10) + 1) * 10.0
                    _tv = list(range(-int(_mb), int(_mb) + 1, 10))
                    _tt = [(f"<span style='color:{UP_COLOR}'>+{v}%</span>" if v > 0
                            else f"<span style='color:{DOWN_COLOR}'>{v}%</span>" if v < 0
                            else "<span style='color:%s'>0%%</span>" % T["muted2"]) for v in _tv]

                    def _sp_txt(d):
                        c = UP_COLOR if d >= 0 else DOWN_COLOR
                        return f"<span style='color:{c}'>{'+' if d >= 0 else ''}{d:.0f}%</span>"

                    fig_se = go.Figure()
                    fig_se.add_trace(go.Scatter(
                        x=_se["날짜"], y=_se["총매입"], name="Cost Basis", mode="lines",
                        line=dict(color=UP_COLOR, width=2), customdata=_rat("총매입"),
                        hovertemplate="Cost Basis %{y:,.0f}원 (%{customdata:.0f}%)<extra></extra>"))
                    fig_se.add_trace(go.Scatter(
                        x=_se["날짜"], y=_se["예수금"], name="Refill", mode="lines",
                        line=dict(color=NEW_COLOR, width=2), customdata=_rat("예수금"),
                        hovertemplate="Refill %{y:,.0f}원 (%{customdata:.0f}%)<extra></extra>"))
                    fig_se.add_trace(go.Scatter(
                        x=_se["날짜"], y=_se["무연료예수금"], name="No Refill", mode="lines",
                        line=dict(color=DOWN_COLOR, width=1.8), customdata=_rat("무연료예수금"),
                        hovertemplate="No Refill %{y:,.0f}원 (%{customdata:.0f}%)<extra></extra>"))
                    fig_se.add_trace(go.Scatter(
                        x=_se["날짜"], y=_sp, name="Surplus", mode="lines", yaxis="y2",
                        line=dict(color=_FUEL_C, width=1.6),
                        customdata=[_sp_txt(v) for v in _sp],
                        hovertemplate="Surplus %{customdata} 더 있음<extra></extra>"))
                    fig_se.update_layout(
                        height=260, margin=dict(l=30, r=36, t=8, b=26),
                        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                        font=dict(color=T["text"], size=11), showlegend=False, hovermode="x unified",
                        hoverlabel=dict(bgcolor=T["card"], bordercolor=T["border"], font=dict(size=11, color=T["text"])),
                        xaxis=dict(showgrid=False, tickfont=dict(size=9, color=T["muted"]), fixedrange=True),
                        yaxis=dict(showgrid=True, gridcolor=T["border"], zeroline=False, tickformat="~s",
                                   tickfont=dict(size=9, color=T["muted"]), fixedrange=True),
                        yaxis2=dict(overlaying="y", side="right", showgrid=False, range=[-_mb, _mb],
                                    tickmode="array", tickvals=_tv, ticktext=_tt,
                                    zeroline=True, zerolinecolor=T["muted2"], zerolinewidth=1,
                                    tickfont=dict(size=9), fixedrange=True),
                        dragmode=False,
                    )
                    # expander 안 st.plotly_chart 폭 0(§6-17) → iframe + responsive
                    components.html(
                        "<style>body{margin:0;background:transparent}</style>"
                        + fig_se.to_html(include_plotlyjs="cdn", full_html=False, default_width="100%",
                                         config={"displayModeBar": False, "responsive": True}),
                        height=272,
                    )

        st.markdown(f"""
        <div class="summary-box sc-bot">
        <div class="capital-line" style="line-height:1.75">
            <div style="font-size:13px;color:{T['text']};font-weight:600;margin-bottom:2px">Today's Take</div>
            <div>내 주식 어제 대비&nbsp;
                <b style="color:{day_color}">{day_sign}{day_change:,.0f}원</b>
                <span style="color:{_stk_c}">&nbsp;{_tt_arrow} {_tt_p(_stk_day)}</span></div>
            <div style="color:{T['muted']}">혼합지수&nbsp;<b style="color:{T['text']}">{_tt_p(_bench_m)}</b>
                &nbsp;·&nbsp;W/O SH&nbsp;<b style="color:{T['text']}">{_tt_p(_bench_s)}</b></div>
            <div style="color:{T['muted']}"><b style="color:{_dc_m_c}">{_dc_m}</b>
                &nbsp;·&nbsp;W/O SH&nbsp;<b style="color:{_dc_s_c}">{_dc_s}</b></div>
        </div>
        {_claude_read_html(T)}
        {daily_trade_html}
    </div>
    """, unsafe_allow_html=True)

    # ---- 섹터별 색상: 파이차트/막대/종목카드 태그가 전부 같은 배정을 쓰도록 여기서 한 번만 계산 ----
    # (주식 총자산 대비 비중 기준 순위로 고정 배정 — 예전에는 이 매핑이 두 벌 따로 있어서
    #  종목카드 섹터 태그 색이 파이차트/막대와 다르게 나오는 경우가 있었음)
    stock_weights = compute_sector_weights(df)  # {섹터그룹: 주식 총자산 대비 %}
    # 비중 큰 순 정렬하되 "기타2"(153 밖 미분류 묶음, §6-24)는 비중과 무관하게 항상 맨 밑
    stock_weight_rank = sorted(stock_weights.items(), key=lambda x: (x[0] == "기타2", -x[1]))
    color_map = {name: SECTOR_PALETTE[i % len(SECTOR_PALETTE)] for i, (name, _) in enumerate(stock_weight_rank)}

    # ---- 섹터 비중 도넛 + 목표 비중 관리 ----
    with st.expander("Sectors", expanded=False):
        include_cash = st.toggle("예수금 포함", value=st.session_state.get("include_cash", True), key="cash_toggle")
        st.session_state["include_cash"] = include_cash

        df_grp = df.copy()
        df_grp["섹터그룹"] = df_grp["섹터"].apply(group_sector)
        sector_val = df_grp.groupby("섹터그룹")["평가금액"].sum().to_dict()
        if include_cash and state["cash"] > 0:
            sector_val[CASH_LABEL] = state["cash"]
        sector_items = sorted(sector_val.items(), key=lambda x: (x[0] == "기타2", -x[1]))  # 기타2는 맨 밑
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
            # DOWN은 ±2%, UP은 ±3% (2026-09-10 사용자 요청 — 하락 쪽을 더 촘촘히)
            down_threshold, up_threshold = 2.0, 3.0
            if updown_mode == "DOWN":
                filtered = sorted([r for r in updown_results if r["pct"] <= -down_threshold], key=lambda r: r["pct"])
                updown_color = DOWN_COLOR
            else:
                filtered = sorted([r for r in updown_results if r["pct"] >= up_threshold], key=lambda r: -r["pct"])
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
                    hist_df = load_watchlist_history_db(sb_url, sb_key)
                st.session_state["fishing_prices"] = prices_df
                st.session_state["fishing_hist"] = hist_df
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
                    hist_df = st.session_state.get("fishing_hist", pd.DataFrame())
                    prev_ranks = get_watchlist_prev_day_ranks(
                        hist_df, fishing_basis, fishing_dir, FISHING_THRESHOLD, today_kst_str())

                    # Up/Down(청산 종목 추적)에도 걸린 종목은 Fishing에서 종목명을 파랑으로
                    # (2026-09-10 사용자 요청). Up/Down 새로고침을 눌러 결과가 있을 때만.
                    _ud = st.session_state.get("updown_results") or []
                    _ud_names = {r["종목명"] for r in _ud
                                 if r["pct"] <= -2.0 or r["pct"] >= 3.0}

                    row_parts = []
                    for i, f in enumerate(flagged, 1):
                        _name_style = f' style="color:{DOWN_COLOR}"' if f["종목명"] in _ud_names else ""
                        prev_rank = prev_ranks.get(f["종목명"])
                        if prev_rank is None:
                            rank_delta_html = f'<span class="rank-delta" style="color:{NEW_COLOR}">NEW</span>'
                        else:
                            delta = prev_rank - i
                            if delta == 0:
                                rank_delta_html = '<span class="rank-delta">-</span>'
                            else:
                                color = UP_COLOR if delta > 0 else DOWN_COLOR
                                arrow = "▲" if delta > 0 else "▼"
                                rank_delta_html = f'<span class="rank-delta" style="color:{color}">{arrow}{abs(delta)}</span>'
                        row_parts.append(
                            f'<div class="updown-row"><span class="rank">{i}</span>{rank_delta_html}'
                            f'<span class="name"{_name_style}>{f["종목명"]}</span>'
                            f'<span class="pct" style="color:{UP_COLOR if f["pct_origin"] >= 0 else DOWN_COLOR}">'
                            f'{"+" if f["pct_origin"] >= 0 else ""}{f["pct_origin"]:.1f}%</span>'
                            f'<span class="pct" style="color:{UP_COLOR if f["pct_ref"] >= 0 else DOWN_COLOR}">'
                            f'{"+" if f["pct_ref"] >= 0 else ""}{f["pct_ref"]:.1f}%</span></div>'
                        )
                    st.markdown("".join(row_parts), unsafe_allow_html=True)

    # ---- Bench: 한 번도 매수한 적 없는 관심종목 (소외종목) ----
    # watchlist 중 transactions에 매수 이력이 0인 종목만. Fishing과 같은 행 포맷이지만
    # ±3% 필터·순위변동 배지는 없다(전부 표시). 정렬: 매수횟수 asc(지금은 전부 0이라 사실상
    # 무의미) → 선택 기준(누적/전일)·방향(DOWN/UP) → 현재가 desc("단가 높은 순" 타이브레이크,
    # 화면엔 안 보임). 미보유 종목이 50개 이하로 줄면 상위 30개만 — 그때 모집단을 watchlist
    # 전체로 넓힐지는 재논의(2026-09-09, 사용자가 "q6는 나중" 이라고 보류). Fishing의
    # 새로고침이 채워둔 st.session_state["fishing_prices"]를 그대로 재사용(별도 조회 안 함).
    with st.expander("Bench", expanded=False):
        bench_prices = st.session_state.get("fishing_prices", pd.DataFrame())
        if bench_prices.empty:
            st.caption("Fishing에서 새로고침을 먼저 눌러주세요.")
        else:
            wl_names = load_watchlist()["종목명"].tolist()
            ever_bought = set(tx.loc[tx["구분"] == "매수", "종목명"])
            buy_counts = tx[tx["구분"] == "매수"].groupby("종목명").size().to_dict()
            never = [n for n in wl_names if n not in ever_bought]
            never_set = set(never)

            bench_rows = []
            for _, r in bench_prices.iterrows():
                if r["종목명"] not in never_set:
                    continue
                try:
                    origin, last, pct_ref = float(r["최초가"]), float(r["최근가"]), float(r["전일대비"])
                except (TypeError, ValueError):
                    continue
                pct_origin = (last - origin) / origin * 100 if origin else 0.0
                bench_rows.append({"종목명": r["종목명"], "현재가": last, "pct_ref": pct_ref,
                                   "pct_origin": pct_origin, "buy_count": buy_counts.get(r["종목명"], 0)})

            st.caption(f"관심종목 {len(wl_names)}개 중 한 번도 매수 안 한 종목 {len(never)}개")

            bc1, bc2 = st.columns(2)
            with bc1:
                bench_basis = st.radio("기준", ["누적", "전일"], horizontal=True,
                                       label_visibility="collapsed", key="bench_basis")
            with bc2:
                bench_dir = st.radio("방향", ["DOWN", "UP"], horizontal=True,
                                     label_visibility="collapsed", key="bench_dir")

            bench_metric = "pct_origin" if bench_basis == "누적" else "pct_ref"
            bench_rows.sort(key=lambda x: (
                x["buy_count"],
                -x[bench_metric] if bench_dir == "UP" else x[bench_metric],
                -x["현재가"],
            ))
            bench_shown = bench_rows if len(never) > 50 else bench_rows[:30]

            if not bench_shown:
                st.caption("표시할 종목이 없습니다.")
            else:
                bench_parts = []
                for i, f in enumerate(bench_shown, 1):
                    bench_parts.append(
                        f'<div class="updown-row"><span class="rank">{i}</span>'
                        f'<span class="name">{f["종목명"]}</span>'
                        f'<span class="pct" style="color:{UP_COLOR if f["pct_origin"] >= 0 else DOWN_COLOR}">'
                        f'{"+" if f["pct_origin"] >= 0 else ""}{f["pct_origin"]:.1f}%</span>'
                        f'<span class="pct" style="color:{UP_COLOR if f["pct_ref"] >= 0 else DOWN_COLOR}">'
                        f'{"+" if f["pct_ref"] >= 0 else ""}{f["pct_ref"]:.1f}%</span></div>'
                    )
                st.markdown("".join(bench_parts), unsafe_allow_html=True)

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
        _ph = load_watchlist_history_db(sb_url, sb_key)
        st.session_state["price_hist_flow"] = _ph
        # Link(§6-28)도 이 새로고침을 그대로 씀 — Foreigner/Volume/Link가 전부 같은 DB 호출
        # 하나를 공유해야 "같은 기준일이면 흔들림이 없다"는 전제가 실제로 성립한다(사용자 지적,
        # 2026-09-11). 현재가는 장마감 전엔 DB가 하루 뒤처지므로 실시간 시세도 같이 받아둔다.
        _codes = _ph["종목코드"].unique().tolist() if not _ph.empty else []
        _quotes, _ = fetch_quotes(_codes) if _codes else ({}, [])
        st.session_state["live_quotes"] = {c: q["price"] for c, q in _quotes.items()
                                            if q and q.get("price") is not None}

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
                vb1, vb2 = st.columns(2)
                with vb1:
                    vol_basis = st.radio("기준", ["누적", "전일"], horizontal=True,
                                          label_visibility="collapsed", key="vol_basis")
                with vb2:
                    vol_dir = st.radio("방향", ["DOWN", "UP"], horizontal=True,
                                        label_visibility="collapsed", key="vol_dir")
                bkey = FLOW_BASIS_KEY["volume"][vol_basis]
                ranked = rank_flow_flags(vol_flags, bkey, vol_dir)
                prev_ranks = get_flow_prev_day_ranks(flow_hist, "volume", vol_basis, vol_dir,
                                                      today_kst_str())
                if not ranked:
                    st.caption(f"{vol_basis} 기준 {vol_dir}으로 움직인 종목이 없습니다.")
                else:
                    parts = []
                    for i, r in enumerate(ranked[:15], 1):
                        v = r[bkey]
                        chg = r["오늘등락률"]
                        chg_s = (f'{"+" if chg >= 0 else ""}{chg:.1f}%') if chg is not None else "-"
                        parts.append(
                            f'<div class="updown-row flow-row"><span class="rank">{i}</span>'
                            f'{_rank_delta_html(prev_ranks.get(r["종목명"]), i)}'
                            f'<span class="name">{r["종목명"]}</span>'
                            f'<span class="pct" style="color:{UP_COLOR if v >= 0 else DOWN_COLOR}">'
                            f'{"+" if v >= 0 else ""}{v:.0f}%</span>'
                            f'<span class="detail">주가 {chg_s}</span></div>'
                        )
                    st.markdown("".join(parts), unsafe_allow_html=True)
                    st.caption(f"{vol_basis}({'평균 대비' if vol_basis == '누적' else '어제 대비'}) "
                               f"거래량 {vol_dir} 큰 순 · 상위 15개 표시 "
                               f"(추적 {len(vol_flags)}종목 중 {vol_dir} {len(ranked)}종목)")

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
                fb1, fb2 = st.columns(2)
                with fb1:
                    fx_basis = st.radio("기준", ["누적", "전일"], horizontal=True,
                                         label_visibility="collapsed", key="fx_basis")
                with fb2:
                    fx_dir = st.radio("방향", ["DOWN", "UP"], horizontal=True,
                                       label_visibility="collapsed", key="fx_dir")
                fkey = FLOW_BASIS_KEY["foreign"][fx_basis]
                ranked = rank_flow_flags(fx_flags, fkey, fx_dir)
                prev_ranks = get_flow_prev_day_ranks(flow_hist, "foreign", fx_basis, fx_dir,
                                                      today_kst_str(), price_hist_flow)
                if not ranked:
                    st.caption(f"{fx_basis} 기준 {fx_dir}으로 움직인 종목이 없습니다.")
                else:
                    parts = []
                    for i, r in enumerate(ranked[:20], 1):
                        v = r[fkey]
                        chg = r["오늘등락률"]
                        chg_s = (f'{"+" if chg >= 0 else ""}{chg:.1f}%') if chg is not None else "-"
                        parts.append(
                            f'<div class="updown-row flow-row"><span class="rank">{i}</span>'
                            f'{_rank_delta_html(prev_ranks.get(r["종목명"]), i)}'
                            f'<span class="name">{r["종목명"]}</span>'
                            f'<span class="hold">{r["오늘보유율"]:.1f}%</span>'
                            f'<span class="pct" style="color:{UP_COLOR if v >= 0 else DOWN_COLOR}">'
                            f'{"+" if v >= 0 else ""}{v:.2f}%p</span>'
                            f'<span class="detail">주가 {chg_s}</span></div>'
                        )
                    st.markdown("".join(parts), unsafe_allow_html=True)
                    st.caption(f"종목명 옆은 현재 외국인 보유율 · "
                               f"{fx_basis}({'기준일 대비' if fx_basis == '누적' else '어제 대비'}) "
                               f"보유율 {fx_dir} 큰 순 · 상위 20개 표시 "
                               f"(추적 {len(fx_flags)}종목 중 {fx_dir} {len(ranked)}종목)")

    # ---- Link (연결고리, 포프 다음 단계): 가격·외인비중이 반대로 크게 벌어진 종목 감시 ----
    # 구 "외인 매수 → 이후 주가 (실험)" 패널 자리를 교체(2026-09-11). 프로젝트용이라
    # 비밀번호(LINK_PASSWORD)로 잠가둠 — 한 번 풀면 세션 동안 유지.
    with st.expander("Link (실험)", expanded=False):
        if not st.session_state.get("link_unlocked", False):
            _pw = st.text_input("비밀번호", type="password", key="link_pw",
                                 label_visibility="collapsed", placeholder="비밀번호")
            if _pw == LINK_PASSWORD:
                st.session_state["link_unlocked"] = True
                st.rerun()
            elif _pw:
                st.caption("비밀번호가 틀렸습니다.")
        else:
            _render_link_panel(ph=price_hist_flow, fh=flow_hist,
                                live_quotes=st.session_state.get("live_quotes"),
                                refresh_fn=_refresh_flow_data, T=T)

    # ---- 종목별 보유현황 ----
    SORT_OPTIONS = {"비중": "weight", "섹터": "sector", "현재가": "price",
                     "평가금액": "valuation", "손익": "profit"}
    if "sort_mode" not in st.session_state:
        st.session_state.sort_mode = "weight"
    if "change_sort_active" not in st.session_state:
        st.session_state.change_sort_active = False

    last_updated = ""
    updated_vals = [v for v in df["업데이트시각"].tolist() if v]
    if updated_vals:
        last_updated = max(updated_vals)

    # 전역 CSS(app.py의 `div[data-testid="stColumn"] { flex:1 1 0 !important; }`)가 모든
    # st.columns() 비율을 강제로 동일폭으로 만들어버리므로, 이 줄만 st.container(key=...)로
    # 감싸서 app.py의 [class*="st-key-holdings_title_row"] 스코프 CSS로 비율을 다시 덮어씀.
    _pnl = pd.to_numeric(df["손익"], errors="coerce")
    _n_win, _n_loss = int((_pnl > 0).sum()), int((_pnl < 0).sum())

    with st.container(key="holdings_title_row"):
        col_title2, col_right = st.columns([5, 4])
        with col_title2:
            st.markdown(
                f"##### Holdings <span style='font-size:12px;font-weight:400'>"
                f"(<span style='color:{UP_COLOR}'>{_n_win}</span> / "
                f"<span style='color:{DOWN_COLOR}'>{_n_loss}</span>)</span>",
                unsafe_allow_html=True,
            )
        with col_right:
            # 등락률순 정렬 토글 점 + 업데이트 날짜를 한 줄로 나란히(app.py CSS로 row-flex).
            # 안 눌림=회색 점, 누르면 빨강(국내 관례 상승/강조색). 아래 "정렬 기준" 라디오와는
            # 독립된 별도 상태(change_sort_active)라 라디오 옵션엔 "등락률"을 안 넣는다.
            is_change_sort = st.session_state.change_sort_active
            if st.button("●", key="change_sort_toggle",
                         type="primary" if is_change_sort else "secondary",
                         help="등락률순 정렬"):
                st.session_state.change_sort_active = not is_change_sort
                st.rerun()
            st.markdown(f"<div class='holdings-updated'>{last_updated}</div>",
                        unsafe_allow_html=True)

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

    mode = "change" if st.session_state.change_sort_active else st.session_state.sort_mode
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

        dividend_cache = load_dividend_cache()

        # 외국인 보유율 (Supabase investor_flow 최신값) — 배당 배지 옆에 수치만. 세션 1회 조회.
        foreign_map = st.session_state.get("holding_foreign_map")
        if foreign_map is None:
            _sb = st.secrets.get("supabase", {})
            _fdf = load_investor_flow_db(_sb.get("url", ""), _sb.get("anon_key", ""))
            foreign_map = ({} if _fdf.empty else
                           _fdf.sort_values("날짜").groupby("종목코드")["외국인보유율"].last().to_dict())
            st.session_state["holding_foreign_map"] = foreign_map

        # 최초 진입일 배지(이익 종목만) — 보유일수는 index_history의 실제 거래일로 셈
        # (KR 휴장일 자동 반영). index_history가 못 덮는 앞 구간만 영업일(월~금)로 근사 보충.
        _idx_days = sorted(str(d) for d in load_index_history()["날짜"].tolist())
        _today_str = today_kst_str()
        _fee_rate = state.get("fee_rate", 0.0)

        def _biz_held(entry: str) -> int:
            n = sum(1 for d in _idx_days if entry <= d <= _today_str)
            if _idx_days and entry < _idx_days[0]:
                n += max(len(pd.bdate_range(entry, _idx_days[0])) - 1, 0)
            return max(n, 1)

        for r in rows:
            pc = UP_COLOR if r["손익"] >= 0 else DOWN_COLOR
            psign = "+" if r["손익"] >= 0 else ""
            cc = UP_COLOR if r["등락률"] >= 0 else DOWN_COLOR
            csign = "+" if r["등락률"] >= 0 else ""
            sc = sector_tag_color(r["섹터"])
            name_style = f"color:{NEW_COLOR}" if r["종목명"] in new_today_names else ""

            code = r["종목코드"]
            is_open = st.session_state.holding_detail_open == code

            _fr = foreign_map.get(code)
            _fr_html = ""
            if _fr is not None and not pd.isna(_fr):
                _frc = NEW_COLOR if _fr > 20 else (DOWN_COLOR if _fr <= 5 else "#000000")
                _fr_html = f'<span class="foreign-tag" style="color:{_frc}">{float(_fr):.1f}%</span>'
            _dv_html = _dividend_badge_html(code, dividend_cache)
            _sep = '<span class="badge-sep">·</span>' if (_dv_html and _fr_html) else ""
            _div_inner = _dv_html + _sep + _fr_html
            dividend_row_html = f'<div class="dividend-row">{_div_inner}</div>' if _div_inner else ""

            # 물타기(현재 사이클 매수 2회+) 했는데 반등해서 현재가 ≥ 최초진입가면 카드 옅은 녹색
            _pts = get_holding_trade_points(tx, r["종목명"])
            _buys = _pts[_pts["구분"] == "매수"] if not _pts.empty else _pts
            _watered_ok = (len(_buys) >= 2
                           and float(r["현재가"]) >= float(_buys.iloc[0]["단가"]))
            _card_cls = "stock-card watered-ok" if _watered_ok else "stock-card"

            # 이익 종목만: (1) 그리드 위 우측정렬 줄에 "최초 진입일(보유 거래일수)",
            # (2) 손익 금액 옆에 세금 차감 후 실현액 병기. 손실 종목은 둘 다 없음.
            _entry_row_html = ""
            _pnl_txt = f"{psign}{r['손익']:,.0f}"
            if r["손익"] >= 0:
                if not _buys.empty:
                    _ed = str(_buys.iloc[0]["날짜"]).split(" ")[0]
                    _p = _ed.split("-")
                    if len(_p) == 3:
                        _entry_row_html = f'<div class="entry-line">{int(_p[1])}/{int(_p[2])}({_biz_held(_ed)}일)</div>'
                _net = r["손익"] - float(r["평가금액"]) * _fee_rate
                _pnl_txt += f"({'+' if _net >= 0 else ''}{_net:,.0f})"

            with st.container(key=f"holding_wrap_{code}"):
                st.markdown(f"""
                <div class="{_card_cls}">
                    <div class="stock-top">
                        <span class="stock-title-group"><span class="stock-name" style="{name_style}">{r['종목명']}</span></span>
                        <span class="sector-tag" style="background:{sc}22;color:{sc}">{r['섹터']}</span>
                    </div>
                    {dividend_row_html}{_entry_row_html}<div class="stock-grid">
                        <div class="cell"><div class="top">{r['수량']:.0f}주</div><div class="bottom">{r['비중']:.1f}%</div></div>
                        <div class="cell"><div class="top">{r['현재가']:,.0f}</div><div class="bottom">{r['평단가']:,.0f}</div></div>
                        <div class="cell"><div class="top">{r['평가금액']:,.0f}</div><div class="bottom">{r['매입금액']:,.0f}</div></div>
                        <div class="cell"><div class="top" style="color:{pc}">{_pnl_txt}</div><div class="bottom"><span style="color:{pc}">{psign}{r['손익률']:.1f}%</span> <span style="color:{cc}">{csign}{r['등락률']:.1f}%</span></div></div>
                    </div>
                </div>
                """, unsafe_allow_html=True)

                if st.button("●", key=f"watering_{code}", help="WATERING",
                             type="primary" if is_open else "secondary"):
                    st.session_state.holding_detail_open = None if is_open else code
                    st.rerun()
            if is_open:
                _render_holding_detail(r, tx, T)
