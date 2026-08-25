"""
포트폴리오 데이터 계층 — Streamlit에 의존하지 않는 순수 로직.

app.py(웹 화면)와 ingest_daily.py(일일 매매일지 반영 스크립트)가 이 모듈을
공유해서 쓴다. 두 곳에서 로직이 갈라지면(=중복 구현) 계산이 서로 어긋나는
버그가 생기기 쉬우므로, holdings/거래 관련 계산은 전부 여기 한 곳에만 둔다.

핵심 원칙 (claude.md 참고):
- transactions.csv가 유일한 진실 공급원. holdings(portfolio_data.csv)는
  거래 기록을 재생(replay)해서 계산되는 파생 데이터로만 취급하고 손으로 고치지 않는다.
- 종목코드/섹터처럼 거래 기록만으로는 알 수 없는 정보는 별도 영구 캐시
  (stock_code_cache.csv, stock_sector_cache.csv)에 보관해서, holdings를
  다시 계산해도 사라지지 않게 한다.
- 같은 날짜의 일일 매매일지를 여러 번 반영해도 안전하도록(=누적되지 않도록),
  "그 날짜의 기존 CSV 기반 거래는 지우고 이번 것으로 교체 후 전체 재생"한다.
"""

import difflib
import io
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from bs4 import BeautifulSoup

KST = ZoneInfo("Asia/Seoul")
HERE = Path(__file__).parent

HOLDINGS_FILE = HERE / "portfolio_data.csv"
TX_FILE = HERE / "transactions.csv"
STATE_FILE = HERE / "account_state.csv"
HISTORY_FILE = HERE / "asset_history.csv"
SECTOR_HISTORY_FILE = HERE / "sector_history.csv"
CODE_CACHE_FILE = HERE / "stock_code_cache.csv"
SECTOR_CACHE_FILE = HERE / "stock_sector_cache.csv"
WATCHLIST_FILE = HERE / "watchlist.csv"  # "Fishing" 관심종목 리스트 (보유/거래와 무관한 별도 목록)
CHECKPOINT_HOLDINGS_FILE = HERE / "checkpoint_holdings.csv"  # rebuild_portfolio_incremental 참고
CHECKPOINT_STATE_FILE = HERE / "checkpoint_state.csv"

HOLD_COLUMNS = ["종목명", "종목코드", "섹터", "수량", "평단가", "현재가", "등락률", "업데이트시각"]
TX_COLUMNS = ["id", "날짜", "종목명", "구분", "수량", "단가", "실현손익", "메모", "정산반영"]

DAILY_IMPORT_TAG = "일일매매일지"  # 이 메모가 붙은 거래는 같은 날짜 재반영 시 교체 대상

# 섹터 비중 보기 전용 그룹 매핑 (종목별 보유현황의 세부 섹터는 그대로 유지됨)
SECTOR_GROUP_MAP = {
    "유통": "유통 물류",
    "물류": "유통 물류",
    "반도체소재": "반도체",
    "반도체장비": "반도체",
    "인터넷": "반도체",
    "건자재": "건설",
    "건설": "건설",
    "제지": "소비재",
    "섬유의류": "소비재",
    "화장품": "소비재",
    "제약바이오": "의료바이오",
    "제약바이어": "의료바이오",  # 오타 대비
    "의료기기": "의료바이오",
    "해운": "해운",
    "조선": "해운",
    "엔터테인먼트": "엔터",
    "게임": "엔터",
    "렌탈서비스": "서비스",
}


def group_sector(sector: str) -> str:
    """섹터 비중 보기용 그룹명 반환. 매핑에 없으면 원래 섹터명 그대로."""
    return SECTOR_GROUP_MAP.get(sector, sector)


def now_kst() -> datetime:
    return datetime.now(KST)


def today_kst_str() -> str:
    return now_kst().strftime("%Y-%m-%d")


def now_kst_str() -> str:
    return now_kst().strftime("%m/%d %H:%M")


def clean_str(x) -> str:
    """pd.NA / NaN / None 안전하게 빈 문자열로 처리."""
    if x is None or (isinstance(x, float) and pd.isna(x)) or (x is pd.NA):
        return ""
    try:
        if pd.isna(x):
            return ""
    except (TypeError, ValueError):
        pass
    return str(x).strip()


# ------------------------------------------------------------------ #
# 종목코드/섹터 영구 캐시
# transactions 재생(rebuild)으로 holdings를 다시 만들 때마다 이 값들이 날아가지
# 않도록, holdings.csv와는 별개의 파일에 독립적으로 보관한다.
# ------------------------------------------------------------------ #
def load_code_cache() -> dict:
    if CODE_CACHE_FILE.exists():
        df = pd.read_csv(CODE_CACHE_FILE, dtype=str, keep_default_na=False)
        return {k: v for k, v in zip(df["종목명"], df["종목코드"]) if k and v}
    return {}


def update_code_cache(new_entries: dict) -> None:
    new_entries = {k: v for k, v in new_entries.items() if k and v}
    if not new_entries:
        return
    cache = load_code_cache()
    cache.update(new_entries)
    pd.DataFrame(sorted(cache.items()), columns=["종목명", "종목코드"]).to_csv(CODE_CACHE_FILE, index=False)


def load_sector_cache() -> dict:
    if SECTOR_CACHE_FILE.exists():
        df = pd.read_csv(SECTOR_CACHE_FILE, dtype=str, keep_default_na=False)
        return {k: v for k, v in zip(df["종목명"], df["섹터"]) if k and v}
    return {}


def update_sector_cache(new_entries: dict) -> None:
    new_entries = {k: v for k, v in new_entries.items() if k and v}
    if not new_entries:
        return
    cache = load_sector_cache()
    cache.update(new_entries)
    pd.DataFrame(sorted(cache.items()), columns=["종목명", "섹터"]).to_csv(SECTOR_CACHE_FILE, index=False)


# ------------------------------------------------------------------ #
# 데이터 로드 / 저장
# ------------------------------------------------------------------ #
def load_holdings() -> pd.DataFrame:
    if HOLDINGS_FILE.exists():
        df = pd.read_csv(HOLDINGS_FILE, dtype={"종목코드": str}, keep_default_na=False, na_values=[""])
        for col in HOLD_COLUMNS:
            if col not in df.columns:
                df[col] = "" if col in ("종목명", "종목코드", "섹터", "업데이트시각") else 0.0
        for col in ("수량", "평단가", "현재가", "등락률"):
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0).astype(float)
        for col in ("종목명", "종목코드", "섹터", "업데이트시각"):
            df[col] = df[col].apply(clean_str)

        # holdings에 빠진 종목코드/섹터는 영구 캐시에서 보충 (rebuild 직후 등)
        code_cache = load_code_cache()
        need_code = df["종목코드"] == ""
        if need_code.any() and code_cache:
            df.loc[need_code, "종목코드"] = df.loc[need_code, "종목명"].map(code_cache).fillna("")
        sector_cache = load_sector_cache()
        need_sector = df["섹터"] == ""
        if need_sector.any() and sector_cache:
            df.loc[need_sector, "섹터"] = df.loc[need_sector, "종목명"].map(sector_cache).fillna("")

        return df[HOLD_COLUMNS]
    return pd.DataFrame(columns=HOLD_COLUMNS)


def save_holdings(df: pd.DataFrame) -> None:
    df.to_csv(HOLDINGS_FILE, index=False)


def load_transactions() -> pd.DataFrame:
    if TX_FILE.exists():
        df = pd.read_csv(TX_FILE, dtype={"id": str}, keep_default_na=False, na_values=[""])
        for col in TX_COLUMNS:
            if col not in df.columns:
                df[col] = ""
        return df[TX_COLUMNS]
    return pd.DataFrame(columns=TX_COLUMNS)


def save_transactions(df: pd.DataFrame) -> None:
    df.to_csv(TX_FILE, index=False)


def load_state() -> dict:
    if STATE_FILE.exists():
        df = pd.read_csv(STATE_FILE)
        return {
            "cash": float(df.loc[0, "예수금"]),
            "initial": float(df.loc[0, "최초자본"]),
            "fee_rate": float(df.loc[0, "수수료율"]) if "수수료율" in df.columns else 0.0,
        }
    return {"cash": 10_000_000.0, "initial": 10_000_000.0, "fee_rate": 0.0}


def save_state(state: dict) -> None:
    pd.DataFrame([{
        "예수금": state["cash"],
        "최초자본": state["initial"],
        "수수료율": state.get("fee_rate", 0.0),
    }]).to_csv(STATE_FILE, index=False)


def load_history() -> pd.DataFrame:
    if HISTORY_FILE.exists():
        return pd.read_csv(HISTORY_FILE)
    return pd.DataFrame(columns=["날짜", "총자산", "조정자산"])


