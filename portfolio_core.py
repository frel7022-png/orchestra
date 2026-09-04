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

import ast
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
INDEX_HISTORY_FILE = HERE / "index_history.csv"  # 날짜별 코스피/코스닥 종가 (지수 대비 계좌 그래프용, §6-17)
CODE_CACHE_FILE = HERE / "stock_code_cache.csv"
SECTOR_CACHE_FILE = HERE / "stock_sector_cache.csv"
MARKET_CACHE_FILE = HERE / "stock_market_cache.csv"  # 종목명→KOSPI/KOSDAQ, §1-3 영구 캐시(§6-17 물타기 성적표에서 종목별 지수 비교용)
DIVIDEND_CACHE_FILE = HERE / "dividend_cache.csv"  # 종목코드→배당수익률, 하루 1회만 재조회(아래 refresh_dividend_yields 참고)
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


def resolve_trading_date() -> str:
    """"오늘 날짜"가 아니라 "이 시점이 대표하는 거래일"을 반환한다. daily-price-fetch.yml
    cron이 GitHub 스케줄 지연으로 자정을 넘겨서 돌면(예: 16:13 KST 목표가 다음날 00:05
    KST에 실행), `today_kst_str()`을 그대로 trade_date로 쓰면 실제로는 전날 종가인데
    다음날 날짜로 잘못 찍힌다 — 2026-09-01에 실제로 발견함(8/28 종가가 "8/29"로, 8/31
    종가가 "9/1"로 Supabase price_history에 잘못 저장돼있었음, §6-16 참고). 장 시작 전
    (오전 9시 이전)에 실행되면 "오늘"이 아니라 "직전 거래일"로 보정한다.
    한계: 이 보정은 "자정 넘겨 지연"만 잡는다 — 만약 지연이 다음날 오전 9시를 넘길
    정도로 극단적이면 이 휴리스틱으로는 못 잡는다(지금까지 관측된 지연은 전부 자정~
    오전 7시 사이였음)."""
    d = now_kst().date()
    if now_kst().hour < 9:
        d -= timedelta(days=1)
    while d.weekday() >= 5:  # 토(5)/일(6)이면 가장 최근 평일로 보정
        d -= timedelta(days=1)
    return d.strftime("%Y-%m-%d")


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


def load_market_cache() -> dict:
    """종목명 → "KOSPI" | "KOSDAQ". stock_code_cache.csv/stock_sector_cache.csv와 같은
    §1-3 "최초 1회만 조회, 그 뒤로는 영구 재사용" 캐시(§6-17 물타기 성적표에서 종목별로
    자기 시장 지수와 비교하려고 둠 — 상장시장은 바뀌지 않으므로 새로고침마다 다시 안 긁음)."""
    if MARKET_CACHE_FILE.exists():
        df = pd.read_csv(MARKET_CACHE_FILE, dtype=str, keep_default_na=False)
        return {k: v for k, v in zip(df["종목명"], df["시장"]) if k and v}
    return {}


def update_market_cache(new_entries: dict) -> None:
    new_entries = {k: v for k, v in new_entries.items() if k and v in ("KOSPI", "KOSDAQ")}
    if not new_entries:
        return
    cache = load_market_cache()
    cache.update(new_entries)
    pd.DataFrame(sorted(cache.items()), columns=["종목명", "시장"]).to_csv(MARKET_CACHE_FILE, index=False)


def fix_holding_sector(name: str, sector: str) -> list[str]:
    """이미 보유 중인 종목의 섹터를 안전하게 고친다. §1-7에서 언급된 "섹터만 고칠 수
    있는 가벼운 도구" — 2026-08-28에 실제로 겪은 버그(체크포인트 섹터 되돌아감, §6-14
    참고) 재발을 막으려고 만듦. 아래 3곳을 항상 같이 고쳐야 다음 ingest_daily.py 실행
    때 되돌아가지 않는다:
      1. stock_sector_cache.csv (영구 캐시) — 앞으로 이 종목이 다시 "신규 매수"로
         잡힐 때(재진입 등) 기준이 되는 값.
      2. portfolio_data.csv — 지금 화면에 보이는 값.
      3. checkpoint_holdings.csv — 이미 보유 중인 종목은 apply_transaction이 이 값을
         그대로 캐리하고 캐시를 다시 안 보므로, 여기가 안 고쳐지면 다음 반영 때 옛날
         값으로 되돌아간다.
    셋 다 dtype=str로 종목코드를 다뤄서(§1-6 실제 겪은 버그) 앞자리 0이 안 날아가게 함.
    반환값: 실제로 값을 바꾼 파일 목록(디버깅/확인용)."""
    touched = []
    update_sector_cache({name: sector})
    touched.append("stock_sector_cache.csv")

    df = load_holdings()
    if (df["종목명"] == name).any():
        df.loc[df["종목명"] == name, "섹터"] = sector
        save_holdings(df)
        touched.append("portfolio_data.csv")

    if CHECKPOINT_HOLDINGS_FILE.exists():
        ckpt = pd.read_csv(CHECKPOINT_HOLDINGS_FILE, dtype={"종목코드": str}, keep_default_na=False)
        if (ckpt["종목명"] == name).any():
            ckpt.loc[ckpt["종목명"] == name, "섹터"] = sector
            ckpt.to_csv(CHECKPOINT_HOLDINGS_FILE, index=False)
            touched.append("checkpoint_holdings.csv")

    return touched


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


def load_index_history() -> pd.DataFrame:
    """날짜별 코스피/코스닥 종가. asset_history.csv와 같은 성격의 로컬 CSV라 §1-5대로
    세션이 git commit/push해야 유지된다(다른 데이터 파일과 함께 커밋되므로 별도 스텝 아님)."""
    if INDEX_HISTORY_FILE.exists():
        return pd.read_csv(INDEX_HISTORY_FILE)
    return pd.DataFrame(columns=["날짜", "KOSPI", "KOSDAQ"])


def save_index_history(df: pd.DataFrame) -> None:
    df.to_csv(INDEX_HISTORY_FILE, index=False)


def snapshot_index_history(kospi: float, kosdaq: float, on_date: str | None = None) -> None:
    """코스피/코스닥 종가 오늘자(또는 지정 날짜) 스냅샷. 같은 날짜는 덮어씀.
    app.py의 "시세 새로고침" 핸들러에서 fetch_index_quotes() 직후 호출된다(별도 cron 불필요).
    resolve_trading_date()를 쓰는 이유는 §6-16 cron 날짜 밀림과 같은 맥락 — 장 시작 전
    새로고침이면 그 지수값은 "오늘"이 아니라 직전 거래일 종가이므로."""
    if not kospi or not kosdaq or kospi <= 0 or kosdaq <= 0:
        return
    d = on_date or resolve_trading_date()
    hist = load_index_history()
    hist = hist[hist["날짜"] != d]
    hist = pd.concat([hist, pd.DataFrame([{"날짜": d, "KOSPI": kospi, "KOSDAQ": kosdaq}])])
    hist = hist.sort_values("날짜")
    save_index_history(hist)


