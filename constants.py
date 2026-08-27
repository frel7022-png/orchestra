"""앱 전역에서 쓰는 순수 데이터 상수. 로직 없음."""

UP_COLOR = "#d9364f"    # 국내 관례: 상승/이익 = 빨강
DOWN_COLOR = "#2b6cd4"  # 하락/손실 = 파랑
NEW_COLOR = "#22c55e"   # 오늘 신규 진입 종목 강조색(초록) — 당일 한정, 다음날엔 사라짐
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