def save_history(df: pd.DataFrame) -> None:
    df.to_csv(HISTORY_FILE, index=False)


def snapshot_history(total_assets: float, adjusted_assets: float, on_date: str | None = None) -> None:
    hist = load_history()
    d = on_date or today_kst_str()
    hist = hist[hist["날짜"] != d]
    hist = pd.concat([hist, pd.DataFrame([{"날짜": d, "총자산": total_assets, "조정자산": adjusted_assets}])])
    hist = hist.sort_values("날짜")
    save_history(hist)


def load_sector_history() -> pd.DataFrame:
    if SECTOR_HISTORY_FILE.exists():
        return pd.read_csv(SECTOR_HISTORY_FILE)
    return pd.DataFrame(columns=["날짜", "섹터그룹", "비중"])


def save_sector_history(df: pd.DataFrame) -> None:
    df.to_csv(SECTOR_HISTORY_FILE, index=False)


def snapshot_sector_history(weights: dict, on_date: str | None = None) -> None:
    """섹터그룹별 오늘자(또는 지정 날짜) 비중(%) 스냅샷 저장. 같은 날짜 데이터는 덮어씀."""
    if not weights:
        return
    hist = load_sector_history()
    d = on_date or today_kst_str()
    hist = hist[hist["날짜"] != d]
    new_rows = pd.DataFrame([{"날짜": d, "섹터그룹": k, "비중": v} for k, v in weights.items()])
    hist = pd.concat([hist, new_rows], ignore_index=True)
    hist = hist.sort_values(["날짜", "섹터그룹"])
    save_sector_history(hist)


# ------------------------------------------------------------------ #
# 네이버 금융: 종목명 → 종목코드 자동 검색 + 시세 조회
# ------------------------------------------------------------------ #
def resolve_code(name: str, code_cache: dict | None = None):
    name = (name or "").strip()
    if not name:
        return None
    if code_cache and code_cache.get(name):
        return code_cache[name]
    url = "https://ac.stock.naver.com/ac"
    params = {"q": name, "target": "stock,index,marketindicator,coin,ipo", "st": "111"}
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=6)
        r.raise_for_status()
        items = (r.json() or {}).get("items") or []
        for it in items:
            code = str(it.get("code") or "").strip()
            if code.isdigit() and len(code) == 6:
                return code
    except Exception:
        pass
    return None


def match_stock_name(query: str) -> dict | None:
    """오탈자가 있을 수 있는 종목명을 네이버 자동완성 검색으로 정정한다("동진세미컴" → "동진쎄미켐").
    네이버 자동완성은 접두어 일치 검색이라 오탈자가 있으면 그대로는 결과가 안 나오는 경우가 많아서,
    검색이 비면 앞부분 글자 수를 줄여가며(4→3→2→1) 재시도하고, 후보들 중 원래 입력과 가장 비슷한
    이름을 difflib로 골라낸다. 반환값: {"name": 정정된 종목명, "code": 종목코드} 또는 확신이
    안 서면(유사도 낮음/후보 없음) None — 이 경우 사람이 직접 확인해야 함."""
    query = (query or "").strip()
    if not query:
        return None

    def search(q):
        try:
            r = requests.get("https://ac.stock.naver.com/ac",
                              params={"q": q, "target": "stock,index,marketindicator,coin,ipo", "st": "111"},
                              headers={"User-Agent": "Mozilla/5.0"}, timeout=6)
            r.raise_for_status()
            items = (r.json() or {}).get("items") or []
            # 종목코드는 보통 6자리 숫자지만, 우선주 등은 "37550K"처럼 끝이 영문인 6자리 코드도 있음
            return [it for it in items if len(str(it.get("code") or "").strip()) == 6]
        except Exception:
            return []

    candidates = search(query)
    for n in (4, 3, 2, 1):
        if candidates:
            break
        if len(query) > n:
            candidates = search(query[:n])

    if not candidates:
        return None

    def score(it):
        return difflib.SequenceMatcher(None, query, it.get("name", "")).ratio()

    best = max(candidates, key=score)
    if score(best) < 0.5:
        return None
    return {"name": best.get("name"), "code": str(best.get("code"))}


# ------------------------------------------------------------------ #
# "Fishing" 관심종목 리스트 — 보유/거래와 무관, 순수 관찰용
# ------------------------------------------------------------------ #
def load_watchlist() -> pd.DataFrame:
    if WATCHLIST_FILE.exists():
        df = pd.read_csv(WATCHLIST_FILE, dtype=str, keep_default_na=False)
        for col in ("종목명", "종목코드"):
            if col not in df.columns:
                df[col] = ""
        sector_cache = load_sector_cache()
        df["섹터"] = df["종목명"].map(sector_cache).fillna("미분류")
        return df[["종목명", "종목코드", "섹터"]]
    return pd.DataFrame(columns=["종목명", "종목코드", "섹터"])


def save_watchlist(names_codes: list[dict]) -> None:
    """names_codes: [{"종목명": ..., "종목코드": ...}, ...]. 섹터는 저장 안 함(캐시에서 매번 조회)."""
    pd.DataFrame(names_codes, columns=["종목명", "종목코드"]).drop_duplicates("종목명").to_csv(
        WATCHLIST_FILE, index=False)


WATCHLIST_PRICE_COLUMNS = ["종목명", "종목코드", "최초가", "최근가", "최근조회일시", "전일대비"]


def get_first_day_prices_db(supabase_url: str, supabase_key: str) -> dict:
    """Supabase price_history에서 종목코드별 최초 관측일(가장 이른 날짜) 종가를 가져온다.
    반환: {종목코드: 최초가}. 접속 정보 없음/데이터 없음이면 빈 dict."""
    hist = load_watchlist_history_db(supabase_url, supabase_key)
    if hist.empty:
        return {}
    hist = hist.sort_values("날짜")
    return hist.groupby("종목코드")["종가"].first().to_dict()


def refresh_watchlist_prices(watchlist: pd.DataFrame, supabase_url: str = "",
                              supabase_key: str = "") -> tuple[pd.DataFrame, list[str]]:
    """"Fishing" 새로고침 버튼 로직. 관심종목별로 최초가(누적 등락률의 기준점)/최근가(이번
    조회 가격)/전일대비(네이버가 이미 계산해서 주는 전일 종가 대비 등락률)를 계산한다.

    최초가는 Supabase price_history의 최초 관측일 종가에서 매번 새로 가져온다(로컬 파일에
    저장/승계하지 않음). 예전엔 로컬 CSV(watchlist_prices.csv)에 최초가를 "영구 보존"하려고
    했는데, 이 파일이 git에 한 번도 커밋된 적이 없어서 Streamlit Cloud가 재배포할 때마다
    (=git push할 때마다) 통째로 사라지고, 그때마다 최초가가 그 시점의 "어제 종가"로 리셋되는
    버그가 있었다(재배포가 잦을수록 심해짐 — 2026-08-21 발견). price_history는 앱 재배포와
    무관하게 독립적으로 유지되는 DB라 이 문제가 구조적으로 없어짐 — 로컬 캐시 파일 자체가
    필요 없어져서 아예 없앰.

    DB에 아직 이 종목 히스토리가 없으면(예: watchlist에 막 추가돼서 cron이 한 번도 못 돈
    경우) 최근가와 전일대비로 역산한 "어제 종가"를 임시 최초가로 씀 — 내일부터 cron이
    쌓아주면 자동으로 정확한 값으로 바뀐다."""
    codes = [c for c in watchlist["종목코드"].tolist() if c]
    quotes, quote_errors = fetch_quotes(codes)
    origin_prices = get_first_day_prices_db(supabase_url, supabase_key)
    now = now_kst_str()

    rows = []
    for _, r in watchlist.iterrows():
        name, code = r["종목명"], r["종목코드"]
        q = quotes.get(code)
        if q is None:
            continue
        price, change_pct = q["price"], q["change_pct"]
        origin = origin_prices.get(code)
        if origin is None:
            origin = price / (1 + change_pct / 100) if change_pct != -100 else price
        rows.append({"종목명": name, "종목코드": code, "최초가": origin,
                     "최근가": price, "최근조회일시": now, "전일대비": change_pct})

    result = pd.DataFrame(rows, columns=WATCHLIST_PRICE_COLUMNS)
    return result, quote_errors