CAPTURE_ANOMALY_FILE = HERE / "report" / "capture_anomalies.csv"


def append_capture_anomalies(anomalies: list[dict]) -> None:
    """국면막대(§6-17)에서 |벤치당일| < 0.001이라 막대를 못 만든 날을
    report/capture_anomalies.csv에 누적 기록한다. 앱 렌더링마다가 아니라 app.py "시세 새로고침"
    핸들러에서만 호출한다(사용자 판단 2026-09-04: 월단위로 모아 분기 해석하고, 필요하면 그 날의
    CR을 후보에서 제외). 같은 날짜가 이미 있으면 무시."""
    if not anomalies:
        return
    rows = [{"날짜": a.get("날짜", ""), "벤치당일": a.get("벤치당일"), "내당일": a.get("내당일"),
             "사유": "|벤치당일| < 0.001 (시장 무변동)"} for a in anomalies]
    new = pd.DataFrame(rows, columns=["날짜", "벤치당일", "내당일", "사유"])
    CAPTURE_ANOMALY_FILE.parent.mkdir(parents=True, exist_ok=True)
    if CAPTURE_ANOMALY_FILE.exists():
        old = pd.read_csv(CAPTURE_ANOMALY_FILE)
        new = new[~new["날짜"].isin(old["날짜"].astype(str))]
        if new.empty:
            return
        out = pd.concat([old, new], ignore_index=True)
    else:
        out = new
    out.sort_values("날짜").to_csv(CAPTURE_ANOMALY_FILE, index=False)


# ------------------------------------------------------------------ #
# "SamHynix extracted" (§6-19) — 코스피 지수에서 대형 반도체(삼성전자/삼성전자우/SK하이닉스)를
# 덜어낸 합성 지수. 이 3종목이 코스피 시총의 절반 안팎(2026-09-04 ~52%)이라, 반도체를
# 안 담는 포트폴리오를 헤드라인 코스피와 대보면 사실상 "반도체 절반 펀드"와 비교하는
# 셈이 되어 왜곡됨 — 혼합지수의 코스피 다리를 이 합성 지수로 갈아끼워 더 공정하게 본다.
# ------------------------------------------------------------------ #
BIGCAP_HISTORY_FILE = HERE / "bigcap_history.csv"  # 날짜, 삼성전자, 삼성전자우, SK하이닉스 (종가)
BIGCAP_CODES = {"삼성전자": "005930", "삼성전자우": "005935", "SK하이닉스": "000660"}
# 비중 wᵢ(t) = sharesᵢ · closeᵢ(t) / TOTAL(t),  TOTAL(t) = TOTAL0 · KOSPI(t)/KOSPI0.
# 상장주식수는 증자/자사주 소각으로 변하니 분기에 한 번 갱신 권장. TOTAL0은 ETF/ETN 제외
# 추정(전종목 합산 ~5,870조 − ETF·ETN ~470조). 스냅샷: 2026-09-04.
_BIGCAP_SHARES = {"삼성전자": 5_846_279_000, "삼성전자우": 802_000_000, "SK하이닉스": 730_300_000}
_KOSPI_MKTCAP_ANCHOR = (6645.0, 5.40e15)  # (KOSPI 지수레벨, 지수 시가총액 원)


def load_bigcap_history() -> pd.DataFrame:
    """삼성전자/삼성전자우/SK하이닉스 일별 종가. index_history.csv와 같은 성격의 로컬 CSV."""
    if BIGCAP_HISTORY_FILE.exists():
        return pd.read_csv(BIGCAP_HISTORY_FILE)
    return pd.DataFrame(columns=["날짜"] + list(BIGCAP_CODES))


def save_bigcap_history(df: pd.DataFrame) -> None:
    df.to_csv(BIGCAP_HISTORY_FILE, index=False)


def snapshot_bigcap_history(prices: dict, on_date: str | None = None) -> None:
    """대형 반도체 3종목 종가 스냅샷(같은 날짜 덮어씀). prices: {"삼성전자": 종가, ...}.
    app.py 새로고침 핸들러에서 snapshot_index_history 바로 뒤에 호출 — 장 시작 전이면
    resolve_trading_date로 직전 거래일에 기록(§6-16 cron 날짜 밀림과 같은 맥락)."""
    prices = {k: float(v) for k, v in (prices or {}).items()
              if k in BIGCAP_CODES and v and float(v) > 0}
    if not prices:
        return
    d = on_date or resolve_trading_date()
    hist = load_bigcap_history()
    hist = hist[hist["날짜"] != d]
    row = {"날짜": d}
    row.update({k: prices.get(k) for k in BIGCAP_CODES})
    hist = pd.concat([hist, pd.DataFrame([row])]).sort_values("날짜")
    save_bigcap_history(hist)


def fetch_bigcap_quotes() -> dict:
    """{"삼성전자": 종가, "삼성전자우": 종가, "SK하이닉스": 종가}. fetch_quotes 재사용."""
    q, _ = fetch_quotes(list(BIGCAP_CODES.values()))
    out = {}
    for nm, code in BIGCAP_CODES.items():
        p = (q.get(code) or {}).get("price")
        if p:
            out[nm] = float(p)
    return out


