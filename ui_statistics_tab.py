"""Statistics 탭 (§6-32) — 지금까지 쌓인 매매 결과를 집계해서 보여주는 "결과물" 섹션.

Portfolio 탭이 "지금 서 있는 자리"(현재 보유·물타기), Analysis 탭이 "시장 대비 어떻게
하고 있나"(벤치마크·DC/UC)라면, 이 탭은 "그동안 무슨 일이 있었나"를 총량으로 본다 —
정의(함수) → 로직(계산) → 결과(숫자)라는 순서로, 나만 보는 게 아니라 나중에 다른 사람이
봐도 따라올 수 있게 서술 통계부터 쌓아나간다(2026-09-16, 사용자 요청).
"""

import pandas as pd
import streamlit as st

from constants import UP_COLOR, DOWN_COLOR
from portfolio_core import price_bracket_distribution, top_traded_stocks


def render_statistics_tab(tx, T):
    st.markdown("##### Price Brackets")
    st.caption("청산 완료된 사이클을 진입가(첫 매수 단가) 기준으로 가격대별로 묶은 것 — "
               "이 계좌가 실제로 어떤 가격대 주식을 사고파는지 보는 기초 통계.")
    dist = price_bracket_distribution(tx)
    total_cycles = int(dist["건수"].sum())
    if total_cycles == 0:
        st.caption("청산 완료된 사이클이 아직 없어요.")
    else:
        max_pct = max(dist["비율"].max(), 1.0)
        rows_html = []
        for _, r in dist.iterrows():
            width_pct = max(min(r["비율"] / max_pct * 100, 100), 0) if r["건수"] else 0
            rows_html.append(
                '<div class="sector-bar-row">'
                f'<div class="sector-bar-label">{r["구간"]}</div>'
                '<div class="sector-bar-track">'
                f'<div class="sector-bar-fill" style="background:{UP_COLOR};width:{width_pct}%"></div>'
                '</div>'
                f'<div class="sector-bar-pct"><span class="cur">{int(r["건수"])}건</span>'
                f'<span class="delta" style="color:{T["muted"]}">({r["비율"]:.0f}%)</span></div>'
                '</div>'
            )
        st.markdown(f'<div class="sector-bar-list">{"".join(rows_html)}</div>', unsafe_allow_html=True)
        st.caption(f"총 {total_cycles}건(청산 완료 사이클 기준)")

    st.markdown("##### Top Traded")
    st.caption("청산 완료된 사이클이 가장 많은 종목 10개 — 최초 진입가 대비 마지막 매도가의 "
               "순수 가격 변화와, 반복 매매로 실제 벌어들인 누적 실현손익을 나란히 비교한다. "
               "가격 변화보다 실현손익률이 훨씬 크면 반복 매매(물타기 후 재진입 등)가 "
               "단순 보유보다 더 벌었다는 뜻.")
    top = top_traded_stocks(tx, top_n=10)
    if top.empty:
        st.caption("청산 완료된 사이클이 아직 없어요.")
        return

    def _signed(v, unit=""):
        c = UP_COLOR if v >= 0 else DOWN_COLOR
        sign = "+" if v >= 0 else ""
        return f'<span style="color:{c}">{sign}{v:,.0f}{unit}</span>'

    def _signed_pct(v):
        c = UP_COLOR if v >= 0 else DOWN_COLOR
        sign = "+" if v >= 0 else ""
        return f'<span style="color:{c}">{sign}{v:.1f}%</span>'

    cards = []
    for _, r in top.iterrows():
        cards.append(
            f'<div style="padding:8px 0;border-bottom:1px solid {T["border"]}">'
            '<div style="display:flex;justify-content:space-between;align-items:baseline">'
            f'<span style="font-weight:600;font-size:13px">{r["종목명"]}</span>'
            f'<span style="font-size:11px;color:{T["muted"]}">{int(r["청산횟수"])}회 청산</span>'
            '</div>'
            f'<div style="font-size:11.5px;color:{T["muted"]};margin-top:2px">'
            f'{r["최초진입가"]:,.0f}원({r["최초진입일"]}) → {r["최후매도가"]:,.0f}원({r["최후매도일"]}) '
            f'({_signed_pct(r["가격변화율"])})'
            '</div>'
            f'<div style="font-size:12px;margin-top:2px">'
            f'누적실현손익 {_signed(r["누적실현손익"], "원")} ({_signed_pct(r["누적실현손익률"])})'
            '</div></div>'
        )
    st.markdown("".join(cards), unsafe_allow_html=True)
