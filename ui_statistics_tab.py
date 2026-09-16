"""Statistics 탭 (§6-32) — 지금까지 쌓인 매매 결과를 집계해서 보여주는 "결과물" 섹션.

Portfolio 탭이 "지금 서 있는 자리"(현재 보유·물타기), Analysis 탭이 "시장 대비 어떻게
하고 있나"(벤치마크·DC/UC)라면, 이 탭은 "그동안 무슨 일이 있었나"를 총량으로 본다 —
정의(함수) → 로직(계산) → 결과(숫자)라는 순서로, 나만 보는 게 아니라 나중에 다른 사람이
봐도 따라올 수 있게 서술 통계부터 쌓아나간다(2026-09-16, 사용자 요청).
"""

import pandas as pd
import streamlit as st

from constants import UP_COLOR, DOWN_COLOR
from portfolio_core import (
    price_bracket_distribution, top_traded_stocks, selection_index, get_current_prices_for_names,
    today_kst_str, now_kst_str, save_ui_cache_json, load_ui_cache_json,
)

_VERDICT_COLOR = {"승": UP_COLOR, "패": DOWN_COLOR}  # "제외"는 회색(T["muted2"])


def _current_price_map(holdings: pd.DataFrame) -> dict:
    """종목명→현재가. 지금 보유 중이면 holdings의 현재가(이미 메인 "시세 새로고침"으로
    최신) 그대로 쓰고, 보유 중이 아니면 Up/Down이 이미 캐싱해둔 `st.session_state
    ["updown_results"]`(§6-31 로컬 캐시로 세션 리셋 후에도 남아있음)의 현재가를 재사용한다 —
    둘 다에서 못 찾은 나머지만 `_refresh_top_traded`가 실제로 새로 조회한다."""
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


def _refresh_top_traded(tx, holdings) -> None:
    """Top Traded 새로고침 — 사용자 지시(2026-09-16): "121개(청산 사이클)는 9/16까지는
    고정이니 매번 다시 계산할 필요 없다 — 오늘 하루에 두 번 눌러도 이미 오늘 자로 고정
    했으면 그대로 두고, 새 날짜에 누르면 그 사이(예: 휴가로 9/17→9/25면 9/18~25) 새로 닫힌
    사이클을 반영해서 다시 오늘로 고정." 사이클 집계 자체는 가벼워서 매번 새로 훑어도
    문제없지만, **현재가 조회(네트워크)는 하루 한 번으로 제한**해 부하를 줄인다 — 이게
    이 함수가 실제로 아끼는 비용."""
    cache = st.session_state.get("top_traded_cache")
    today = today_kst_str()
    if cache and cache.get("as_of_date") == today:
        return  # 오늘 이미 고정됨 — 새로 조회 안 함(부하 절약)

    top = top_traded_stocks(tx, top_n=None)
    names = list(top["종목명"]) if not top.empty else []
    price_map = _current_price_map(holdings)
    missing = [n for n in names if n not in price_map]
    if missing:
        price_map.update(get_current_prices_for_names(missing))
    sel = selection_index(tx, current_prices=price_map)
    st.session_state["top_traded_cache"] = {
        "as_of_date": today, "checked_at": now_kst_str(),
        "rows": sel["rows"], "wins": sel["wins"], "losses": sel["losses"],
        "excluded": sel["excluded"], "decided": sel["decided"],
        "win_rate": sel["win_rate"], "index": sel["index"],
    }
    save_ui_cache_json("top_traded", st.session_state["top_traded_cache"])


def _render_bracket_bars(dist: pd.DataFrame, T: dict) -> None:
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


def _render_top_traded_cards(rows: list[dict], T: dict) -> None:
    def _signed(v, unit=""):
        c = UP_COLOR if v >= 0 else DOWN_COLOR
        sign = "+" if v >= 0 else ""
        return f'<span style="color:{c}">{sign}{v:,.0f}{unit}</span>'

    def _signed_pct(v):
        c = UP_COLOR if v >= 0 else DOWN_COLOR
        sign = "+" if v >= 0 else ""
        return f'<span style="color:{c}">{sign}{v:.1f}%</span>'

    cards = []
    for r in rows:
        ref_label = "현재" if r["기준가구분"] == "현재" else str(r["최후매도일"])
        last_sell_note = (
            f'<div style="font-size:11px;color:{T["muted2"]};margin-top:1px">'
            f'마지막 매도 {r["최후매도가"]:,.0f}원({r["최후매도일"]})</div>'
            if r["기준가구분"] == "현재" else ""
        )
        verdict = r.get("판정")
        verdict_html = (
            f'<span style="font-size:11px;font-weight:700;margin-right:6px;'
            f'color:{_VERDICT_COLOR.get(verdict, T["muted2"])}">{verdict}</span>'
            if verdict else ""
        )
        cards.append(
            f'<div style="padding:8px 0;border-bottom:1px solid {T["border"]}">'
            '<div style="display:flex;justify-content:space-between;align-items:baseline">'
            f'<span style="font-weight:600;font-size:13px">{r["종목명"]}</span>'
            f'<span style="font-size:11px;color:{T["muted"]}">{verdict_html}{int(r["청산횟수"])}회 청산</span>'
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


def render_statistics_tab(tx, holdings, T):
    st.markdown("##### Price Brackets")
    dist = price_bracket_distribution(tx)
    if int(dist["건수"].sum()) == 0:
        st.caption("청산 완료된 사이클이 아직 없어요.")
    else:
        _render_bracket_bars(dist, T)

    with st.expander("Top Traded", expanded=False):
        if st.button("새로고침", key="top_traded_refresh", use_container_width=True):
            with st.spinner("현재가 조회 중..."):
                _refresh_top_traded(tx, holdings)
            st.rerun()

        # 세션이 새로 열려 비어있으면(§6-31) 로컬 캐시에서 마지막 고정값을 먼저 채운다.
        if "top_traded_cache" not in st.session_state:
            cached = load_ui_cache_json("top_traded")
            if cached:
                st.session_state["top_traded_cache"] = cached

        cache = st.session_state.get("top_traded_cache")
        if not cache or not cache.get("rows"):
            st.caption("새로고침을 누르면 청산 완료된 사이클을 전부 종목별로 모아 보여줍니다.")
        else:
            st.caption(f"{cache['as_of_date']} 기준 고정 · 마지막 조회 {cache['checked_at']}")
            idx = cache["index"]
            idx_color = UP_COLOR if idx > 0 else (DOWN_COLOR if idx < 0 else T["muted"])
            idx_sign = "+" if idx > 0 else ""
            st.markdown(
                f'<div style="font-size:12.5px;color:{T["text"]};font-weight:600;margin-bottom:6px">'
                f'Selection Index <span style="color:{idx_color}">{idx_sign}{idx}</span>'
                f'<span style="font-weight:400;color:{T["muted"]}">'
                f' (승 {cache["wins"]} · 패 {cache["losses"]} · 제외 {cache["excluded"]}, '
                f'승률 {cache["win_rate"]:.0f}%)</span></div>',
                unsafe_allow_html=True,
            )
            _render_top_traded_cards(cache["rows"], T)