def synthetic_kospi_ex_bigcap(index_hist: pd.DataFrame, bigcap_hist: pd.DataFrame) -> pd.DataFrame:
    """index_hist의 KOSPI 열을 '삼성전자·삼성전자우·SK하이닉스를 덜어낸 코스피' 합성 레벨로
    바꾼 사본을 돌려준다(KOSDAQ·날짜는 그대로). 이 사본을 compute_index_vs_account에 그대로
    넘기면 코스피 다리·혼합지수·RP가 전부 '반도체 제외' 버전으로 계산된다.
      일별:  r_ex = (r_kospi − Σ wᵢ·rᵢ) / (1 − Σ wᵢ)
             wᵢ(t) = sharesᵢ · closeᵢ(t) / (TOTAL0 · KOSPI(t)/KOSPI0)
    첫날 레벨 = 원본 KOSPI 첫날 값(누적수익 앵커 동일). 그날 대형주 종가가 없으면 그 구간은
    r_ex = r_kospi로 둠(=코스피와 동일). bigcap_hist가 비었거나 KOSPI 열이 없으면 원본 그대로."""
    if index_hist is None or index_hist.empty or "KOSPI" not in index_hist:
        return index_hist
    h = index_hist.copy().sort_values("날짜").reset_index(drop=True)
    if bigcap_hist is None or bigcap_hist.empty:
        return h

    def _n(x):
        try:
            v = float(x)
            return v if v > 0 else None
        except (TypeError, ValueError):
            return None

    names = list(BIGCAP_CODES)
    bg_by_date = {r["날짜"]: {n: _n(r.get(n)) for n in names}
                  for _, r in bigcap_hist.sort_values("날짜").iterrows()}
    k0_lvl, total0 = _KOSPI_MKTCAP_ANCHOR
    kospi = pd.to_numeric(h["KOSPI"], errors="coerce").tolist()
    dates = h["날짜"].tolist()

    ex_level = [kospi[0] if kospi else None] + [None] * (len(h) - 1)
    prev_closes = bg_by_date.get(dates[0])
    for i in range(1, len(h)):
        r_k = (kospi[i] / kospi[i - 1] - 1.0) if (kospi[i - 1] and kospi[i]) else 0.0
        cur = bg_by_date.get(dates[i])
        if (cur and prev_closes and kospi[i]
                and all(cur.get(n) for n in names) and all(prev_closes.get(n) for n in names)):
            total_t = total0 * (kospi[i] / k0_lvl)
            w_sum, wr_sum = 0.0, 0.0
            for n in names:
                wi = _BIGCAP_SHARES[n] * cur[n] / total_t
                ri = cur[n] / prev_closes[n] - 1.0
                w_sum += wi
                wr_sum += wi * ri
            r_ex = (r_k - wr_sum) / (1.0 - w_sum) if w_sum < 0.999 else r_k
        else:
            r_ex = r_k
        ex_level[i] = ex_level[i - 1] * (1.0 + r_ex)
        if cur and all(cur.get(n) for n in names):
            prev_closes = cur
    h["KOSPI"] = ex_level
    return h


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


WATCHLIST_PRICE_COLUMNS = ["종목명", "종목코드", "최초가", "최근가", "최근조회일시", "전일대비", "기준일"]


def get_first_day_prices_db(supabase_url: str, supabase_key: str) -> dict:
    """Supabase price_history에서 종목코드별 최초 관측일(가장 이른 날짜) 종가를 가져온다.
    반환: {종목코드: 최초가}. 접속 정보 없음/데이터 없음이면 빈 dict."""
    hist = load_watchlist_history_db(supabase_url, supabase_key)
    if hist.empty:
        return {}
    hist = hist.sort_values("날짜")
    return hist.groupby("종목코드")["종가"].first().to_dict()


def get_first_day_dates_db(supabase_url: str, supabase_key: str) -> dict:
    """Supabase price_history에서 종목코드별 최초 관측일(=Fishing "누적" %의 기준일)을
    가져온다. 반환: {종목코드: 날짜문자열}. get_first_day_prices_db와 짝을 이루는 함수 —
    화면에 "기준일"을 표시하려고 2026-08-28에 추가함(그전엔 계산에만 쓰이고 안 보였음)."""
    hist = load_watchlist_history_db(supabase_url, supabase_key)
    if hist.empty:
        return {}
    hist = hist.sort_values("날짜")
    return hist.groupby("종목코드")["날짜"].first().to_dict()


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
    origin_dates = get_first_day_dates_db(supabase_url, supabase_key)
    today = today_kst_str()
    now = now_kst_str()

    rows = []
    for _, r in watchlist.iterrows():
        name, code = r["종목명"], r["종목코드"]
        q = quotes.get(code)
        if q is None:
            continue
        price, change_pct = q["price"], q["change_pct"]
        origin = origin_prices.get(code)
        origin_date = origin_dates.get(code)
        if origin is None:
            origin = price / (1 + change_pct / 100) if change_pct != -100 else price
            origin_date = origin_date or today  # DB 히스토리 아직 없음 — 오늘을 임시 기준일로
        rows.append({"종목명": name, "종목코드": code, "최초가": origin,
                     "최근가": price, "최근조회일시": now, "전일대비": change_pct,
                     "기준일": origin_date})

    result = pd.DataFrame(rows, columns=WATCHLIST_PRICE_COLUMNS)
    return result, quote_errors


def get_watchlist_prev_day_ranks(hist_df: pd.DataFrame, basis: str, direction: str,
                                  threshold: float, today: str) -> dict:
    """Fishing 순위 변동 표시용. hist_df(load_watchlist_history_db 결과: 종목코드,종목명,
    섹터,날짜,종가,등락률)에서 "오늘" 이전 가장 최근 거래일 데이터로, 화면에 쓰는 것과
    동일한 기준(basis: "누적"/"전일")·방향(direction: "DOWN"/"UP")·임계값(threshold)으로
    그날의 ±threshold% 목록을 재구성해 순위를 매긴다. 반환: {종목명: 순위(1부터)} — 그
    거래일에 데이터가 없거나 임계값 밖이었던 종목은 dict에 없음(=UI에서 "NEW" 처리 대상)."""
    if hist_df.empty:
        return {}
    dates = sorted(d for d in hist_df["날짜"].unique() if d < today)
    if not dates:
        return {}
    prev_date = dates[-1]

    origin = hist_df.sort_values("날짜").groupby("종목코드")["종가"].first()
    prev_rows = hist_df[hist_df["날짜"] == prev_date]

    scored = []
    for _, r in prev_rows.iterrows():
        o = origin.get(r["종목코드"])
        if not o:
            continue
        pct_origin = (r["종가"] - o) / o * 100
        pct_ref = r["등락률"]
        basis_key = pct_origin if basis == "누적" else pct_ref
        if (direction == "DOWN" and basis_key <= -threshold) or \
           (direction == "UP" and basis_key >= threshold):
            scored.append((r["종목명"], basis_key))

    scored.sort(key=lambda x: abs(x[1]), reverse=True)
    return {name: i + 1 for i, (name, _) in enumerate(scored)}


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


# Volume/Foreigner 화면의 "기준"(누적=평균 대비 / 전일=어제 대비) 라디오 → flags dict의 어느 키를 쓸지
FLOW_BASIS_KEY = {
    "volume": {"누적": "vs평균pct", "전일": "vs어제pct"},
    "foreign": {"누적": "vs평균pp", "전일": "vs어제pp"},
}


def rank_flow_flags(flags: list[dict], basis_key: str, direction: str) -> list[dict]:
    """compute_volume_flags/compute_foreign_flags 결과를 Fishing과 같은 방식으로 정렬 —
    방향(DOWN=그 값이 음수, UP=양수)으로 거른 뒤 |값| 큰 순. basis_key는 FLOW_BASIS_KEY 참고."""
    sign = -1.0 if direction == "DOWN" else 1.0
    picked = [f for f in flags if f.get(basis_key) is not None and sign * f[basis_key] > 0]
    picked.sort(key=lambda f: -abs(f[basis_key]))
    return picked


