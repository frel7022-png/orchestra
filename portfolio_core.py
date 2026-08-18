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

import io
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

KST = ZoneInfo("Asia/Seoul")
HERE = Path(__file__).parent

HOLDINGS_FILE = HERE / "portfolio_data.csv"
TX_FILE = HERE / "transactions.csv"
STATE_FILE = HERE / "account_state.csv"
HISTORY_FILE = HERE / "asset_history.csv"
SECTOR_HISTORY_FILE = HERE / "sector_history.csv"
CODE_CACHE_FILE = HERE / "stock_code_cache.csv"
SECTOR_CACHE_FILE = HERE / "stock_sector_cache.csv"

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
            result[code] = {"price": price, "change_pct": change_pct}

    return result, errors


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


def rebuild_portfolio_from_transactions(tx: pd.DataFrame, initial_capital: float, fee_rate: float = 0.0,
                                         prior_holdings: pd.DataFrame | None = None):
    """transactions.csv 전체를 날짜순(같은 날짜 내에서는 원래 입력 순서대로) 처음부터
    재생하여 holdings/state/실현손익을 다시 계산.
    holdings는 이 함수(및 apply_transaction)를 통해서만 파생되는 결과물로 취급하고,
    절대 손으로 고치지 않는다 — 다음 재계산 때 덮어써지기 때문.
    fee_rate는 거래대금 대비 수수료+세금 추정 비율 — 매 거래마다 적용되며,
    state["fee_rate"]에 담겨 반환되어 이후에도 계속 같은 비율로 재사용된다.

    prior_holdings: replay 직전의 holdings 스냅샷(직전 시세 새로고침 결과). 현재가/등락률/
    업데이트시각은 거래 기록만으로는 알 수 없는 정보라서, apply_transaction은 새로 만들어지는
    각 종목 row의 현재가를 임시로 "그 종목의 첫 매수가"로 채운다. prior_holdings를 주면
    종목명 기준으로 그 실시간 시세 값을 그대로 이어붙여서, 매매일지를 반영할 때마다 오늘
    거래하지 않은 종목들의 손익률이 매수가 기준으로 리셋되는 걸 막는다."""
    holdings = pd.DataFrame(columns=HOLD_COLUMNS)
    state = {"cash": initial_capital, "initial": initial_capital, "fee_rate": fee_rate}

    if tx.empty:
        return holdings, state, tx

    code_cache = load_code_cache()
    sector_cache = load_sector_cache()

    tx_sorted = tx.copy().reset_index(drop=True)
    tx_sorted["_ord"] = range(len(tx_sorted))
    tx_sorted = tx_sorted.sort_values(["날짜", "_ord"]).reset_index(drop=True)

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

    if prior_holdings is not None and not prior_holdings.empty:
        prior = prior_holdings.drop_duplicates("종목명").set_index("종목명")
        for i, row in holdings.iterrows():
            name = row["종목명"]
            if name in prior.index:
                prev_price = float(prior.loc[name, "현재가"])
                if prev_price > 0:
                    holdings.loc[i, "현재가"] = prev_price
                    holdings.loc[i, "등락률"] = prior.loc[name, "등락률"]
                    holdings.loc[i, "업데이트시각"] = prior.loc[name, "업데이트시각"]

    tx = tx.copy()
    for tid, val in realized_map.items():
        tx.loc[tx["id"] == tid, "실현손익"] = val

    return holdings, state, tx


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