# ------------------------------------------------------------------ #
# Supabase DB 히스토리 조회 — GitHub Actions가 매일 평일 16:00 KST에 관심종목 153개
# 종가를 Supabase price_history 테이블에 자동 적재한다(db_fetch_daily_prices.py 참고).
# watchlist.csv/watchlist_prices.csv(±3% 당일 스크리너용)와는 별개 파이프라인.
# 접속 정보(url/key)는 Streamlit secrets에서 관리하므로, 이 모듈을 순수하게
# 유지하기 위해 인자로 받는다(직접 st.secrets를 읽지 않음).
# 원래 이 위로 "필터 빌더"(조건 검색 UI, 2026-08-19 신설)가 이 함수를 갖다 썼는데,
# DB에 데이터가 몇 개월치 쌓이기 전까진 의미 있는 결과가 안 나오고 조건도 나중에
# 다시 구상해서 짤 예정이라 2026-08-24에 UI+로직(run_filter_builder,
# FILTER_CONDITION_TYPES, FILTER_DIRECTIONS)을 통째로 제거함 — 재도입 시 git log에서
# 커밋 찾아서 참고할 것. 이 함수(load_watchlist_history_db)는 §6-6 "Fishing" 최초가
# 조회(get_first_day_prices_db)가 여전히 쓰고 있어서 남겨둠.
# ------------------------------------------------------------------ #
def load_watchlist_history_db(supabase_url: str, supabase_key: str) -> pd.DataFrame:
    """Supabase price_history + watchlist를 조인해서 하나의 DataFrame으로.
    반환 컬럼: 종목코드, 종목명, 섹터, 날짜, 종가, 등락률. 접속 실패/데이터 없음이면 빈 DataFrame."""
    empty = pd.DataFrame(columns=["종목코드", "종목명", "섹터", "날짜", "종가", "등락률"])
    if not supabase_url or not supabase_key:
        return empty

    headers = {"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}"}
    try:
        wl_resp = requests.get(f"{supabase_url}/rest/v1/watchlist?select=stock_code,stock_name,sector",
                                headers=headers, timeout=15)
        wl_resp.raise_for_status()
        wl = {r["stock_code"]: r for r in wl_resp.json()}

        rows, offset, page = [], 0, 1000
        while True:
            r = requests.get(
                f"{supabase_url}/rest/v1/price_history?select=stock_code,trade_date,close_price,change_pct"
                f"&order=trade_date&limit={page}&offset={offset}",
                headers=headers, timeout=20)
            r.raise_for_status()
            batch = r.json()
            rows.extend(batch)
            if len(batch) < page:
                break
            offset += page
    except Exception:
        return empty

    if not rows:
        return empty

    df = pd.DataFrame(rows)
    df["종목명"] = df["stock_code"].map(lambda c: wl.get(c, {}).get("stock_name", c))
    df["섹터"] = df["stock_code"].map(lambda c: wl.get(c, {}).get("sector") or "미분류")
    df = df.rename(columns={"stock_code": "종목코드", "trade_date": "날짜",
                             "close_price": "종가", "change_pct": "등락률"})
    return df[["종목코드", "종목명", "섹터", "날짜", "종가", "등락률"]]


# ------------------------------------------------------------------ #
# 거래량/외국인 수급 트래킹 (2026-08-24 신설) — investor_flow(종목별)/market_flow(시장
# 전체) 테이블을 조회해서 "평소보다 튀는지" 계산한다. §6-12 참고. DB엔 원시값만 쌓고
# "평균 대비"/"어제 대비" 같은 파생값은 여기서 매번 계산 — 나중에 "최근 며칠 평균"
# 기준을 사용자가 바꿀 수 있게 하려는 설계(§6-9 필터 빌더와 같은 원칙).
# ------------------------------------------------------------------ #
def load_investor_flow_db(supabase_url: str, supabase_key: str) -> pd.DataFrame:
    """investor_flow + watchlist 조인. 반환 컬럼: 종목코드, 종목명, 섹터, 날짜, 거래량,
    기관순매수, 외국인순매수, 외국인보유율. load_watchlist_history_db와 같은 패턴."""
    cols = ["종목코드", "종목명", "섹터", "날짜", "거래량", "기관순매수", "외국인순매수", "외국인보유율"]
    empty = pd.DataFrame(columns=cols)
    if not supabase_url or not supabase_key:
        return empty

    headers = {"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}"}
    try:
        wl_resp = requests.get(f"{supabase_url}/rest/v1/watchlist?select=stock_code,stock_name,sector",
                                headers=headers, timeout=15)
        wl_resp.raise_for_status()
        wl = {r["stock_code"]: r for r in wl_resp.json()}

        rows, offset, page = [], 0, 1000
        while True:
            r = requests.get(
                f"{supabase_url}/rest/v1/investor_flow?select=stock_code,trade_date,volume,"
                f"institution_net,foreign_net,foreign_pct&order=trade_date&limit={page}&offset={offset}",
                headers=headers, timeout=20)
            r.raise_for_status()
            batch = r.json()
            rows.extend(batch)
            if len(batch) < page:
                break
            offset += page
    except Exception:
        return empty

    if not rows:
        return empty

    df = pd.DataFrame(rows)
    df["종목명"] = df["stock_code"].map(lambda c: wl.get(c, {}).get("stock_name", c))
    df["섹터"] = df["stock_code"].map(lambda c: wl.get(c, {}).get("sector") or "미분류")
    df = df.rename(columns={"stock_code": "종목코드", "trade_date": "날짜", "volume": "거래량",
                             "institution_net": "기관순매수", "foreign_net": "외국인순매수",
                             "foreign_pct": "외국인보유율"})
    return df[cols]


def load_market_flow_db(supabase_url: str, supabase_key: str) -> pd.DataFrame:
    """market_flow 전체. 반환 컬럼: 시장, 날짜, 거래량, 개인순매수, 외국인순매수, 기관순매수."""
    cols = ["시장", "날짜", "거래량", "개인순매수", "외국인순매수", "기관순매수"]
    empty = pd.DataFrame(columns=cols)
    if not supabase_url or not supabase_key:
        return empty
    headers = {"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}"}
    try:
        r = requests.get(
            f"{supabase_url}/rest/v1/market_flow?select=market,trade_date,volume,"
            f"individual_net,foreign_net,institution_net&order=trade_date",
            headers=headers, timeout=15)
        r.raise_for_status()
        rows = r.json()
    except Exception:
        return empty
    if not rows:
        return empty
    df = pd.DataFrame(rows)
    df = df.rename(columns={"market": "시장", "trade_date": "날짜", "volume": "거래량",
                             "individual_net": "개인순매수", "foreign_net": "외국인순매수",
                             "institution_net": "기관순매수"})
    return df[cols]


def _latest_change_pct_map(price_hist: pd.DataFrame | None) -> dict:
    """load_watchlist_history_db()가 반환하는 형태(종목코드,...,날짜,종가,등락률)에서
    종목별 가장 최근 등락률만 뽑아 {종목코드: 등락률} dict로. Volume/Foreigner 화면에
    "그래서 오늘 이 종목 주가는 몇 % 움직였나"를 같이 보여주려는 용도(2026-08-24,
    사용자 요청 — 수급 지표만 보여주고 실제 주가 움직임이 빠져있으면 판단하기 어려우므로)."""
    if price_hist is None or price_hist.empty:
        return {}
    latest = price_hist.sort_values("날짜").groupby("종목코드").last()
    return pd.to_numeric(latest["등락률"], errors="coerce").to_dict()


def compute_volume_flags(hist: pd.DataFrame, price_hist: pd.DataFrame | None = None) -> list[dict]:
    """종목별 오늘 거래량이 그동안 쌓인 평균/어제 대비 얼마나 튀었는지.
    hist: load_investor_flow_db()가 반환하는 형태. price_hist(선택): load_watchlist_
    history_db()가 반환하는 형태 — 있으면 그날 실제 주가 등락률도 같이 붙여준다.
    데이터가 하루뿐인 종목은 비교 대상이 없으므로 제외. 반환: |오늘 vs 평균 %| 큰 순으로
    정렬된 [{"종목명","종목코드","섹터","오늘","평균","어제","vs평균pct","vs어제pct",
    "오늘등락률"}, ...]. "오늘"(원시 거래량)은 DB 원자료용으로 남겨두지만, 화면에는
    표시하지 않기로 함(2026-08-24, 사용자 요청) — 대신 vs어제pct/오늘등락률을 보여줌."""
    price_map = _latest_change_pct_map(price_hist)
    results = []
    for code, g in hist.groupby("종목코드"):
        g = g.sort_values("날짜")
        vols = pd.to_numeric(g["거래량"], errors="coerce").dropna()
        if len(vols) < 2 or vols.mean() <= 0:
            continue
        today_vol, avg_vol, yday_vol = vols.iloc[-1], vols.mean(), vols.iloc[-2]
        results.append({
            "종목명": g["종목명"].iloc[-1], "종목코드": code, "섹터": g["섹터"].iloc[-1],
            "오늘": int(today_vol), "평균": avg_vol, "어제": int(yday_vol),
            "vs평균pct": (today_vol - avg_vol) / avg_vol * 100,
            "vs어제pct": (today_vol - yday_vol) / yday_vol * 100 if yday_vol else None,
            "오늘등락률": price_map.get(code),
        })
    results.sort(key=lambda r: -abs(r["vs평균pct"]))
    return results