def get_flow_prev_day_ranks(hist: pd.DataFrame, kind: str, basis: str, direction: str,
                             today: str) -> dict:
    """Volume/Foreigner 순위 변동(▲▼) 표시용 — Fishing의 get_watchlist_prev_day_ranks와 같은 목적.
    hist(load_investor_flow_db 결과)에서 today 이전 가장 최근 거래일까지만 잘라 flags를 다시
    계산하고, 지금 화면과 같은 기준(basis)·방향(direction)으로 순위를 매긴다.
    kind: "volume" | "foreign". 반환: {종목명: 순위(1부터)}."""
    if hist is None or hist.empty:
        return {}
    past = hist[hist["날짜"] < today]
    if past.empty or past["날짜"].nunique() < 2:
        return {}
    prev_date = past["날짜"].max()
    trimmed = past[past["날짜"] <= prev_date]
    flags = compute_volume_flags(trimmed) if kind == "volume" else compute_foreign_flags(trimmed)
    ranked = rank_flow_flags(flags, FLOW_BASIS_KEY[kind][basis], direction)
    return {f["종목명"]: i for i, f in enumerate(ranked, 1)}


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


# ------------------------------------------------------------------ #
# 포리너 프로젝트(포프) — 외인 매수 스파이크 후 주가 반응 이벤트 스터디 (2026-09-03 초안)
# 관찰 전용, 매매 신호 아님. Foreigner 스크리너와 같은 신호(외국인보유율 %p 변화)로
# 이벤트를 잡고, 그 뒤 T+1..T+5 거래일 종가수익률을 raw / 시장초과(그 종목이 속한
# 코스피·코스닥 지수 차감) / 전체 종목·전체일 평균(대조군) 대비로 집계한다.
# MEMORY: project_foreigner_fop.
# ------------------------------------------------------------------ #
def _forward_return_table(price_hist: pd.DataFrame, index_hist: pd.DataFrame,
                           market_map: dict, horizons: tuple) -> dict:
    """price_hist의 모든 (종목, 날짜 T)에 대해 T+h 거래일 종가수익률과, 그 종목 지수의
    같은 구간 수익률을 뺀 시장초과 수익률을 구한다. T+h는 달력이 아니라 그 종목
    price_history에 실제로 있는 h번째 다음 행. 반환:
        {종목코드: {"종목명": str, "by_date": {날짜: {"raw": {h: r}, "exc": {h: r}}}}}
    이벤트 검출/대조군이 공유하는 내부 헬퍼."""
    idx = index_hist if index_hist is not None else pd.DataFrame()
    kospi, kosdaq = {}, {}
    if not idx.empty:
        idx = idx.sort_values("날짜")
        kospi = dict(zip(idx["날짜"], pd.to_numeric(idx["KOSPI"], errors="coerce")))
        kosdaq = dict(zip(idx["날짜"], pd.to_numeric(idx["KOSDAQ"], errors="coerce")))

    out = {}
    for code, g in price_hist.groupby("종목코드"):
        g = g.sort_values("날짜")
        dates = list(g["날짜"])
        closes = list(pd.to_numeric(g["종가"], errors="coerce"))
        name = g["종목명"].iloc[-1] if "종목명" in g else code
        mk = (market_map or {}).get(name)
        imap = kospi if mk == "KOSPI" else kosdaq if mk == "KOSDAQ" else None
        by_date = {}
        for i, d0 in enumerate(dates):
            c0 = closes[i]
            if not c0 or pd.isna(c0) or c0 <= 0:
                continue
            rec = {"raw": {}, "exc": {}}
            for h in horizons:
                j = i + h
                if j >= len(dates):
                    continue
                c1 = closes[j]
                if not c1 or pd.isna(c1) or c1 <= 0:
                    continue
                raw = c1 / c0 - 1.0
                rec["raw"][h] = raw
                if imap is not None:
                    i0, i1 = imap.get(d0), imap.get(dates[j])
                    if i0 and i1 and not pd.isna(i0) and not pd.isna(i1) and i0 > 0:
                        rec["exc"][h] = raw - (i1 / i0 - 1.0)
            if rec["raw"]:
                by_date[d0] = rec
        out[code] = {"종목명": name, "by_date": by_date}
    return out


def study_foreign_buy_forward_returns(flow_hist: pd.DataFrame, price_hist: pd.DataFrame,
                                       index_hist: pd.DataFrame, market_map: dict,
                                       basis: str = "평균", threshold_pp: float = 0.30,
                                       horizons=(1, 2, 3, 5), min_history: int = 5) -> dict:
    """외국인보유율이 basis 기준으로 +threshold_pp(퍼센트포인트) 이상 뛴 (종목, 날짜)를
    '이벤트'로 잡고, 이후 T+h 거래일 종가수익률을 집계한다.
      basis="전일": 직전 거래일 보유율 대비 상승폭.
      basis="평균": 그 시점까지(자신 포함) 보유율 확장평균 대비 — compute_foreign_flags의
                    'vs평균pp'와 같은 정의라 Foreigner 스크리너 신호와 일치.
    min_history: 그 종목 관측이 이만큼 쌓이기 전(초반 며칠)은 평균/전일 비교가 불안정해
                 이벤트에서 제외(기본 5). 특히 '평균' 기준이 확장평균 lag 때문에 초반에
                 아무 날이나 터지는 걸 막는다.
    반환 dict(ui_portfolio_tab.py '외인 매수 → 이후 주가' 섹션용):
      n_events, sample_start, as_of, basis, threshold_pp,
      horizons: {h: {n, raw_mean, raw_hit, exc_mean, exc_hit,
                     base_n, base_exc_mean, base_exc_hit, edge_mean}},
      recent_events: [{종목명, 날짜, 신호pp, rets:{h: 시장초과수익 or raw fallback}}] (최근순 12개).
    edge_mean = 이벤트 평균 시장초과 − 대조군(전체 종목·전체일) 평균 시장초과.
    관찰 전용. MEMORY: project_foreigner_fop."""
    horizons = tuple(sorted({int(h) for h in horizons}))
    empty = {"basis": basis, "threshold_pp": threshold_pp, "n_events": 0,
             "sample_start": None, "as_of": None,
             "horizons": {h: {"n": 0} for h in horizons}, "recent_events": []}
    if flow_hist is None or flow_hist.empty or price_hist is None or price_hist.empty:
        return empty

    fwd = _forward_return_table(price_hist, index_hist, market_map or {}, horizons)

    # --- 이벤트 검출 ---
    events = []
    for code, g in flow_hist.groupby("종목코드"):
        g = g.sort_values("날짜")
        dts = list(g["날짜"])
        vals = list(pd.to_numeric(g["외국인보유율"], errors="coerce"))
        run_sum, run_n = 0.0, 0
        seen = 0  # 이 종목에서 지금까지 본 유효 관측 수(자신 제외)
        for i, v in enumerate(vals):
            if v is None or pd.isna(v):
                continue
            if basis == "전일":
                prev = vals[i - 1] if i > 0 else None
                delta = (v - prev) if (prev is not None and not pd.isna(prev)) else None
            else:
                run_sum += v
                run_n += 1
                delta = v - (run_sum / run_n)
            enough = seen >= min_history
            seen += 1
            if enough and delta is not None and delta >= threshold_pp:
                events.append({"종목코드": code, "종목명": g["종목명"].iloc[-1],
                               "날짜": dts[i], "신호pp": float(delta)})

    # --- 이벤트별 forward 수익률 ---
    hz = {h: {"raw": [], "exc": []} for h in horizons}
    enriched = []
    for ev in events:
        rec = fwd.get(ev["종목코드"], {}).get("by_date", {}).get(ev["날짜"])
        row = dict(ev, rets={})
        if rec:
            for h in horizons:
                if h in rec["raw"]:
                    hz[h]["raw"].append(rec["raw"][h])
                if h in rec["exc"]:
                    hz[h]["exc"].append(rec["exc"][h])
                row["rets"][h] = rec["exc"].get(h, rec["raw"].get(h))
        enriched.append(row)

    # --- 대조군: 전체 (종목, 날짜)의 같은 forward 분포 ---
    base = {h: {"raw": [], "exc": []} for h in horizons}
    for info in fwd.values():
        for rec in info["by_date"].values():
            for h in horizons:
                if h in rec["raw"]:
                    base[h]["raw"].append(rec["raw"][h])
                if h in rec["exc"]:
                    base[h]["exc"].append(rec["exc"][h])

    def _mean(xs):
        return sum(xs) / len(xs) if xs else None

    def _hit(xs):
        return sum(1 for x in xs if x > 0) / len(xs) if xs else None

    hzout = {}
    for h in horizons:
        em, bm = _mean(hz[h]["exc"]), _mean(base[h]["exc"])
        hzout[h] = {
            "n": len(hz[h]["raw"]),
            "raw_mean": _mean(hz[h]["raw"]), "raw_hit": _hit(hz[h]["raw"]),
            "exc_mean": em, "exc_hit": _hit(hz[h]["exc"]),
            "base_n": len(base[h]["raw"]),
            "base_exc_mean": bm, "base_exc_hit": _hit(base[h]["exc"]),
            "edge_mean": (em - bm) if (em is not None and bm is not None) else None,
        }

    enriched.sort(key=lambda r: r["날짜"], reverse=True)
    return {
        "basis": basis, "threshold_pp": threshold_pp, "n_events": len(events),
        "sample_start": str(flow_hist["날짜"].min()), "as_of": str(flow_hist["날짜"].max()),
        "horizons": hzout, "recent_events": enriched[:12],
    }


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


