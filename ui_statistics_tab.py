"""Statistics 탭 (§6-32) — 지금까지 쌓인 매매 결과를 집계해서 보여주는 "결과물" 섹션.

Portfolio 탭이 "지금 서 있는 자리"(현재 보유·물타기), Analysis 탭이 "시장 대비 어떻게
하고 있나"(벤치마크·DC/UC)라면, 이 탭은 "그동안 무슨 일이 있었나"를 총량으로 본다 —
정의(함수) → 로직(계산) → 결과(숫자)라는 순서로, 나만 보는 게 아니라 나중에 다른 사람이
봐도 따라올 수 있게 서술 통계부터 쌓아나간다(2026-09-16, 사용자 요청).

**현재가는 안 씀**(2026-09-16 결론) — 한때 최초진입가 대비 지금 가격까지 비교하는 기능
(Selection Index)을 만들었는데, "현재가"가 매일 움직이는 값이라 판정 자체가 계속
흔들리는 근본적 문제가 있었고(사용자 지적: "종목이 170개 넘다 보니 한동안 안 산 것도
있는데, 지금은 승리지만 시간이 흐르면 패배가 될 수도 있다"), 이미 Up/Down이 "판 뒤
가격이 어떻게 됐는지"를 전담하므로 여기서 또 현재가를 쓸 이유도 없다는 판단으로
전부 걷어내고 순수 매매 기록(진입가·매도횟수·실현손익)만 남김 — 네트워크 조회도 없어짐.
"""

import streamlit as st

from constants import UP_COLOR, DOWN_COLOR
from portfolio_core import (
    price_bracket_distribution, holdings_price_bracket_distribution, top_traded_stocks,
)

_TOP_TRADED_PAGE_SIZE = 20


def _render_bracket_bars_paired(dist_sold, dist_held, T: dict) -> None:
    """구간마다 빨강(매도 이력) 막대 바로 밑에 파랑(현재 보유) 막대를 짝지어 보여준다
    (2026-09-16 사용자 지시: "둘이 비교되게 빨간 막대 밑에 파란 막대가 낫지 않을까") —
    두 분포를 한 스케일(max_pct)로 같이 정규화해서 막대 길이가 색끼리도 바로 비교되게 함.
    **구간 라벨(예: "1만원 이하")은 빨강 줄에만 쓰고 파랑 줄엔 비워둔다**(2026-09-17 사용자
    지시: "둘 다 써있으면 혼잡하다") — 어차피 같은 구간이 위아래로 짝지어 있어 라벨 없이도
    파랑이 어느 구간인지 헷갈리지 않는다."""
    max_pct = max(dist_sold["비율"].max(), dist_held["비율"].max(), 1.0)

    def _bar(r, color, show_label):
        width_pct = max(min(r["비율"] / max_pct * 100, 100), 0) if r["건수"] else 0
        label = r["구간"] if show_label else ""
        return (
            '<div class="sector-bar-row">'
            f'<div class="sector-bar-label">{label}</div>'
            '<div class="sector-bar-track">'
            f'<div class="sector-bar-fill" style="background:{color};width:{width_pct}%"></div>'
            '</div>'
            f'<div class="sector-bar-pct"><span class="cur">{int(r["건수"])}건</span>'
            f'<span class="delta" style="color:{T["muted"]}">({r["비율"]:.0f}%)</span></div>'
            '</div>'
        )

    rows_html = []
    for (_, rs), (_, rh) in zip(dist_sold.iterrows(), dist_held.iterrows()):
        rows_html.append(
            f'<div style="margin-bottom:10px">{_bar(rs, UP_COLOR, True)}'
            f'<div style="margin-top:2px">{_bar(rh, DOWN_COLOR, False)}</div></div>'
        )
    st.caption("빨강 매도 이력 · 파랑 현재 보유")
    st.markdown(f'<div class="sector-bar-list">{"".join(rows_html)}</div>', unsafe_allow_html=True)


def _render_top_traded_cards(rows, T: dict) -> None:
    def _signed(v, unit=""):
        c = UP_COLOR if v >= 0 else DOWN_COLOR
        sign = "+" if v >= 0 else ""
        return f'<span style="color:{c}">{sign}{v:,.0f}{unit}</span>'

    def _signed_pct(v):
        c = UP_COLOR if v >= 0 else DOWN_COLOR
        sign = "+" if v >= 0 else ""
        return f'<span style="color:{c}">{sign}{v:.1f}%</span>'

    cards = []
    for _, r in rows.iterrows():
        cards.append(
            f'<div style="padding:7px 0;border-bottom:1px solid {T["border"]}">'
            '<div style="display:flex;justify-content:space-between;align-items:baseline">'
            f'<span style="font-weight:600;font-size:13px">{r["종목명"]}</span>'
            f'<span style="font-size:11px;color:{T["muted"]}">{int(r["매도횟수"])}회</span>'
            '</div>'
            f'<div style="font-size:12px;margin-top:2px">'
            f'누적실현손익 {_signed(r["누적실현손익"], "원")} (평균 손익률 {_signed_pct(r["누적실현손익률"])})'
            '</div></div>'
        )
    st.markdown("".join(cards), unsafe_allow_html=True)


def render_statistics_tab(tx, holdings, T):
    st.markdown("##### Price Brackets")
    dist = price_bracket_distribution(tx)
    holdings_dist = holdings_price_bracket_distribution(holdings)
    if int(dist["건수"].sum()) == 0 and int(holdings_dist["건수"].sum()) == 0:
        st.caption("매도 완료된 사이클이 아직 없어요.")
    else:
        # 구간마다 빨강(매도 이력) 막대 밑에 파랑(현재 보유) 막대를 짝지어 — 과거 성향과
        # 지금 실제 분포가 한쪽으로 안 쏠렸는지 바로 비교되게(2026-09-16 사용자 지시).
        _render_bracket_bars_paired(dist, holdings_dist, T)

    with st.expander("Top Traded", expanded=False):
        top = top_traded_stocks(tx, top_n=None)
        if top.empty:
            st.caption("매도 완료된 사이클이 아직 없어요.")
        else:
            show_n = st.session_state.get("top_traded_show_n", _TOP_TRADED_PAGE_SIZE)
            _render_top_traded_cards(top.iloc[:show_n], T)
            if len(top) > show_n:
                if st.button("더보기", key="top_traded_more", use_container_width=True):
                    st.session_state["top_traded_show_n"] = show_n + _TOP_TRADED_PAGE_SIZE
                    st.rerun()