def compute_foreign_flags(hist: pd.DataFrame, price_hist: pd.DataFrame | None = None) -> list[dict]:
    """종목별 오늘 외국인보유율이 평균/어제 대비 얼마나 움직였는지(%p, 퍼센트포인트 차이 —
    보유율 자체가 이미 %라 상대변화율로 보면 하루 변동폭이 작아 헷갈리므로 %p로 비교).
    price_hist(선택): compute_volume_flags와 동일 용도(그날 실제 주가 등락률).
    반환: |오늘 vs 평균 %p| 큰 순으로 정렬된 [{"종목명","종목코드","섹터","오늘보유율",
    "평균보유율","어제보유율","vs평균pp","vs어제pp","오늘외국인순매수","오늘등락률"}, ...].
    "오늘외국인순매수"(원시 주식수)는 DB 원자료용으로 남겨두지만, 화면에는 표시하지
    않기로 함(2026-08-24, 사용자 요청) — 대신 vs어제pp/오늘등락률을 보여줌."""
    price_map = _latest_change_pct_map(price_hist)
    results = []
    for code, g in hist.groupby("종목코드"):
        g = g.sort_values("날짜")
        pct = pd.to_numeric(g["외국인보유율"], errors="coerce").dropna()
        if len(pct) < 2:
            continue
        today_pct, avg_pct, yday_pct = pct.iloc[-1], pct.mean(), pct.iloc[-2]
        net = pd.to_numeric(g["외국인순매수"], errors="coerce").dropna()
        results.append({
            "종목명": g["종목명"].iloc[-1], "종목코드": code, "섹터": g["섹터"].iloc[-1],
            "오늘보유율": today_pct, "평균보유율": avg_pct, "어제보유율": yday_pct,
            "vs평균pp": today_pct - avg_pct, "vs어제pp": today_pct - yday_pct,
            "오늘외국인순매수": int(net.iloc[-1]) if not net.empty else None,
            "오늘등락률": price_map.get(code),
        })
    results.sort(key=lambda r: -abs(r["vs평균pp"]))
    return results


def compute_market_flow_baseline(mkt_hist: pd.DataFrame) -> dict:
    """코스피/코스닥 시장 전체의 오늘 거래량 vs 평균(%), 오늘 외국인순매수(참고용, 억원).
    반환: {"KOSPI": {...}, "KOSDAQ": {...}} — 데이터가 하루뿐인 시장은 빠짐."""
    result = {}
    for market, g in mkt_hist.groupby("시장"):
        g = g.sort_values("날짜")
        vols = pd.to_numeric(g["거래량"], errors="coerce").dropna()
        if len(vols) < 2 or vols.mean() <= 0:
            continue
        today_vol, avg_vol = vols.iloc[-1], vols.mean()
        fnet = pd.to_numeric(g["외국인순매수"], errors="coerce").dropna()
        result[market] = {
            "오늘거래량": int(today_vol),
            "거래량vs평균pct": (today_vol - avg_vol) / avg_vol * 100,
            "오늘외국인순매수": int(fnet.iloc[-1]) if not fnet.empty else None,
            "평균외국인순매수": round(fnet.mean(), 1) if not fnet.empty else None,
        }
    return result


def fetch_quotes(codes: list[str]) -> tuple[dict, list[str]]:
    """네이버 실시간 시세 API는 한 번에 너무 많은 종목코드를 요청하면 일부만 응답하는
    경우가 있어(대략 20개 안팎에서 잘림), 20개씩 나눠서 요청한 뒤 결과를 합친다.
    반환값: (코드→시세 dict, 실패한 청크에 대한 오류 메시지 목록)"""
    codes = [c for c in codes if c]
    if not codes:
        return {}, []

    CHUNK_SIZE = 20
    result = {}
    errors = []
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}

    for i in range(0, len(codes), CHUNK_SIZE):
        chunk = codes[i:i + CHUNK_SIZE]
        url = f"https://polling.finance.naver.com/api/realtime/domestic/stock/{','.join(chunk)}"
        try:
            resp = requests.get(url, headers=headers, timeout=6)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as e:
            errors.append(f"시세 조회 실패({i + 1}~{i + len(chunk)}번째 종목): {e}")
            continue

        datas = payload.get("datas")
        if datas is None:
            try:
                datas = payload["result"]["areas"][0]["datas"]
            except Exception:
                datas = []

        for d in datas or []:
            code = str(d.get("itemCode") or d.get("cd") or d.get("code") or "").strip()
            price_raw = d.get("closePrice") or d.get("cv") or d.get("nv")
            chg_raw = d.get("fluctuationsRatio") or d.get("cr")
            vol_raw = d.get("accumulatedTradingVolume")
            if not code or price_raw is None:
                continue
            try:
                price = float(str(price_raw).replace(",", ""))
            except ValueError:
                continue
            try:
                change_pct = float(str(chg_raw).replace(",", "").replace("%", "")) if chg_raw is not None else 0.0
            except ValueError:
                change_pct = 0.0
            try:
                volume = int(str(vol_raw).replace(",", "")) if vol_raw is not None else None
            except ValueError:
                volume = None
            result[code] = {"price": price, "change_pct": change_pct, "volume": volume}

    return result, errors


def fetch_investor_flow(code: str) -> list[dict]:
    """네이버 개별종목 페이지(`/item/frgn.naver`)의 "외국인 기관 순매매 거래량" 표를 가져온다.
    실시간 시세 API(JSON)와 달리 **화면용 HTML을 그대로 긁는 것**이라 더 깨지기 쉬움 —
    네이버가 페이지 구조를 바꾸면 조용히 깨질 수 있다는 걸 알고 씀(2026-08-24, 사용자가
    거래량/외국인 수급 등락폭을 보고 싶다고 해서 도입). 그래서 실패 시 예외를 던지지 않고
    조용히 빈 리스트를 반환한다(fetch_index_quotes와 같은 패턴) — 호출부가 그날/그 종목만
    스킵하고 넘어가면 됨.

    한 번 호출로 최근 약 20영업일치가 한꺼번에 나온다 — 그래서 처음 도입할 때 매일 하루씩
    쌓일 때까지 기다릴 필요 없이 즉시 한 달 가까이 백필(backfill)할 수 있다.

    반환: [{"날짜": "YYYY-MM-DD", "거래량": int, "기관순매수": int, "외국인순매수": int,
            "외국인보유율": float}, ...] — 최근 날짜부터 순서대로(페이지가 그렇게 줌)."""
    url = f"https://finance.naver.com/item/frgn.naver?code={code}"
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        html = resp.content.decode("euc-kr", errors="replace")
    except Exception:
        return []

    def num(td):
        t = td.get_text(strip=True).replace(",", "").replace("%", "").replace("+", "")
        try:
            return float(t)
        except ValueError:
            return None

    try:
        soup = BeautifulSoup(html, "html.parser")
        table = soup.find("table", summary=lambda s: bool(s) and "순매매 거래량" in s)
        if table is None:
            return []
        rows = []
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 9:
                continue
            date_txt = tds[0].get_text(strip=True)
            if not date_txt:
                continue
            volume = num(tds[4])
            inst_net = num(tds[5])
            foreign_net = num(tds[6])
            foreign_pct = num(tds[8])
            if volume is None or inst_net is None or foreign_net is None:
                continue
            rows.append({
                "날짜": date_txt.replace(".", "-"),
                "거래량": int(volume),
                "기관순매수": int(inst_net),
                "외국인순매수": int(foreign_net),
                "외국인보유율": foreign_pct,
            })
        return rows
    except Exception:
        return []