def fetch_daily_price_history(code: str, start_date: str, end_date: str) -> list[dict]:
    """네이버 일별시세 API(api.finance.naver.com/siseJson.naver)에서 종목의 과거 일별
    종가/거래량을 가져온다. 실시간 시세 API(fetch_quotes)와 달리 특정 기간의 과거
    데이터를 한 번에 받을 수 있어서, watchlist에 새로 편입된 보유종목의 price_history를
    "그 종목이 실제로 처음 들어온 날짜"부터 소급해서 채우는 용도로 쓴다
    (backfill_watchlist_from_holdings.py, §6-16 참고).

    start_date/end_date: "YYYY-MM-DD". 응답이 진짜 JSON은 아니고(작은따옴표 헤더 +
    큰따옴표 날짜가 섞인) 파이썬 리터럴에 가까운 형태라 ast.literal_eval로 파싱한다.
    반환: [{"날짜": "YYYY-MM-DD", "종가": float, "거래량": int}, ...] 날짜 오름차순.
    실패/데이터 없음이면 빈 리스트(비공식 API라 조용히 실패 처리, fetch_investor_flow와
    같은 패턴)."""
    start, end = start_date.replace("-", ""), end_date.replace("-", "")
    url = (f"https://api.finance.naver.com/siseJson.naver?symbol={code}"
           f"&requestType=1&startTime={start}&endTime={end}&timeframe=day")
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        rows = ast.literal_eval(resp.text.strip())
    except Exception:
        return []
    if not rows or len(rows) < 2:
        return []

    header, data = rows[0], rows[1:]
    try:
        i_date, i_close, i_vol = header.index("날짜"), header.index("종가"), header.index("거래량")
    except ValueError:
        return []

    result = []
    for row in data:
        try:
            d = row[i_date]
            date_str = f"{d[0:4]}-{d[4:6]}-{d[6:8]}"
            result.append({"날짜": date_str, "종가": float(row[i_close]), "거래량": int(row[i_vol])})
        except (IndexError, ValueError, TypeError):
            continue
    return result


def load_dividend_cache() -> dict:
    """종목코드→{"배당수익률": float, "배당기준월": str, "조회일": str}. stock_code_cache.csv/
    stock_sector_cache.csv와 똑같은 §1-3 "최초 1회만 조회, 그 뒤로는 영구 재사용" 캐시
    패턴 — 배당수익률은 시세와 달리 시시각각 바뀌는 값이 아니라서(사용자 판단,
    2026-09-01) 한 번 조회하면 다시 긁지 않는다. "조회일"은 실제 갱신 판단에는 안 쓰고
    참고용 기록으로만 남겨둔다."""
    if DIVIDEND_CACHE_FILE.exists():
        df = pd.read_csv(DIVIDEND_CACHE_FILE, dtype={"종목코드": str}, keep_default_na=False)
        return {
            row["종목코드"]: {"배당수익률": float(row["배당수익률"]),
                           "배당기준월": row.get("배당기준월", ""), "조회일": row["조회일"]}
            for _, row in df.iterrows() if row["종목코드"]
        }
    return {}


def _save_dividend_cache(cache: dict) -> None:
    rows = [{"종목코드": code, "배당수익률": v["배당수익률"], "배당기준월": v.get("배당기준월", ""),
             "조회일": v["조회일"]} for code, v in sorted(cache.items())]
    pd.DataFrame(rows, columns=["종목코드", "배당수익률", "배당기준월", "조회일"]).to_csv(
        DIVIDEND_CACHE_FILE, index=False)


