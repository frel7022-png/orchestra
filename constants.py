"""앱 전역에서 쓰는 순수 데이터 상수. 로직 없음."""

UP_COLOR = "#d9364f"    # 국내 관례: 상승/이익 = 빨강
DOWN_COLOR = "#2b6cd4"  # 하락/손실 = 파랑
NEW_COLOR = "#22c55e"   # 오늘 신규 진입 종목 강조색(초록) — 당일 한정, 다음날엔 사라짐
DIVIDEND_MID_COLOR = "#15803d"  # 배당수익률 3~5% 구간 강조색(진한 녹색) — NEW_COLOR보다 어둡게
CASH_LABEL = "현금(예수금)"

# 수정 섹터 27개(+기타/기타2) 각각에 뚜렷이 구분되는 색을 주려고 확장 (2026-09-09, §6-24).
# ui_portfolio_tab이 SECTOR_PALETTE[i % len]로 비중순 배정하므로 순환은 되지만, 30색이면
# 실제 보유 섹터 수(~20)를 다 커버하고도 남는다.
SECTOR_PALETTE = [
    "#2DD4BF", "#F5A623", "#A78BFA", "#34D399", "#F472B6",
    "#FBBF24", "#60A5FA", "#F87171", "#C084FC", "#38BDF8",
    "#FB923C", "#4ADE80", "#E879F9", "#22D3EE", "#FACC15",
    "#818CF8", "#FCA5A5", "#2DD4BF", "#A3E635", "#F9A8D4",
    "#93C5FD", "#FDBA74", "#6EE7B7", "#D8B4FE", "#67E8F9",
    "#FDE047", "#F0ABFC", "#BEF264", "#FECACA", "#7DD3FC",
]

# 섹터별 목표 비중(주식 총자산 대비, %). 아직 정하지 않은 섹터는 포함하지 않음 — 추후 추가.
# 2026-09-09 섹터 체계를 수정 섹터 27개로 교체하며(§6-24) "소비재"가 사라져 목표선도 제거.
# 새 27개 체계 기준 목표는 사용자가 정해주면 여기 추가.
SECTOR_TARGETS = {
    "식품": 30.0,
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