def fetch_market_flow(market: str) -> list[dict]:
    """코스피/코스닥 "시장 전체"의 일별 거래량 + 투자자별(개인/외국인/기관) 순매수.
    개별 종목의 거래량/수급이 그날 유난히 튀었는지 판단하려면 "평소 이 종목" 기준뿐 아니라
    "그날 시장 전체" 기준도 있어야 비교가 되므로 둔 베이스라인(2026-08-24, 사용자 요청) —
    코스닥 종목은 코스닥과, 코스피 종목은 코스피와 비교해야 기준이 맞아서 시장별로 따로
    저장한다. fetch_investor_flow와 같은 이유로 비공식 HTML 스크레이핑이라 실패하면
    조용히 빈 리스트를 반환한다.

    market: "KOSPI" 또는 "KOSDAQ".
    반환: [{"날짜": "YYYY-MM-DD", "거래량": int(천주) | None, "개인순매수": int(억원) | None,
            "외국인순매수": int(억원) | None, "기관순매수": int(억원) | None}, ...]
    거래량은 지수 일별시세 페이지, 순매수는 투자자별 매매동향 페이지 — 서로 다른 두 페이지를
    가져와 날짜 기준으로 합친다(한쪽만 있으면 다른 쪽 필드는 None)."""
    sosok = "02" if market == "KOSDAQ" else ""
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}

    def num(td):
        t = td.get_text(strip=True).replace(",", "")
        try:
            return int(t)
        except ValueError:
            return None

    volumes = {}
    try:
        resp = requests.get(f"https://finance.naver.com/sise/sise_index_day.naver?code={market}",
                             headers=headers, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content.decode("euc-kr", errors="replace"), "html.parser")
        for tr in soup.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 6 or "date" not in (tds[0].get("class") or []):
                continue
            date_txt = tds[0].get_text(strip=True)
            vol = num(tds[4])
            if date_txt and vol is not None:
                volumes[date_txt.replace(".", "-")] = vol
    except Exception:
        pass

    flows = {}
    try:
        today = today_kst_str().replace("-", "")
        resp = requests.get(
            f"https://finance.naver.com/sise/investorDealTrendDay.naver?bizdate={today}&sosok={sosok}",
            headers=headers, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content.decode("euc-kr", errors="replace"), "html.parser")
        for tr in soup.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 4 or "date2" not in (tds[0].get("class") or []):
                continue
            date_txt = tds[0].get_text(strip=True)  # "26.08.24" (2자리 연도)
            if not date_txt:
                continue
            date_iso = "20" + date_txt.replace(".", "-")
            flows[date_iso] = {
                "개인순매수": num(tds[1]), "외국인순매수": num(tds[2]), "기관순매수": num(tds[3]),
            }
    except Exception:
        pass

    rows = []
    for d in sorted(set(volumes) | set(flows), reverse=True):
        f = flows.get(d, {})
        rows.append({
            "날짜": d, "거래량": volumes.get(d),
            "개인순매수": f.get("개인순매수"), "외국인순매수": f.get("외국인순매수"),
            "기관순매수": f.get("기관순매수"),
        })
    return rows


def fetch_index_quotes() -> dict:
    """코스피/코스닥 지수 실시간 조회. 반환: {"KOSPI": {"price","change","change_pct"}, "KOSDAQ": {...}}."""
    url = "https://polling.finance.naver.com/api/realtime/domestic/index/KOSPI,KOSDAQ"
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}
    try:
        resp = requests.get(url, headers=headers, timeout=6)
        resp.raise_for_status()
        payload = resp.json()
    except Exception:
        return {}

    result = {}
    for d in payload.get("datas") or []:
        code = d.get("itemCode")
        if code not in ("KOSPI", "KOSDAQ"):
            continue
        try:
            price = float(d.get("closePriceRaw"))
            change = float(d.get("compareToPreviousClosePriceRaw"))
            change_pct = float(d.get("fluctuationsRatioRaw"))
        except (TypeError, ValueError):
            continue
        result[code] = {"price": price, "change": change, "change_pct": change_pct}
    return result