def fetch_dividend_yield(code: str) -> tuple[float, str] | None:
    """네이버 종목분석 페이지(`/item/coinfo.naver`)의 "배당수익률" 행을 긁어온다.
    fetch_investor_flow와 같은 이유로 비공식 HTML 스크레이핑 — 페이지 구조가 바뀌면
    조용히 깨질 수 있다는 걸 알고 씀. 배당을 안 주는 종목은 그 행 값이 "N/A"로 표시되는데,
    이건 실패가 아니라 "배당수익률 0%"라는 뜻이라 0.0으로 반환한다(파싱 자체가 안 되는
    진짜 실패와 구분하려고 None과 별도로 둠).

    반환: (배당수익률(%), 배당기준월) 튜플, 또는 페이지 파싱 실패 시 None. 배당기준월은
    "배당수익률" 라벨 옆에 네이버가 표시하는 결산연월(예: "2025.12")이다 — 실제 배당
    지급일/기준일 같은 날짜 단위 정보는 이 페이지에 없어서, 구할 수 있는 것 중 가장
    가까운 걸 대신 쓴다. 배당이 없는 종목은 이 값도 빈 문자열."""
    url = f"https://finance.naver.com/item/coinfo.naver?code={code}"
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        html = resp.content.decode("euc-kr", errors="replace")
    except Exception:
        return None

    try:
        soup = BeautifulSoup(html, "html.parser")
        target_th = next(
            (th for th in soup.find_all("th") if th.get_text(strip=True).startswith("배당수익률")), None)
        if target_th is None:
            return None
        period_spans = target_th.find_all("span", recursive=False)
        period = period_spans[-1].get_text(strip=True) if len(period_spans) >= 2 else ""
        td = target_th.find_next_sibling("td")
        em = td.find("em") if td else None
        text = em.get_text(strip=True) if em else ""
        if not text or text == "N/A":
            return 0.0, ""
        return float(text.replace(",", "")), period
    except Exception:
        return None


def refresh_dividend_yields(codes: list[str]) -> dict:
    """보유종목 배당수익률 캐시에 없는 종목만 채운다("최초 1회만" — 사용자 요청,
    2026-09-01: 배당수익률은 새로고침마다 다시 조회할 필요가 없다고 판단). 이미 캐시에
    있는 종목은 새로고침을 몇 번을 눌러도 네트워크 요청이 전혀 안 나가고, 새로 편입된
    종목만 그 시점에 한 번 긁힌다 — fetch_quotes처럼 여러 종목을 한 번에 묶어 보내는
    API가 없어(종목별 페이지 스크레이핑) 매번 전부 다시 긁으면 새로고침이 느려지는 걸
    막기 위한 설계.
    반환: {종목코드: 배당수익률}(요청한 codes 전부, 캐시에 있던 값 포함)."""
    codes = [c for c in dict.fromkeys(codes) if c]
    cache = load_dividend_cache()
    today = today_kst_str()
    changed = False

    for code in codes:
        if code in cache:
            continue
        fetched = fetch_dividend_yield(code)
        if fetched is not None:
            yield_pct, period = fetched
            cache[code] = {"배당수익률": yield_pct, "배당기준월": period, "조회일": today}
            changed = True
        # 조회 실패면 다음 새로고침 때 다시 시도할 수 있게 캐시에 아예 안 넣는다

    if changed:
        _save_dividend_cache(cache)
    return {code: cache[code]["배당수익률"] for code in codes if code in cache}


def fetch_stock_markets(codes: list[str]) -> dict:
    """종목코드 → "KOSPI" | "KOSDAQ". fetch_quotes와 같은 실시간 시세 API를 쓰되
    `stockExchangeType.name`("KOSPI"/"KOSDAQ") 필드만 읽는다(code는 "KS"/"KQ"). 20개씩
    청크로 나눠 요청(§1-4). 실패/미확인 종목은 결과에서 빠짐."""
    codes = [c for c in dict.fromkeys(codes) if c]
    if not codes:
        return {}
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}
    out = {}
    for i in range(0, len(codes), 20):
        chunk = codes[i:i + 20]
        try:
            resp = requests.get(
                f"https://polling.finance.naver.com/api/realtime/domestic/stock/{','.join(chunk)}",
                headers=headers, timeout=6)
            resp.raise_for_status()
            datas = resp.json().get("datas") or []
        except Exception:
            continue
        for d in datas:
            code = str(d.get("itemCode") or d.get("cd") or "").strip()
            name = ((d.get("stockExchangeType") or {}).get("name") or "").strip().upper()
            if code and name in ("KOSPI", "KOSDAQ"):
                out[code] = name
    return out


def refresh_market_cache(holdings: pd.DataFrame) -> dict:
    """holdings 중 stock_market_cache.csv에 없는 종목만 시장(KOSPI/KOSDAQ)을 조회해 채운다
    (refresh_dividend_yields와 같은 "최초 1회만" 패턴 — 상장시장은 안 바뀌므로).
    반환: {종목명: "KOSPI"|"KOSDAQ"} — holdings 종목 중 캐시에 있는 것 전부."""
    cache = load_market_cache()
    need = [(str(r["종목명"]), str(r["종목코드"])) for _, r in holdings.iterrows()
            if r["종목명"] and r["종목명"] not in cache and r["종목코드"]]
    if need:
        by_code = fetch_stock_markets([c for _, c in need])
        new = {name: by_code[code] for name, code in need if code in by_code}
        update_market_cache(new)
        cache.update(new)
    return {str(r["종목명"]): cache[str(r["종목명"])] for _, r in holdings.iterrows()
            if str(r["종목명"]) in cache}


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
# 지수 대비 계좌 (§6-17)
# ------------------------------------------------------------------ #
def _cash_by_date(tx: pd.DataFrame, initial_capital: float, fee_rate: float) -> dict:
    """거래를 날짜순으로 재생하며 '그 날짜 종료 시점의 예수금'을 기록. apply_transaction을
    그대로 재생에 쓰므로(파일 상단 원칙 §1-1) 예수금이 rebuild_portfolio_*와 어긋나지 않는다.
    반환: {날짜: 예수금} — 거래가 있었던 날짜만. 없는 날은 호출부에서 직전 값을 이어 쓴다."""
    if tx is None or tx.empty:
        return {}
    holdings = pd.DataFrame(columns=HOLD_COLUMNS)
    state = {"cash": float(initial_capital), "initial": float(initial_capital), "fee_rate": fee_rate}
    code_cache = load_code_cache()
    sector_cache = load_sector_cache()
    out = {}
    for _, row in _sort_tx_for_replay(tx).iterrows():
        holdings, state, _ = apply_transaction(
            holdings, state, row["종목명"], row["구분"],
            float(row["수량"]), float(row["단가"]), code_cache, sector_cache, fee_rate)
        out[row["날짜"]] = state["cash"]
    return out


def _cash_on(cash_map: dict, date: str, initial_capital: float) -> float:
    """cash_map(거래 있었던 날짜만)에서 date 시점의 예수금 — date 이하 가장 최근 거래일 값,
    그런 게 없으면(첫 거래 전) 최초자본."""
    prior = [d for d in cash_map if d <= date]
    return cash_map[max(prior)] if prior else float(initial_capital)


