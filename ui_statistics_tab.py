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


def _current_price_map(holdings: pd.DataFrame) -> dict:
    """종목명→현재가. 지금 보유 중이면 holdings의 현재가(이미 메인 "시세 새로고침"으로
    최신) 그대로 쓰고, 보유 중이 아니면 Up/Down이 이미 캐싱해둔 `st.session_state
    ["updown_results"]`(§6-31 로컬 캐시로 세션 리셋 후에도 남아있음)의 현재가를 재사용한다 —
    Statistics 탭 자체는 새 네트워크 요청을 전혀 안 낸다(2026-09-16, §6-31과 같은 원칙)."""
    price_map = {}
    if holdings is not None and not holdings.empty:
        h = holdings.copy()
        h["현재가"] = pd.to_numeric(h["현재가"], errors="coerce")
        price_map.update({row["종목명"]: row["현재가"] for _, row in h.iterrows()
                           if pd.notna(row["현재가"])})
    for r in (st.session_state.get("updown_results") or []):
        if r["종목명"] not in price_map and r.get("현재가") is not None:
            price_map[r["종목명"]] = r["현재가"]
    return price_map


def render_statistics_tab(tx, holdings, T):
    st.markdown("##### Price Brackets")
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

    st.markdown("##### Top Traded")
    price_map = _current_price_map(holdings)
    top = top_traded_stocks(tx, top_n=10, current_prices=price_map)
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
        ref_label = "현재" if r["기준가구분"] == "현재" else str(r["최후매도일"])
        # 현재가를 실제로 쓴 경우에만 "마지막 매도가 이거였다"를 보조 정보로 한 줄 더 —
        # 폴백(현재가 없음)일 땐 기준가 자체가 최후매도가라 중복이라 안 붙임.
        last_sell_note = (
            f'<div style="font-size:11px;color:{T["muted2"]};margin-top:1px">'
            f'마지막 매도 {r["최후매도가"]:,.0f}원({r["최후매도일"]})</div>'
            if r["기준가구분"] == "현재" else ""
        )
        cards.append(
            f'<div style="padding:8px 0;border-bottom:1px solid {T["border"]}">'
            '<div style="display:flex;justify-content:space-between;align-items:baseline">'
            f'<span style="font-weight:600;font-size:13px">{r["종목명"]}</span>'
            f'<span style="font-size:11px;color:{T["muted"]}">{int(r["청산횟수"])}회 청산</span>'
            '</div>'
            f'<div style="font-size:11.5px;color:{T["muted"]};margin-top:2px">'
            f'{r["최초진입가"]:,.0f}원({r["최초진입일"]}) → {r["기준가"]:,.0f}원({ref_label}) '
            f'({_signed_pct(r["가격변화율"])})</div>'
            f'{last_sell_note}'
            f'<div style="font-size:12px;margin-top:2px">'
            f'누적실현손익 {_signed(r["누적실현손익"], "원")} ({_signed_pct(r["누적실현손익률"])})'
            '</div></div>'
        )
    st.markdown("".join(cards), unsafe_allow_html=True)