def refresh_all_prices(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """보유종목 시세를 전부 새로고침. 반환값: (갱신된 df, 진단 리포트 dict)."""
    df = df.copy()
    code_cache = load_code_cache()
    unresolved = []
    newly_resolved = {}
    for i, row in df.iterrows():
        code = clean_str(row.get("종목코드", ""))
        if not code or code.lower() == "nan":
            found = resolve_code(row["종목명"], code_cache)
            if found:
                df.loc[i, "종목코드"] = found
                newly_resolved[row["종목명"]] = found
            else:
                unresolved.append(row["종목명"])
    if newly_resolved:
        update_code_cache(newly_resolved)

    codes = [clean_str(c) for c in df["종목코드"].tolist()]
    codes = [c for c in codes if c and c.lower() != "nan"]
    quotes, quote_errors = fetch_quotes(codes)

    now = now_kst_str()
    updated, failed = 0, []
    for i, row in df.iterrows():
        code = clean_str(row.get("종목코드", ""))
        if code and code in quotes:
            df.loc[i, "현재가"] = quotes[code]["price"]
            df.loc[i, "등락률"] = quotes[code]["change_pct"]
            df.loc[i, "업데이트시각"] = now
            updated += 1
        elif code:
            failed.append(row["종목명"])

    save_holdings(df)
    report = {"updated": updated, "unresolved": unresolved, "failed": failed, "quote_errors": quote_errors}
    return df, report


def get_current_prices_for_names(names: list[str]) -> dict:
    """종목명 리스트의 현재가 조회. 보유 여부와 무관 — 청산된 종목 추적용."""
    code_cache = load_code_cache()
    name_to_code = {}
    newly_resolved = {}
    for n in names:
        code = resolve_code(n, code_cache)
        if code:
            name_to_code[n] = code
            if n not in code_cache:
                newly_resolved[n] = code
    if newly_resolved:
        update_code_cache(newly_resolved)
    quotes, _ = fetch_quotes(list(name_to_code.values()))
    result = {}
    for n, code in name_to_code.items():
        if code in quotes:
            result[n] = quotes[code]["price"]
    return result


def get_closed_out_last_sells(holdings_df: pd.DataFrame, tx_df: pd.DataFrame) -> pd.DataFrame:
    """현재 보유 중이 아닌(=완전히 매도한) 종목들의 마지막 매도일/매도가."""
    sell_tx = tx_df[tx_df["구분"] == "매도"].copy()
    if sell_tx.empty:
        return pd.DataFrame(columns=["종목명", "매도일", "매도가"])
    sell_tx["단가"] = pd.to_numeric(sell_tx["단가"], errors="coerce")
    held_names = set(holdings_df["종목명"].tolist())
    sell_tx = sell_tx[~sell_tx["종목명"].isin(held_names)]
    sell_tx = sell_tx[sell_tx["단가"].notna() & (sell_tx["단가"] > 0)]
    if sell_tx.empty:
        return pd.DataFrame(columns=["종목명", "매도일", "매도가"])
    sell_tx = sell_tx.sort_values(["종목명", "날짜", "id"])
    last = sell_tx.groupby("종목명", as_index=False).tail(1)[["종목명", "날짜", "단가"]]
    last = last.rename(columns={"날짜": "매도일", "단가": "매도가"})
    return last.reset_index(drop=True)


# ------------------------------------------------------------------ #
# 종목별 보유현황 카드 클릭 시 상세(매수/매도 내역 + "물타기 적정성" 그래프, 2026-08-21 신설)
# ------------------------------------------------------------------ #
def _current_cycle_transactions(tx: pd.DataFrame, name: str) -> pd.DataFrame:
    """그 종목의 거래 중 "현재 보유 사이클"(마지막으로 전량매도해서 보유수량이 0이 된
    시점 이후 ~ 지금)에 해당하는 것만 남긴다. 예: 10000원에 사서 8500원까지 물타다 9000원에
    전량매도(1번째 사이클 종료) 후, 나중에 5000원에 재진입한 상태라면 1번째 사이클 거래는
    전부 제외하고 두 번째 사이클(5000원 매수 이후)만 반환 — 안 그러면 서로 다른 사이클의
    평단가/진입가가 뒤섞여서 "지금 물타기가 적절한가"를 알 수 없게 된다(2026-08-24, 사용자
    요청으로 도입).
    정렬은 rebuild_portfolio_from_transactions와 동일하게 "날짜 → 같은 날짜 내 원래 입력순"
    (입력순 = tx 안에서의 행 순서, 신규 거래는 항상 끝에 append되므로 이 순서가 곧 입력순)."""
    t = tx[tx["종목명"] == name].copy()
    if t.empty:
        return t
    t["수량"] = pd.to_numeric(t["수량"], errors="coerce").fillna(0)
    t["_ord"] = t.index
    t = t.sort_values(["날짜", "_ord"]).reset_index(drop=True)
    signed_qty = t["수량"].where(t["구분"] == "매수", -t["수량"])
    cum_qty = signed_qty.cumsum()
    zero_points = cum_qty[cum_qty.abs() < 1e-6]
    if not zero_points.empty:
        t = t.iloc[zero_points.index[-1] + 1:]
    return t.drop(columns="_ord").reset_index(drop=True)


def get_holding_trade_summary_all_time(tx: pd.DataFrame, name: str) -> dict:
    """그 종목의 전체 매매 이력(사이클 구분 없이, 과거에 완전히 청산했던 사이클까지 전부
    포함한 누적) 매수/매도 건수·누적금액·실현손익 합계. 지금까지 이 종목으로 총 얼마
    벌고 잃었는지 트래킹하려는 목적(2026-08-24, 사용자 요청) — get_holding_trade_summary는
    "현재 사이클"만 보여줘서 이전에 청산했던 사이클의 실현손익이 안 보이므로, 이 함수를
    별도로 둬서 "누적"과 "현재 사이클" 둘 다 화면에 같이 보여준다."""
    t = tx[tx["종목명"] == name].copy()
    t["수량"] = pd.to_numeric(t["수량"], errors="coerce").fillna(0)
    t["단가"] = pd.to_numeric(t["단가"], errors="coerce").fillna(0)
    buys = t[t["구분"] == "매수"]
    sells = t[t["구분"] == "매도"]
    return {
        "buy_count": int(len(buys)),
        "buy_amount": float((buys["수량"] * buys["단가"]).sum()),
        "sell_count": int(len(sells)),
        "sell_amount": float((sells["수량"] * sells["단가"]).sum()),
        "realized_pnl": float(pd.to_numeric(sells["실현손익"], errors="coerce").fillna(0).sum()),
    }


def get_holding_trade_summary(tx: pd.DataFrame, name: str) -> dict:
    """현재 보유 사이클(전량매도 후 재진입했다면 그 이후만)의 매수/매도 건수·누적금액·
    실현손익 합계. 평단가는 여기서 다루지 않음 — 이미 holdings(portfolio_data.csv)에
    정확히 계산돼있는 값을 그대로 쓸 것(매도가 껴있어도 apply_transaction이 순서대로
    재생하며 정확히 계산하므로, 여기서 매수 총액/총수량으로 단순 재평균하면 틀림 —
    예: 2주@1000원 매수 후 1주 매도, 다시 1주@900원 매수하면 평단가는 950원이지,
    (2000+900)/3=966원이 아님)."""
    t = _current_cycle_transactions(tx, name)
    t["단가"] = pd.to_numeric(t["단가"], errors="coerce").fillna(0)
    buys = t[t["구분"] == "매수"]
    sells = t[t["구분"] == "매도"]
    return {
        "buy_count": int(len(buys)),
        "buy_amount": float((buys["수량"] * buys["단가"]).sum()),
        "sell_count": int(len(sells)),
        "sell_amount": float((sells["수량"] * sells["단가"]).sum()),
        "realized_pnl": float(pd.to_numeric(sells["실현손익"], errors="coerce").fillna(0).sum()),
    }


def get_holding_trade_points(tx: pd.DataFrame, name: str) -> pd.DataFrame:
    """현재 보유 사이클(전량매도 후 재진입했다면 그 이후만)의 매수/매도 거래를 날짜순으로.
    반환 컬럼: 날짜, 구분, 단가, 수량. "물타기 적정성" 그래프에서 매수/매도 시점을 점으로
    찍는 데 씀."""
    t = _current_cycle_transactions(tx, name)
    t["단가"] = pd.to_numeric(t["단가"], errors="coerce")
    return t[["날짜", "구분", "단가", "수량"]].reset_index(drop=True)


def get_holding_avg_price_path(tx: pd.DataFrame, name: str) -> pd.DataFrame:
    """현재 보유 사이클에서 매수할 때마다 평단가가 어떻게 바뀌었는지(계단식) 반환.
    반환 컬럼: 날짜, 평단가 — 매수 시점에만 값이 있음(apply_transaction과 동일하게 매도는
    평단가에 영향을 주지 않으므로). 그래프에서 line_shape='hv'로 그리면 매수와 매수
    사이 구간은 자동으로 평평하게 이어져서 "얼마에 사서 다음 매수 전까지 평단가가
    유지되다가, 사면 계단처럼 바뀌는" 모양이 된다(2026-08-24 신설)."""
    t = _current_cycle_transactions(tx, name)
    if t.empty:
        return pd.DataFrame(columns=["날짜", "평단가"])
    t["단가"] = pd.to_numeric(t["단가"], errors="coerce").fillna(0)
    qty = 0.0
    avg = 0.0
    points = []
    for _, row in t.iterrows():
        if row["구분"] == "매수":
            new_qty = qty + row["수량"]
            avg = (qty * avg + row["수량"] * row["단가"]) / new_qty if new_qty else 0.0
            qty = new_qty
            points.append((row["날짜"], avg))
        else:
            qty -= row["수량"]
    return pd.DataFrame(points, columns=["날짜", "평단가"])


# ------------------------------------------------------------------ #
# 지표 계산
# ------------------------------------------------------------------ #
def compute_metrics(df: pd.DataFrame, cash: float):
    df = df.copy()
    for col in ("수량", "평단가", "현재가", "등락률"):
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    df["섹터"] = df["섹터"].replace("", "미분류").fillna("미분류")

    df["평가금액"] = df["수량"] * df["현재가"]
    df["매입금액"] = df["수량"] * df["평단가"]
    df["손익"] = df["평가금액"] - df["매입금액"]
    df["손익률"] = df.apply(lambda r: (r["손익"] / r["매입금액"] * 100) if r["매입금액"] else 0, axis=1)

    stock_valuation = df["평가금액"].sum()
    total_assets = stock_valuation + cash
    df["비중"] = df["평가금액"].apply(lambda v: (v / total_assets * 100) if total_assets else 0)

    unrealized_loss = -df.loc[df["손익"] < 0, "손익"].sum()
    return df, stock_valuation, total_assets, unrealized_loss


def compute_sector_weights(df: pd.DataFrame) -> dict:
    """섹터그룹별 비중(%). 주식 평가금액 총합 대비이며 예수금은 포함하지 않음."""
    if df.empty:
        return {}
    d = df.copy()
    d["섹터그룹"] = d["섹터"].apply(group_sector)
    stock_total = d["평가금액"].sum()
    if stock_total <= 0:
        return {}
    grp = d.groupby("섹터그룹")["평가금액"].sum()
    return (grp / stock_total * 100).to_dict()


# ------------------------------------------------------------------ #
# 거래 반영 (매수/매도) — holdings는 이 함수를 거쳐서만 바뀐다
# ------------------------------------------------------------------ #
def apply_transaction(holdings: pd.DataFrame, state: dict, name: str, kind: str, qty: float, price: float,
                       code_cache: dict | None = None, sector_cache: dict | None = None,
                       fee_rate: float = 0.0):
    """fee_rate: 매수/매도 대금 대비 수수료+세금 추정 비율 (예: 0.000579 = 0.0579%).
    매수/매도 구분 없이 거래대금에 균일하게 적용하는 근사치 — 실제로는 매도 쪽에
    거래세가 더 붙어서 비대칭이지만, 그걸 나눠볼 데이터가 없어 우선 평균값으로 적용."""
    holdings = holdings.copy()
    realized = None
    match = holdings.index[holdings["종목명"] == name]
    fee = qty * price * fee_rate

    if kind == "매수":
        cost = qty * price
        state["cash"] -= (cost + fee)
        if len(match):
            i = match[0]
            old_qty = float(holdings.loc[i, "수량"])
            old_avg = float(holdings.loc[i, "평단가"])
            new_qty = old_qty + qty
            new_avg = (old_qty * old_avg + cost) / new_qty if new_qty else 0
            holdings.loc[i, "수량"] = new_qty
            holdings.loc[i, "평단가"] = new_avg
            if not holdings.loc[i, "현재가"] or float(holdings.loc[i, "현재가"]) == 0:
                holdings.loc[i, "현재가"] = price
        else:
            new_row = {c: "" for c in HOLD_COLUMNS}
            new_row.update({
                "종목명": name,
                "종목코드": (code_cache or {}).get(name, ""),
                "섹터": (sector_cache or {}).get(name, "미분류"),
                "수량": qty, "평단가": price, "현재가": price,
                "등락률": 0, "업데이트시각": now_kst_str(),
            })
            holdings = pd.concat([holdings, pd.DataFrame([new_row])], ignore_index=True)
    else:  # 매도
        proceeds = qty * price
        state["cash"] += (proceeds - fee)
        if len(match):
            i = match[0]
            old_qty = float(holdings.loc[i, "수량"])
            old_avg = float(holdings.loc[i, "평단가"])
            realized = (price - old_avg) * qty
            new_qty = old_qty - qty
            if new_qty <= 0:
                holdings = holdings.drop(index=i).reset_index(drop=True)
            else:
                holdings.loc[i, "수량"] = new_qty
        else:
            realized = 0.0

    return holdings, state, realized


def _sort_tx_for_replay(tx: pd.DataFrame) -> pd.DataFrame:
    """"날짜 → 같은 날짜 내 원래 입력순"으로 정렬(§1-1). rebuild_portfolio_from_transactions와
    rebuild_portfolio_incremental이 공유 — 정렬 기준이 두 곳에서 갈라지면 같은 거래가
    함수에 따라 다른 순서로 재생돼서 평단가가 어긋나는 버그가 생길 수 있다."""
    tx_sorted = tx.copy().reset_index(drop=True)
    tx_sorted["_ord"] = range(len(tx_sorted))
    return tx_sorted.sort_values(["날짜", "_ord"]).reset_index(drop=True)


def _replay_transactions(holdings: pd.DataFrame, state: dict, tx_sorted: pd.DataFrame,
                          code_cache: dict, sector_cache: dict, fee_rate: float):
    """이미 정렬된(_sort_tx_for_replay) tx_sorted를 순서대로 하나씩 재생하며 holdings/state를
    갱신하는 핵심 루프. rebuild_portfolio_from_transactions(전체 재생)와
    rebuild_portfolio_incremental(체크포인트+최근분만 재생)이 이 함수 하나를 공유한다 —
    "holdings/거래 관련 계산은 한 곳에만 둔다"는 파일 상단 원칙 그대로, 이 루프를 복제해서
    따로 구현하지 말 것(두 벌이 되면 계산이 서로 어긋나는 버그가 생기기 쉬움).
    반환: (holdings, state, {매도 거래 id: 실현손익})."""
    realized_map = {}
    for _, row in tx_sorted.iterrows():
        name = row["종목명"]
        kind = row["구분"]
        qty = float(row["수량"])
        price = float(row["단가"])
        holdings, state, realized = apply_transaction(holdings, state, name, kind, qty, price,
                                                        code_cache, sector_cache, fee_rate)
        if kind == "매도":
            realized_map[row["id"]] = realized if realized is not None else 0.0
    return holdings, state, realized_map


def _apply_prior_prices(holdings: pd.DataFrame, prior_holdings: pd.DataFrame | None) -> pd.DataFrame:
    """prior_holdings(replay 직전의 실시간 시세 스냅샷)의 현재가/등락률/업데이트시각을
    이름 기준으로 이어붙인다 — 거래 기록만으로는 알 수 없는 정보라서 매매일지를 반영할
    때마다 오늘 거래하지 않은 종목들의 손익률이 매수가 기준으로 리셋되는 걸 막는다."""
    if prior_holdings is None or prior_holdings.empty:
        return holdings
    prior = prior_holdings.drop_duplicates("종목명").set_index("종목명")
    for i, row in holdings.iterrows():
        name = row["종목명"]
        if name in prior.index:
            prev_price = float(prior.loc[name, "현재가"])
            if prev_price > 0:
                holdings.loc[i, "현재가"] = prev_price
                holdings.loc[i, "등락률"] = prior.loc[name, "등락률"]
                holdings.loc[i, "업데이트시각"] = prior.loc[name, "업데이트시각"]
    return holdings


def _stamp_realized(tx: pd.DataFrame, realized_map: dict) -> pd.DataFrame:
    """realized_map({거래id: 실현손익})을 tx 사본의 "실현손익" 컬럼에 채워 넣는다.
    이 함수를 거치지 않은(이번에 재생 안 된) 행은 그대로 둔다 — rebuild_portfolio_incremental이
    체크포인트 이전 구간의 이미 정확히 저장돼있던 실현손익 값을 안 건드리기 위해 이렇게 분리함."""
    tx = tx.copy()
    # "실현손익" 컬럼은 매수 행은 빈 문자열, 매도 행은 숫자를 담아야 하는 혼합 타입 컬럼인데,
    # pandas 3.0부터는 전부 빈 문자열인 컬럼을 Arrow 기반 문자열 dtype으로 추론해버려서
    # 숫자를 대입하면 TypeError가 난다(CI에서 pandas 3.0.5로 처음 실제로 겪음, 2026-08-24).
    # object dtype으로 미리 못박아서 문자열/숫자 혼용을 허용한다.
    tx["실현손익"] = tx["실현손익"].astype(object)
    for tid, val in realized_map.items():
        tx.loc[tx["id"] == tid, "실현손익"] = val
    return tx


def rebuild_portfolio_from_transactions(tx: pd.DataFrame, initial_capital: float, fee_rate: float = 0.0,
                                         prior_holdings: pd.DataFrame | None = None):
    """transactions.csv 전체를 날짜순(같은 날짜 내에서는 원래 입력순)으로 처음부터 재생하여
    holdings/state/실현손익을 다시 계산 — "정답"을 계산하는 기준(ground truth) 함수.
    holdings는 이 함수(및 apply_transaction)를 통해서만 파생되는 결과물로 취급하고,
    절대 손으로 고치지 않는다 — 다음 재계산 때 덮어써지기 때문.
    fee_rate는 거래대금 대비 수수료+세금 추정 비율 — 매 거래마다 적용되며,
    state["fee_rate"]에 담겨 반환되어 이후에도 계속 같은 비율로 재사용된다.

    실제 반영에는 매번 처음부터 전체를 재생하지 않는 rebuild_portfolio_incremental()을 쓴다
    (ingest_daily.py 참고) — 이 함수는 그 결과가 항상 맞는지 비교할 기준으로, 그리고
    테스트에서 계속 쓰인다."""
    holdings = pd.DataFrame(columns=HOLD_COLUMNS)
    state = {"cash": initial_capital, "initial": initial_capital, "fee_rate": fee_rate}

    if tx.empty:
        return holdings, state, tx

    code_cache = load_code_cache()
    sector_cache = load_sector_cache()
    tx_sorted = _sort_tx_for_replay(tx)

    holdings, state, realized_map = _replay_transactions(
        holdings, state, tx_sorted, code_cache, sector_cache, fee_rate)
    holdings = _apply_prior_prices(holdings, prior_holdings)
    tx = _stamp_realized(tx, realized_map)

    return holdings, state, tx


def load_checkpoint() -> tuple[pd.DataFrame, dict | None, str | None]:
    """저장된 체크포인트(holdings, state, 그 시점 날짜)를 불러온다. 없으면
    (빈 holdings, None, None) — rebuild_portfolio_incremental이 "처음 실행"으로 처리한다."""
    if not CHECKPOINT_STATE_FILE.exists():
        return pd.DataFrame(columns=HOLD_COLUMNS), None, None
    state_df = pd.read_csv(CHECKPOINT_STATE_FILE)
    if state_df.empty:
        return pd.DataFrame(columns=HOLD_COLUMNS), None, None
    row = state_df.iloc[0]
    state = {"cash": float(row["예수금"]), "initial": float(row["초기자본"]),
              "fee_rate": float(row["fee_rate"])}
    ckpt_date = str(row["체크포인트날짜"])
    if CHECKPOINT_HOLDINGS_FILE.exists():
        holdings = pd.read_csv(CHECKPOINT_HOLDINGS_FILE, dtype={"종목코드": str},
                                keep_default_na=False, na_values=[""])
    else:
        holdings = pd.DataFrame(columns=HOLD_COLUMNS)
    return holdings, state, ckpt_date


def save_checkpoint(holdings: pd.DataFrame, state: dict, ckpt_date: str) -> None:
    holdings.to_csv(CHECKPOINT_HOLDINGS_FILE, index=False)
    pd.DataFrame([{
        "체크포인트날짜": ckpt_date, "예수금": state["cash"],
        "초기자본": state["initial"], "fee_rate": state.get("fee_rate", 0.0),
    }]).to_csv(CHECKPOINT_STATE_FILE, index=False)


def rebuild_portfolio_incremental(tx: pd.DataFrame, initial_capital: float, fee_rate: float = 0.0,
                                   prior_holdings: pd.DataFrame | None = None,
                                   safety_days: int = 3, today: str | None = None):
    """rebuild_portfolio_from_transactions과 최종 결과가 항상 같아야 하지만(불변식 —
    tests/test_portfolio_core.py에서 둘을 직접 비교해서 검증함), 매번 transactions.csv
    전체를 처음부터 재생하지 않는다. 대신 "체크포인트"(오늘로부터 safety_days일보다 오래돼서
    다시 안 바뀔 게 확실한 구간까지의 holdings/현금 계산 결과)를 파일로 저장해뒀다가,
    다음 반영부터는 체크포인트 이후 거래만 재생한다.

    **왜 safety_days만큼은 체크포인트로 확정 안 하는가**: §1-2 원칙(증권사 CSV는 그날 하루
    전체 누적이라, 같은 날짜를 나중에 다시 반영하면 그날 거래가 통째로 교체됨) 때문에,
    최근 며칠은 아직 재업로드로 바뀔 수 있다. 그래서 "확실히 다시 안 바뀔 구간"까지만
    체크포인트로 확정하고, 최근 safety_days일치는 매번 처음부터(체크포인트 위에서) 다시
    재생한다 — 이 구간은 거래 건수가 며칠 치뿐이라 매번 재생해도 비용이 작다.

    지금(거래 몇백 건)은 이렇게 해도 체감 성능 이득이 없지만, 거래가 몇 년 치 쌓여
    수천~수만 건이 되면 "매번 처음부터 전체 재생"은 반영할 때마다 점점 느려지는 구조라
    (db_fetch_daily_prices.py의 append-only 패턴과 대조적으로, 지금까지는 holdings를
    매번 새로 계산했음). 거래가 적은 지금부터 이 경로로만 반영하게 해서, 나중에 거래가
    많이 쌓였을 때 "처음 실행되는 낯선 코드"가 아니라 "이미 매일 검증되고 있던 코드"가
    되게 하려는 의도(2026-08-25, 사용자 요청 — 체감 이득이 없어도 부담이 안 되면 지금부터
    실제 경로로 써서 다듬어가자는 방향)."""
    holdings = pd.DataFrame(columns=HOLD_COLUMNS)
    state = {"cash": initial_capital, "initial": initial_capital, "fee_rate": fee_rate}

    if tx.empty:
        return holdings, state, tx

    today = today or today_kst_str()
    cutoff = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=safety_days)).strftime("%Y-%m-%d")

    code_cache = load_code_cache()
    sector_cache = load_sector_cache()
    tx_sorted = _sort_tx_for_replay(tx)

    ckpt_holdings, ckpt_state, ckpt_date = load_checkpoint()
    if ckpt_state is None:
        ckpt_state = {"cash": initial_capital, "initial": initial_capital, "fee_rate": fee_rate}
    # 체크포인트가 저장된 시점 이후로 initial_capital/fee_rate 자체가 바뀌었을 가능성에
    # 대비해, 이번 호출의 값으로 덮어써서 항상 최신 기준을 따르게 함.
    ckpt_state["initial"] = initial_capital
    ckpt_state["fee_rate"] = fee_rate

    if ckpt_date is None:
        fold_mask = tx_sorted["날짜"] <= cutoff
    else:
        fold_mask = (tx_sorted["날짜"] > ckpt_date) & (tx_sorted["날짜"] <= cutoff)
    to_fold = tx_sorted[fold_mask]
    tail = tx_sorted[tx_sorted["날짜"] > cutoff]

    realized_map = {}
    cur_holdings, cur_state = ckpt_holdings.copy(), dict(ckpt_state)

    if not to_fold.empty:
        cur_holdings, cur_state, fold_realized = _replay_transactions(
            cur_holdings, cur_state, to_fold, code_cache, sector_cache, fee_rate)
        realized_map.update(fold_realized)
        save_checkpoint(cur_holdings, cur_state, cutoff)

    final_holdings, final_state, tail_realized = _replay_transactions(
        cur_holdings.copy(), dict(cur_state), tail, code_cache, sector_cache, fee_rate)
    realized_map.update(tail_realized)

    final_holdings = _apply_prior_prices(final_holdings, prior_holdings)
    tx = _stamp_realized(tx, realized_map)

    return final_holdings, final_state, tx