def _index_cum_returns(index_hist: pd.DataFrame, anchor: str) -> pd.DataFrame:
    """index_hist(날짜, KOSPI, KOSDAQ)를 anchor일 종가 대비 누적등락(소수)으로. anchor가
    정확히 없으면 anchor 이상 가장 이른 날을 기준으로."""
    h = index_hist.copy()
    h = h[h["날짜"] >= anchor].sort_values("날짜").reset_index(drop=True)
    if h.empty:
        return pd.DataFrame(columns=["날짜", "코스피", "코스닥"])
    base_k, base_q = float(h.loc[0, "KOSPI"]), float(h.loc[0, "KOSDAQ"])
    return pd.DataFrame({
        "날짜": h["날짜"],
        "코스피": pd.to_numeric(h["KOSPI"], errors="coerce") / base_k - 1.0,
        "코스닥": pd.to_numeric(h["KOSDAQ"], errors="coerce") / base_q - 1.0,
    })


def _index_day_moves(index_hist: pd.DataFrame) -> pd.DataFrame:
    """index_hist(날짜, KOSPI, KOSDAQ)에 '전 거래일 대비 그날 등락(소수)'을 붙인다.
    반환 DataFrame[날짜, 코스피d, 코스닥d]."""
    if index_hist is None or index_hist.empty:
        return pd.DataFrame(columns=["날짜", "코스피d", "코스닥d"])
    h = index_hist.copy().sort_values("날짜").reset_index(drop=True)
    return pd.DataFrame({
        "날짜": h["날짜"],
        "코스피d": pd.to_numeric(h["KOSPI"], errors="coerce").pct_change(),
        "코스닥d": pd.to_numeric(h["KOSDAQ"], errors="coerce").pct_change(),
    })


def compute_index_vs_account(tx: pd.DataFrame, asset_hist: pd.DataFrame, index_hist: pd.DataFrame,
                              initial_capital: float, fee_rate: float = 0.0,
                              kospi_weight: float | None = None, beta_window: int = 5) -> dict:
    """'지수 대비 계좌' 그래프 데이터(§6-17). 값은 전부 소수(0.0145 = +1.45%).

    - 계좌수익(t)  = 총자산(t)/최초자본 - 1  — 앱 요약카드의 그 값. 예수금이 눌러주는 '완충된' 선.
    - 주식수익 Rs(t) = 보유주식을 100% 투자했다고 봤을 때의 누적수익. 스냅샷 구간마다
      (주식평가액 변화 - 그 구간 순매수대금)을 직전 주식평가액으로 나눠 순수 가격변동만
      복리로 누적 → 지수와 1:1 비교 가능. 예수금 비중이 낮을수록 계좌수익보다 크게 벌어짐.
      순매수대금(예수금↔주식 주머니 이동)을 빼주는 이유는 §6-10 물타기 그래프와 같은 취지.
      수수료/세금은 예수금에서 빠져 주식평가액엔 안 닿으므로 Rs엔 안 섞임(지수도 마찰비용
      0이라 비교 기준이 맞음).
    - 코스피/코스닥 = anchor일(asset_hist·index_hist 공통 시작일) 종가 대비 누적등락(0 중심).

    kospi_weight: 내 보유주식 중 코스피 종목의 평가금액 비중(0~1). 주면 "혼합 지수"
      (= wk·코스피 + (1-wk)·코스닥)를 만들어 민감도와 초과수익 판정 기준으로 쓴다
      (종목별 상장시장을 반영한 전체 벤치마크 — 사용자 요청 2026-09-01). None이면 코스피 기준.

    반환 dict:
      me:     DataFrame[날짜, 계좌수익, 주식수익, 주식당일, 계좌당일, 벤치누적, 벤치당일,
                        국면막대주식, 국면막대계좌, 국면]
              (asset_hist 스냅샷 날짜. 누적은 anchor 대비, 당일은 직전 스냅샷 대비 diff.
               벤치 = 혼합 지수(wk 없으면 코스피)를 그 스냅샷 날짜에 정렬한 값.
               국면막대* = 그 구간의 민감도 막대(하락일 = 1−내당일/벤치당일, 상승일 = 내당일/벤치당일),
               국면 = "하락"/"상승"/"".)
      index:  DataFrame[날짜, 코스피, 코스닥]  (index_hist의 모든 거래일, 누적)
      latest: {"코스피"/"코스닥"/"주식"/"계좌"/"벤치": (누적, 당일)} — 최신 시점 값.
      cap_today / cap_down / cap_up / cap_cr / cap_5d: 내 주식(Rs) 기준 국면 막대 요약 —
                   오늘 막대 / 하락일 막대 평균 / 상승일 막대 평균 / CR(=상승÷하락) / 최근 5일 평균.
      acct_cap_*: 같은 걸 내 계좌수익(예수금 포함) 기준으로.
      cap_anomalies: [{날짜, 벤치당일, 내당일}] — |벤치당일| < 0.001이라 막대를 못 만든 날(이상치).
      sensitivity_basis: "혼합" | "코스피" (막대가 어느 벤치 기준인지)
    """
    empty = {"me": pd.DataFrame(columns=["날짜", "계좌수익", "주식수익"]),
             "index": pd.DataFrame(columns=["날짜", "코스피", "코스닥"]),
             "latest": {},
             "cap_today": None, "cap_down": None, "cap_up": None, "cap_cr": None, "cap_5d": None,
             "acct_cap_today": None, "acct_cap_down": None, "acct_cap_up": None,
             "acct_cap_cr": None, "acct_cap_5d": None, "cap_anomalies": [],
             "sensitivity_basis": "혼합" if kospi_weight is not None else "코스피"}
    if asset_hist is None or asset_hist.empty or index_hist is None or index_hist.empty:
        return empty

    snap = asset_hist.copy().sort_values("날짜").reset_index(drop=True)
    anchor = max(str(snap["날짜"].min()), str(index_hist["날짜"].min()))
    snap = snap[snap["날짜"] >= anchor].reset_index(drop=True)
    if snap.empty:
        return empty

    idx_cum = _index_cum_returns(index_hist, anchor)
    cash_map = _cash_by_date(tx, initial_capital, fee_rate)

    t = tx.copy() if tx is not None and not tx.empty else pd.DataFrame(columns=["날짜", "구분", "수량", "단가"])
    if not t.empty:
        t["수량"] = pd.to_numeric(t["수량"], errors="coerce").fillna(0.0)
        t["단가"] = pd.to_numeric(t["단가"], errors="coerce").fillna(0.0)
        t["_amt"] = t["수량"] * t["단가"] * t["구분"].map({"매수": 1.0, "매도": -1.0}).fillna(0.0)

    rows = []
    prev_S = None
    prev_d = None
    Rs = 0.0
    for _, r in snap.iterrows():
        d = str(r["날짜"])
        total = float(r["총자산"])
        cash_d = _cash_on(cash_map, d, initial_capital)
        S = total - cash_d
        acct = total / initial_capital - 1.0 if initial_capital else 0.0
        if prev_S is None:
            Rs = 0.0
        elif prev_S > 0:
            flow = float(t.loc[(t["날짜"] > prev_d) & (t["날짜"] <= d), "_amt"].sum()) if not t.empty else 0.0
            rs = (S - prev_S - flow) / prev_S
            Rs = (1.0 + Rs) * (1.0 + rs) - 1.0
        rows.append({"날짜": d, "계좌수익": acct, "주식수익": Rs})
        prev_S, prev_d = S, d

    me = pd.DataFrame(rows, columns=["날짜", "계좌수익", "주식수익"])

    wk = None if kospi_weight is None else min(max(float(kospi_weight), 0.0), 1.0)

    # 벤치 = 혼합 지수(wk·코스피 + (1-wk)·코스닥, wk 없으면 코스피)를 스냅샷 날짜에 정렬해서 붙임.
    # 내 주식·내 계좌의 누적/당일을 "지수 이겼나/졌나"로 색칠하려면 각 스냅샷 시점의 벤치값이 필요.
    bench = idx_cum.copy()
    bench["_b"] = bench["코스피"] if wk is None else wk * bench["코스피"] + (1.0 - wk) * bench["코스닥"]
    bdates, bvals = list(bench["날짜"]), list(bench["_b"])

    def _bench_on(d):
        prior = [v for bd, v in zip(bdates, bvals) if bd <= d]
        return float(prior[-1]) if prior else None

    me["주식당일"] = me["주식수익"].diff()
    me["계좌당일"] = me["계좌수익"].diff()
    me["벤치누적"] = [_bench_on(d) for d in me["날짜"]]
    me["벤치당일"] = me["벤치누적"].diff()

    def _last(s):
        v = s.iloc[-1] if len(s) else None
        return float(v) if v is not None and pd.notna(v) else None

    # ---- 최신 시점 누적 + 당일 ----
    latest = {}
    if not idx_cum.empty:
        moves = _index_day_moves(index_hist)
        kd = _last(moves["코스피d"]) if len(moves) else None
        qd = _last(moves["코스닥d"]) if len(moves) else None
        latest["코스피"] = (float(idx_cum["코스피"].iloc[-1]), kd)
        latest["코스닥"] = (float(idx_cum["코스닥"].iloc[-1]), qd)
    if not me.empty:
        latest["주식"] = (_last(me["주식수익"]), _last(me["주식당일"]))
        latest["계좌"] = (_last(me["계좌수익"]), _last(me["계좌당일"]))
        latest["벤치"] = (_last(me["벤치누적"]), _last(me["벤치당일"]))  # 주식·계좌 색칠 기준

    # ---- 국면별 민감도 막대 (2026-09-04 최종 설계, RP 대체) ----
    # 하루(스냅샷 구간)마다 막대 하나. 그날 시장이 내렸으면(빨강) "얼마나 안 따라 빠졌나(방어)",
    # 올랐으면(파랑) "얼마나 따라 올랐나(참여)".
    #   하락일 막대 = 1 − 내당일/벤치당일   (1 = 하나도 안 잃음, 0 = 시장만큼, 2 = 잃은 만큼 벎)
    #   상승일 막대 = 내당일/벤치당일       (1 = 시장만큼 벎, 0 = 하나도 못 벎, >1 = 더 벎)
    # 둘 다 높을수록 좋음. |벤치당일| < EPS면 시장이 사실상 안 움직인 날이라 막대 없음 + 이상치 로그.
    # 요약: 하락장 = 하락일 막대 평균, 상승장 = 상승일 막대 평균, CR = 상승장/하락장(1=대칭, >1=우호적),
    #   5일 = 최근 beta_window일 막대 평균. 비율을 평균 내지만 사용자 판단(2026-09-04): 73종목
    #   분산이라 벤치~0인데 계좌만 튀는 날이 거의 없고, 나더라도 이상치 로그 후 월단위로 수동 제외.
    #   (예전 RP = (Δ내−Δ벤치)/|Δ벤치| 단일 식 — "0=시장동일"이 국면따라 좋/나쁨이 뒤집혀 폐기.)
    EPS = 0.001
    _bd = me["벤치당일"].values           # 스냅샷 구간별 벤치 변화(소수), index 0 = NaN

    def _cap_cols(mine_day):
        """내 당일수익 시계열 → (일자별 막대 리스트, 하락장평균, 상승장평균, CR, 5일평균, 이상치)."""
        n = len(me)
        bars = [None] * n
        anomalies = []
        for i in range(1, n):
            b, m = _bd[i], mine_day[i]
            if pd.isna(b) or pd.isna(m):
                continue
            if abs(b) < EPS:
                anomalies.append({"날짜": str(me["날짜"].iloc[i]),
                                  "벤치당일": round(float(b), 6), "내당일": round(float(m), 6)})
                continue
            bars[i] = (1.0 - m / b) if b < 0 else (m / b)
        idxvals = [(i, bars[i]) for i in range(n) if bars[i] is not None]
        down = [v for i, v in idxvals if _bd[i] < 0]
        up = [v for i, v in idxvals if _bd[i] > 0]
        d_avg = (sum(down) / len(down)) if down else None
        u_avg = (sum(up) / len(up)) if up else None
        cr = (u_avg / d_avg) if (u_avg is not None and d_avg is not None and abs(d_avg) > 1e-6) else None
        last = [v for _, v in idxvals][-beta_window:]
        avg5 = (sum(last) / len(last)) if last else None
        return bars, d_avg, u_avg, cr, avg5, anomalies

    s_bars, s_down, s_up, s_cr, s_5d, s_anom = _cap_cols(me["주식당일"].values)   # 내 주식(Rs)
    a_bars, a_down, a_up, a_cr, a_5d, _ = _cap_cols(me["계좌당일"].values)        # 내 계좌(예수금 포함)

    me["국면막대주식"] = pd.Series(s_bars, index=me.index, dtype="float64")
    me["국면막대계좌"] = pd.Series(a_bars, index=me.index, dtype="float64")
    me["국면"] = pd.Series(["하락" if (not pd.isna(x) and x < 0) else "상승" if (not pd.isna(x) and x > 0) else ""
                             for x in _bd], index=me.index)

    return {"me": me, "index": idx_cum, "latest": latest,
            "cap_today": s_bars[-1] if s_bars else None, "cap_down": s_down, "cap_up": s_up,
            "cap_cr": s_cr, "cap_5d": s_5d,
            "acct_cap_today": a_bars[-1] if a_bars else None, "acct_cap_down": a_down,
            "acct_cap_up": a_up, "acct_cap_cr": a_cr, "acct_cap_5d": a_5d,
            "cap_anomalies": s_anom,
            "sensitivity_basis": "혼합" if wk is not None else "코스피"}


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
                "등락률": 0.0, "업데이트시각": now_kst_str(),
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