# ------------------------------------------------------------------ #
# 증권사 "일일 매매일지" CSV 파싱 + 반영
# 형식: 종목별 1행, [상세, 종목명, 잔고구분, 잔고수량, 잔고평균단가,
#      금일매수평균가, 금일매수수량, 금일매수매입금액,
#      금일매도평균가, 금일매도수량, 금일매도매도금액,
#      수수료+제세금, 실현손익(증권사 계산), 손익률(%), ...] 순서의 위치 기반 파싱.
# 컬럼명이 병합헤더+개행문자라 이름으로 읽는 게 불안정해서 열 순서로 읽는다.
# ------------------------------------------------------------------ #
def parse_daily_trade_csv(raw: bytes) -> pd.DataFrame:
    text = None
    for enc in ("cp949", "utf-8-sig", "utf-8"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("CSV 인코딩을 인식할 수 없습니다 (cp949/utf-8 모두 실패).")

    df = pd.read_csv(io.StringIO(text))
    if len(df) < 2 or df.shape[1] < 13:
        raise ValueError("예상한 일일 매매일지 형식이 아닙니다 (열 개수 부족).")

    # 첫 데이터 행은 '평균가/수량/매입금액...' 서브헤더이므로 건너뜀
    df = df.iloc[1:].reset_index(drop=True)
    c = df.columns.tolist()

    def num(series):
        return pd.to_numeric(
            series.astype(str).str.replace(",", "").str.strip(), errors="coerce"
        ).fillna(0)

    out = pd.DataFrame({
        "종목명": df[c[1]].astype(str).str.strip(),
        "매수평균가": num(df[c[5]]),
        "매수수량": num(df[c[6]]),
        "매도평균가": num(df[c[8]]),
        "매도수량": num(df[c[9]]),
        "실현손익_증권사": num(df[c[12]]),
    })
    out = out[out["종목명"].notna() & (out["종목명"] != "") & (out["종목명"].str.lower() != "nan")]
    return out.reset_index(drop=True)


def import_daily_trades(parsed: pd.DataFrame, tx: pd.DataFrame, trade_date: str):
    """해당 날짜의 매매일지를 반영.
    증권사 CSV는 '그날 하루 전체 누적' 내역이므로, 같은 날짜에 이미 이 방식으로 반영된
    거래가 있으면 전부 지우고 이번 업로드분으로 교체한다 (누적 방지 — 같은 날짜를
    여러 번 다시 반영해도 중복되지 않게).
    반환값: (교체된 tx, 새로 추가된 거래 수, 이번에 교체(삭제)된 이전 거래 수)"""
    tx = tx.copy()
    prior_mask = (tx["날짜"] == trade_date) & (tx["메모"] == DAILY_IMPORT_TAG)
    replaced_count = int(prior_mask.sum())
    tx = tx[~prior_mask].reset_index(drop=True)

    new_rows = []
    for _, r in parsed.iterrows():
        name = r["종목명"]
        if r["매수수량"] > 0 and r["매수평균가"] > 0:
            new_rows.append({
                "id": str(uuid.uuid4())[:8], "날짜": trade_date, "종목명": name, "구분": "매수",
                "수량": r["매수수량"], "단가": r["매수평균가"], "실현손익": "",
                "메모": DAILY_IMPORT_TAG, "정산반영": True,
            })
        if r["매도수량"] > 0 and r["매도평균가"] > 0:
            new_rows.append({
                "id": str(uuid.uuid4())[:8], "날짜": trade_date, "종목명": name, "구분": "매도",
                "수량": r["매도수량"], "단가": r["매도평균가"], "실현손익": "",
                "메모": DAILY_IMPORT_TAG, "정산반영": True,
            })
    if new_rows:
        tx = pd.concat([tx, pd.DataFrame(new_rows)], ignore_index=True)

    return tx, len(new_rows), replaced_count


# ------------------------------------------------------------------ #
# 종목별 체결내역 CSV 파싱 (dg.csv / lps 폴더 형식)
# 형식: 종목 하나당 파일 하나, 체결(fill) 단위 원자적 기록.
# 컬럼: 체결일자, 주문번호, 체결번호, 체결시각, 대출구분, 주문구분(매수/매도), 수량, 단가, 체결금액
# ------------------------------------------------------------------ #
def parse_execution_log_csv(raw: bytes, stock_name: str) -> pd.DataFrame:
    text = None
    for enc in ("cp949", "utf-8-sig", "utf-8"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError(f"{stock_name}: CSV 인코딩을 인식할 수 없습니다 (cp949/utf-8 모두 실패).")

    df = pd.read_csv(io.StringIO(text), dtype=str)
    required = {"체결일자", "체결시각", "주문구분", "수량", "단가"}
    if not required.issubset(df.columns):
        raise ValueError(f"{stock_name}: 예상한 체결내역 형식이 아닙니다 (필요한 컬럼 없음: "
                          f"{required - set(df.columns)}).")

    out = pd.DataFrame({
        "날짜": df["체결일자"].astype(str).str.strip(),
        "시각": df["체결시각"].astype(str).str.strip(),
        "종목명": stock_name,
        "구분": df["주문구분"].astype(str).str.strip(),
        "수량": pd.to_numeric(df["수량"].astype(str).str.replace(",", ""), errors="coerce"),
        "단가": pd.to_numeric(df["단가"].astype(str).str.replace(",", ""), errors="coerce"),
    })
    out = out[out["구분"].isin(["매수", "매도"])]
    out = out[out["수량"].notna() & out["단가"].notna() & (out["수량"] > 0) & (out["단가"] > 0)]
    return out.reset_index(drop=True)
