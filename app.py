"""
한국 주식 포트폴리오 트래커 (Streamlit) — SP Orchestra
------------------------------------------------------------------
    streamlit run app.py

시세는 네이버 금융 비공식 공개 API를 사용합니다(종목코드 자동 검색 포함).
"""

import calendar
import base64
import hashlib
import io
import uuid
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

import matplotlib.pyplot as plt
import pandas as pd
import requests
import streamlit as st

KST = ZoneInfo("Asia/Seoul")
HERE = Path(__file__).parent

HOLDINGS_FILE = HERE / "portfolio_data.csv"
TX_FILE = HERE / "transactions.csv"
STATE_FILE = HERE / "account_state.csv"
HISTORY_FILE = HERE / "asset_history.csv"
SECTOR_HISTORY_FILE = HERE / "sector_history.csv"
IMPORT_LOG_FILE = HERE / "import_log.csv"

HOLD_COLUMNS = ["종목명", "종목코드", "섹터", "수량", "평단가", "현재가", "등락률", "업데이트시각"]
TX_COLUMNS = ["id", "날짜", "종목명", "구분", "수량", "단가", "실현손익", "메모", "정산반영"]

UP_COLOR = "#d9364f"    # 국내 관례: 상승/이익 = 빨강
DOWN_COLOR = "#2b6cd4"  # 하락/손실 = 파랑
CASH_LABEL = "현금(예수금)"

SECTOR_PALETTE = [
    "#2DD4BF", "#F5A623", "#A78BFA", "#34D399", "#F472B6",
    "#FBBF24", "#60A5FA", "#F87171", "#C084FC", "#38BDF8", "#FB923C",
]

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
    "가구": "소비재",
    "제약바이오": "의료바이오",
    "제약바이어": "의료바이오",  # 오타 대비
    "의료기기": "의료바이오",
    "해운": "해운",
    "조선": "해운",
    "엔터테인먼트": "엔터",
    "게임": "엔터",
    "렌탈서비스": "서비스",
    "에너지기자재": "에너지",
    "지주(에너지)": "에너지",
    "에너지": "에너지",
    "지주(방산소재)": "방산",
    "금속가공": "금속철강",
    "전자부품": "전기전자",
    "전기전자부품": "전기전자",
}

# 종목명 → 섹터 자동 매핑 (신규 종목이 CSV로 들어올 때 "미분류" 대신 자동 지정, 기존 미분류 종목도 자동 보정)
STOCK_SECTOR_MAP = {
    "유한양행": "제약바이오",
    "사조대림": "식품",
    "SGC에너지": "에너지",
    "크라운해태홀딩스": "식품",
    "서흥": "제약바이오",
    "율촌화학": "제지",
    "한샘": "가구",
    "계룡건설": "건설",
    "이마트": "유통",
    "필옵틱스": "반도체장비",
    "종근당": "제약바이오",
    "심텍": "반도체장비",
    "한화시스템": "지주(방산소재)",
}

# 섹터별 목표 비중(주식 총자산 대비, %). 아직 정하지 않은 섹터는 포함하지 않음 — 추후 추가.
SECTOR_TARGETS = {
    "식품": 30.0,
    "소비재": 20.0,
}


def group_sector(sector: str) -> str:
    """섹터 비중 보기용 그룹명 반환. 매핑에 없으면 원래 섹터명 그대로."""
    return SECTOR_GROUP_MAP.get(sector, sector)

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


def theme() -> dict:
    return THEMES[st.session_state.get("theme", "light")]


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
# GitHub 데이터 동기화 (재배포/컨테이너 재시작으로 로컬 CSV가 사라져도 복구되게)
# ------------------------------------------------------------------ #
def _github_cfg():
    """.streamlit/secrets.toml 의 [github] 섹션에서 설정을 읽어옴.
    예:
        [github]
        token = "ghp_xxx..."
        repo = "username/reponame"
        branch = "main"
        # path_prefix = "data/"   # 선택: 레포 내 하위 폴더에 저장하고 싶을 때
    미설정 시 GitHub 동기화 없이 로컬 디스크만 사용(조용히 스킵).
    """
    try:
        gh = st.secrets["github"]
        return gh["token"], gh["repo"], gh.get("branch", "main"), gh.get("path_prefix", "")
    except Exception:
        return None, None, None, None


def _github_api_url(local_path: Path, prefix: str) -> str:
    token, repo, branch, _ = _github_cfg()
    file_path = f"{prefix}{local_path.name}" if prefix else local_path.name
    return f"https://api.github.com/repos/{repo}/contents/{file_path}"


def _github_log(filename: str, action: str, ok: bool, detail: str = "") -> None:
    log = st.session_state.setdefault("github_sync_log", [])
    log.append({"file": filename, "action": action, "ok": ok, "detail": detail})
    st.session_state["github_sync_log"] = log[-30:]  # 최근 30건만 유지


def github_fetch_file(local_path: Path) -> bool:
    """로컬에 파일이 없을 때 GitHub 레포의 최신본을 받아와 로컬에 저장. 성공 시 True."""
    token, repo, branch, prefix = _github_cfg()
    if not token:
        return False
    try:
        url = _github_api_url(local_path, prefix)
        headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}
        r = requests.get(url, headers=headers, params={"ref": branch}, timeout=10)
        if r.status_code == 200:
            content = base64.b64decode(r.json()["content"])
            local_path.write_bytes(content)
            _github_log(local_path.name, "fetch", True)
            return True
        elif r.status_code == 404:
            _github_log(local_path.name, "fetch", False, "GitHub에 이 파일이 아직 없음(404) — 신규 생성 예정")
        else:
            _github_log(local_path.name, "fetch", False, f"HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        _github_log(local_path.name, "fetch", False, f"예외: {e}")
    return False


def github_commit_file(local_path: Path, message: str) -> bool:
    """로컬 CSV 저장 직후, GitHub 레포에도 동일 내용을 커밋(생성 또는 업데이트). 성공 시 True."""
    token, repo, branch, prefix = _github_cfg()
    if not token:
        _github_log(local_path.name, "commit", False, "GitHub secrets(token)이 설정되어 있지 않음")
        return False
    if not local_path.exists():
        _github_log(local_path.name, "commit", False, "로컬 파일이 존재하지 않음")
        return False
    try:
        url = _github_api_url(local_path, prefix)
        headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}
        content_b64 = base64.b64encode(local_path.read_bytes()).decode()

        sha = None
        r = requests.get(url, headers=headers, params={"ref": branch}, timeout=10)
        if r.status_code == 200:
            sha = r.json().get("sha")
        elif r.status_code not in (200, 404):
            _github_log(local_path.name, "commit", False, f"sha 조회 실패 HTTP {r.status_code}: {r.text[:200]}")
            return False

        payload = {"message": message, "content": content_b64, "branch": branch}
        if sha:
            payload["sha"] = sha
        put_r = requests.put(url, headers=headers, json=payload, timeout=10)
        if put_r.status_code in (200, 201):
            _github_log(local_path.name, "commit", True)
            return True
        else:
            _github_log(local_path.name, "commit", False, f"HTTP {put_r.status_code}: {put_r.text[:300]}")
            return False
    except Exception as e:
        _github_log(local_path.name, "commit", False, f"예외: {e}")
        return False


# ------------------------------------------------------------------ #
# 데이터 로드 / 저장
# ------------------------------------------------------------------ #
def load_holdings() -> pd.DataFrame:
    if not HOLDINGS_FILE.exists():
        github_fetch_file(HOLDINGS_FILE)
    if HOLDINGS_FILE.exists():
        df = pd.read_csv(HOLDINGS_FILE, dtype={"종목코드": str}, keep_default_na=False, na_values=[""])
        for col in HOLD_COLUMNS:
            if col not in df.columns:
                df[col] = "" if col in ("종목명", "종목코드", "섹터", "업데이트시각") else 0.0
        for col in ("수량", "평단가", "현재가", "등락률"):
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0).astype(float)
        for col in ("종목명", "종목코드", "섹터", "업데이트시각"):
            df[col] = df[col].apply(clean_str)
        # 미분류 종목명이 STOCK_SECTOR_MAP에 등록돼 있으면 자동으로 섹터 보정
        needs_fix = (df["섹터"].isin(["", "미분류"])) & (df["종목명"].isin(STOCK_SECTOR_MAP.keys()))
        if needs_fix.any():
            df.loc[needs_fix, "섹터"] = df.loc[needs_fix, "종목명"].map(STOCK_SECTOR_MAP)
        return df[HOLD_COLUMNS]
    return pd.DataFrame(columns=HOLD_COLUMNS)


def save_holdings(df: pd.DataFrame) -> None:
    df.to_csv(HOLDINGS_FILE, index=False)
    github_commit_file(HOLDINGS_FILE, "portfolio_data.csv 업데이트")


def load_transactions() -> pd.DataFrame:
    if not TX_FILE.exists():
        github_fetch_file(TX_FILE)
    if TX_FILE.exists():
        df = pd.read_csv(TX_FILE, dtype={"id": str}, keep_default_na=False, na_values=[""])
        for col in TX_COLUMNS:
            if col not in df.columns:
                df[col] = ""
        return df[TX_COLUMNS]
    return pd.DataFrame(columns=TX_COLUMNS)


def save_transactions(df: pd.DataFrame) -> None:
    df.to_csv(TX_FILE, index=False)
    github_commit_file(TX_FILE, "transactions.csv 업데이트")


def load_state() -> dict:
    if not STATE_FILE.exists():
        github_fetch_file(STATE_FILE)
    if STATE_FILE.exists():
        df = pd.read_csv(STATE_FILE)
        return {"cash": float(df.loc[0, "예수금"]), "initial": float(df.loc[0, "최초자본"])}
    return {"cash": 10_000_000.0, "initial": 10_000_000.0}


def save_state(state: dict) -> None:
    pd.DataFrame([{"예수금": state["cash"], "최초자본": state["initial"]}]).to_csv(STATE_FILE, index=False)
    github_commit_file(STATE_FILE, "account_state.csv 업데이트")


def load_history() -> pd.DataFrame:
    if not HISTORY_FILE.exists():
        github_fetch_file(HISTORY_FILE)
    if HISTORY_FILE.exists():
        return pd.read_csv(HISTORY_FILE)
    return pd.DataFrame(columns=["날짜", "총자산", "조정자산"])


def save_history(df: pd.DataFrame) -> None:
    df.to_csv(HISTORY_FILE, index=False)
    github_commit_file(HISTORY_FILE, "asset_history.csv 업데이트")


def snapshot_history(total_assets: float, adjusted_assets: float) -> None:
    hist = load_history()
    d = today_kst_str()
    hist = hist[hist["날짜"] != d]
    hist = pd.concat([hist, pd.DataFrame([{"날짜": d, "총자산": total_assets, "조정자산": adjusted_assets}])])
    hist = hist.sort_values("날짜")
    save_history(hist)


def load_sector_history() -> pd.DataFrame:
    if not SECTOR_HISTORY_FILE.exists():
        github_fetch_file(SECTOR_HISTORY_FILE)
    if SECTOR_HISTORY_FILE.exists():
        return pd.read_csv(SECTOR_HISTORY_FILE)
    return pd.DataFrame(columns=["날짜", "섹터그룹", "비중"])


def save_sector_history(df: pd.DataFrame) -> None:
    df.to_csv(SECTOR_HISTORY_FILE, index=False)
    github_commit_file(SECTOR_HISTORY_FILE, "sector_history.csv 업데이트")


def snapshot_sector_history(weights: dict) -> None:
    """섹터그룹별 오늘자 비중(주식 총자산 대비 %) 스냅샷 저장. 같은 날짜 데이터는 덮어씀."""
    if not weights:
        return
    hist = load_sector_history()
    d = today_kst_str()
    hist = hist[hist["날짜"] != d]
    new_rows = pd.DataFrame([{"날짜": d, "섹터그룹": k, "비중": v} for k, v in weights.items()])
    hist = pd.concat([hist, new_rows], ignore_index=True)
    hist = hist.sort_values(["날짜", "섹터그룹"])
    save_sector_history(hist)


# ------------------------------------------------------------------ #
# 네이버 금융: 종목명 → 종목코드 자동 검색 + 시세 조회
# ------------------------------------------------------------------ #
@st.cache_data(show_spinner=False, ttl=60 * 60 * 24)
def get_current_prices_for_names(names: list[str]) -> dict:
    """종목명 리스트의 현재가 조회. 보유 여부와 무관 — 청산된 종목 추적용."""
    name_to_code = {}
    for n in names:
        code = resolve_code(n)
        if code:
            name_to_code[n] = code
    quotes = fetch_quotes(list(name_to_code.values()))
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


def resolve_code(name: str):
    name = (name or "").strip()
    if not name:
        return None
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


def fetch_quotes(codes: list[str]) -> dict:
    """네이버 실시간 시세 API는 한 번에 너무 많은 종목코드를 요청하면 일부만 응답하는
    경우가 있어(대략 20개 안팎에서 잘림), 20개씩 나눠서 요청한 뒤 결과를 합친다."""
    codes = [c for c in codes if c]
    if not codes:
        return {}

    CHUNK_SIZE = 20
    result = {}
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}

    for i in range(0, len(codes), CHUNK_SIZE):
        chunk = codes[i:i + CHUNK_SIZE]
        url = f"https://polling.finance.naver.com/api/realtime/domestic/stock/{','.join(chunk)}"
        try:
            resp = requests.get(url, headers=headers, timeout=6)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as e:
            st.warning(f"시세 조회 실패({i + 1}~{i + len(chunk)}번째 종목): {e}")
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

    return result


def refresh_all_prices(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    unresolved = []
    for i, row in df.iterrows():
        code = clean_str(row.get("종목코드", ""))
        if not code or code.lower() == "nan":
            found = resolve_code(row["종목명"])
            if found:
                df.loc[i, "종목코드"] = found
            else:
                unresolved.append(row["종목명"])

    codes = [clean_str(c) for c in df["종목코드"].tolist()]
    codes = [c for c in codes if c and c.lower() != "nan"]
    quotes = fetch_quotes(codes)

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
    if updated:
        st.toast(f"{updated}개 종목 시세 갱신 완료")
    if unresolved:
        st.warning("종목명을 찾지 못했어요(직접 입력 필요): " + ", ".join(unresolved))
    if failed:
        st.warning("시세를 못 가져왔어요(직접 입력 필요): " + ", ".join(failed))
    return df


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
# 거래 반영 (매수/매도)
# ------------------------------------------------------------------ #
def apply_transaction(holdings: pd.DataFrame, state: dict, name: str, kind: str, qty: float, price: float):
    holdings = holdings.copy()
    realized = None
    match = holdings.index[holdings["종목명"] == name]

    if kind == "매수":
        cost = qty * price
        state["cash"] -= cost
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
            new_row.update({"종목명": name, "종목코드": "", "섹터": STOCK_SECTOR_MAP.get(name, "미분류"),
                             "수량": qty, "평단가": price, "현재가": price,
                             "등락률": 0, "업데이트시각": now_kst_str()})
            holdings = pd.concat([holdings, pd.DataFrame([new_row])], ignore_index=True)
    else:  # 매도
        proceeds = qty * price
        state["cash"] += proceeds
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


def load_import_log() -> pd.DataFrame:
    if not IMPORT_LOG_FILE.exists():
        github_fetch_file(IMPORT_LOG_FILE)
    if IMPORT_LOG_FILE.exists():
        return pd.read_csv(IMPORT_LOG_FILE, dtype=str)
    return pd.DataFrame(columns=["hash", "날짜", "처리시각"])


def save_import_log(df: pd.DataFrame) -> None:
    df.to_csv(IMPORT_LOG_FILE, index=False)
    github_commit_file(IMPORT_LOG_FILE, "import_log.csv 업데이트")


def parse_broker_daily_csv(raw: bytes) -> pd.DataFrame:
    """증권사 일일 매매일지 CSV(잔고/금일매수/금일매도 구조)를 파싱.
    컬럼명이 병합헤더+개행문자라 인식이 불안정하므로 '위치(열 순서)' 기준으로 읽는다.
    기대 열 순서: [상세, 종목명, 잔고구분, 잔고수량, 잔고평균단가,
                  매수평균가, 매수수량, 매수금액, 매도평균가, 매도수량, 매도금액,
                  수수료, 실현손익(증권사), 손익률, ...]
    """
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
        raise ValueError("예상한 매매일지 형식이 아닙니다 (열 개수 부족).")

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


def rebuild_portfolio_from_transactions(tx: pd.DataFrame, initial_capital: float):
    """transactions.csv 전체를 날짜순으로 처음부터 재생하여 holdings/state/실현손익을 다시 계산.
    같은 날짜의 CSV 매매일지를 여러 번 업로드해도(=그 날짜 거래가 통째로 교체됨) 항상
    '최신 업로드 기준'의 정확한 최종 상태가 되도록 하는 단일 진실 공급원(source of truth) 방식."""
    holdings = pd.DataFrame(columns=HOLD_COLUMNS)
    state = {"cash": initial_capital, "initial": initial_capital}

    if tx.empty:
        return holdings, state, tx

    tx_sorted = tx.copy().reset_index(drop=True)
    tx_sorted["_ord"] = range(len(tx_sorted))
    tx_sorted = tx_sorted.sort_values(["날짜", "_ord"]).reset_index(drop=True)

    realized_map = {}
    for _, row in tx_sorted.iterrows():
        name = row["종목명"]
        kind = row["구분"]
        qty = float(row["수량"])
        price = float(row["단가"])
        holdings, state, realized = apply_transaction(holdings, state, name, kind, qty, price)
        if kind == "매도":
            realized_map[row["id"]] = realized if realized is not None else 0.0

    tx = tx.copy()
    for tid, val in realized_map.items():
        tx.loc[tx["id"] == tid, "실현손익"] = val

    return holdings, state, tx


def import_daily_trades(parsed: pd.DataFrame, holdings: pd.DataFrame, state: dict,
                         tx: pd.DataFrame, trade_date: str):
    """해당 날짜의 매매일지를 반영.
    증권사 CSV는 '그날 하루 전체 누적' 내역이므로, 같은 날짜에 이미 CSV로 반영된 거래가
    있으면 전부 지우고 이번 업로드분으로 교체한 뒤, 거래 기록 전체를 처음부터 재생해서
    holdings/state를 다시 계산한다 (그래야 같은 날 여러 번 올려도 중복 반영되지 않음)."""
    tx = tx.copy()
    prior_mask = (tx["날짜"] == trade_date) & (tx["메모"] == "CSV 업로드")
    replaced_count = int(prior_mask.sum())
    tx = tx[~prior_mask].reset_index(drop=True)

    new_rows = []
    for _, r in parsed.iterrows():
        name = r["종목명"]
        if r["매수수량"] > 0 and r["매수평균가"] > 0:
            new_rows.append({
                "id": str(uuid.uuid4())[:8], "날짜": trade_date, "종목명": name, "구분": "매수",
                "수량": r["매수수량"], "단가": r["매수평균가"], "실현손익": "",
                "메모": "CSV 업로드", "정산반영": True,
            })
        if r["매도수량"] > 0 and r["매도평균가"] > 0:
            new_rows.append({
                "id": str(uuid.uuid4())[:8], "날짜": trade_date, "종목명": name, "구분": "매도",
                "수량": r["매도수량"], "단가": r["매도평균가"], "실현손익": "",
                "메모": "CSV 업로드", "정산반영": True,
            })
    if new_rows:
        tx = pd.concat([tx, pd.DataFrame(new_rows)], ignore_index=True)

    holdings2, state2, tx2 = rebuild_portfolio_from_transactions(tx, state.get("initial", 10_000_000.0))
    return holdings2, state2, tx2, len(new_rows), replaced_count


# ------------------------------------------------------------------ #
# 페이지 설정
# ------------------------------------------------------------------ #
st.set_page_config(page_title="SP Orchestra", page_icon="◆", layout="centered")

if "theme" not in st.session_state:
    st.session_state["theme"] = "light"
T = theme()

st.markdown(f"""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Sora:wght@600;700;800&display=swap');
    .stApp {{ background-color: {T['bg']}; }}
    .brand {{
        font-family: 'Sora', sans-serif;
        font-weight: 800;
        font-size: 18px;
        letter-spacing: 0.01em;
        color: {T['text']};
        padding: 6px 0;
        white-space: nowrap;
    }}
    .block-container {{ padding-top: 1.1rem; padding-bottom: 2rem; padding-left: 1rem; padding-right: 1rem; max-width: 480px; }}
    #MainMenu, footer, header {{ visibility: hidden; }}
    h1, h2, h3, h4, h5, p, span, label, div {{ color: {T['text']}; }}

    /* 파일 업로드 위젯: 기본 다크 배경 대신 앱 테마에 맞춤 */
    section[data-testid="stFileUploaderDropzone"] {{
        background: {T['card2']} !important;
        border: 1px dashed {T['border']} !important;
        border-radius: 10px !important;
    }}
    section[data-testid="stFileUploaderDropzone"] * {{
        color: {T['text']} !important;
    }}
    section[data-testid="stFileUploaderDropzone"] small,
    section[data-testid="stFileUploaderDropzone"] span {{
        color: {T['muted']} !important;
    }}
    section[data-testid="stFileUploaderDropzone"] button {{
        background: {T['card']} !important;
        color: {T['text']} !important;
        border: 1px solid {T['border']} !important;
    }}
    div[data-testid="stFileUploaderFile"],
    div[data-testid="stFileUploaderFileName"] {{
        background: {T['card']} !important;
        border: 1px solid {T['border']} !important;
        border-radius: 8px !important;
        color: {T['text']} !important;
    }}
    div[data-testid="stFileUploaderFile"] * {{
        color: {T['text']} !important;
    }}

    .summary-box {{ background:{T['card']}; border:1px solid {T['border']}; border-radius:14px; padding:18px 20px; margin-bottom:14px; }}
    .summary-label {{ color:{T['muted']}; font-size:13px; margin-bottom:4px; }}
    .summary-main {{ font-size:28px; font-weight:800; line-height:1.2; }}
    .summary-sub {{ font-size:14px; font-weight:600; margin-left:6px; }}
    .summary-grid {{ display:flex; flex-wrap:wrap; justify-content:space-between; margin-top:14px; gap:8px; }}
    .summary-grid div {{ font-size:12.5px; color:{T['muted']}; min-width:29%; }}
    .summary-grid b {{ display:block; font-size:15px; color:{T['text']}; margin-top:2px; }}
    .capital-line {{ margin-top:10px; padding-top:10px; border-top:1px solid {T['border']}; font-size:12.5px; color:{T['muted']}; }}
    .capital-line b {{ font-size:14px; }}

    .daily-trade-box {{ margin-top:10px; padding-top:10px; border-top:1px solid {T['border']}; font-size:12.5px; color:{T['muted']}; }}
    .daily-trade-count {{ font-size:13px; color:{T['text']}; font-weight:700; margin-bottom:6px; }}
    .daily-trade-count span {{ font-weight:400; color:{T['muted']}; margin-left:4px; }}
    .daily-trade-row {{ display:flex; flex-wrap:wrap; gap:6px 8px; align-items:baseline; margin-top:4px; }}
    .daily-trade-row .tag-label {{ font-size:12px; font-weight:700; min-width:30px; }}
    .trade-chip {{ font-size:12px; background:{T['bg']}; border:1px solid {T['border']}; border-radius:99px; padding:2px 9px; color:{T['text']}; }}
    .trade-chip b {{ font-weight:600; }}

    .legend-wrap {{ display:flex; flex-wrap:wrap; gap:7px 14px; margin-top:10px; justify-content:center; }}
    .legend-item {{ display:flex; align-items:center; gap:5px; font-size:12px; color:{T['text']}; }}
    .legend-dot {{ width:8px; height:8px; border-radius:99px; flex-shrink:0; }}
    .legend-pct {{ color:{T['muted']}; font-family: ui-monospace, monospace; }}

    .sector-bar-list {{ margin-top:10px; }}
    .sector-bar-row {{ display:flex; align-items:center; gap:6px; margin-bottom:14px; }}
    .sector-bar-label {{ font-size:11px; font-weight:600; color:{T['text']}; width:64px; flex-shrink:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
    .sector-bar-track {{ position:relative; flex:1; height:14px; background:{T['card']}; border:1px solid {T['border']}; border-radius:999px; overflow:visible; }}
    .sector-bar-fill {{ position:absolute; left:1px; top:1px; bottom:1px; border-radius:999px; }}
    .sector-target-marker {{ position:absolute; top:-2px; bottom:-2px; width:2px; background:{T['text']}; opacity:0.55; }}
    .sector-target-label {{ position:absolute; top:100%; margin-top:2px; transform:translateX(-50%); font-size:9.5px; color:{T['muted2']}; white-space:nowrap; }}
    .sector-bar-pct {{ font-size:12px; color:{T['muted']}; width:64px; flex-shrink:0; text-align:right; font-family: ui-monospace, monospace; white-space:nowrap; }}
    .sector-bar-pct .cur {{ font-weight:700; color:{T['text']}; }}
    .sector-bar-pct .delta {{ margin-left:3px; }}
    .sector-stock-names {{ font-size:10.5px; color:{T['muted2']}; margin:2px 0 0 2px; }}

    .updown-row {{ display:flex; align-items:center; gap:8px; padding:7px 2px; border-bottom:1px solid {T['border']}; font-size:12.5px; }}
    .updown-row:last-child {{ border-bottom:none; }}
    .updown-row .name {{ font-weight:700; color:{T['text']}; flex:1; }}
    .updown-row .pct {{ font-weight:700; font-family: ui-monospace, monospace; width:62px; text-align:right; }}
    .updown-row .detail {{ font-size:11px; color:{T['muted']}; font-family: ui-monospace, monospace; width:118px; text-align:right; }}


    .stock-card {{ background:{T['card']}; border:1px solid {T['border']}; border-radius:12px; padding:10px 16px; margin-bottom:7px; }}
    .stock-top {{ display:flex; justify-content:space-between; align-items:baseline; }}
    .stock-name {{ font-size:15px; font-weight:700; color:{T['text']}; }}
    .stock-weight-inline {{ font-size:11px; color:{T['muted']}; margin-left:6px; }}
    .sector-tag {{ font-size:10.5px; padding:2px 7px; border-radius:5px; font-weight:600; flex-shrink:0; }}
    .stock-grid {{ display:grid; grid-template-columns: 0.7fr 1.05fr 1.05fr 1.3fr; gap:6px; margin-top:7px; }}
    .cell .top {{ font-size:12.5px; font-weight:700; color:{T['text']}; }}
    .cell .bottom {{ font-size:11px; color:{T['muted']}; margin-top:2px; }}
    .stock-foot {{ display:flex; justify-content:flex-end; margin-top:6px; font-size:10px; color:{T['muted2']}; }}

    .tx-card {{ background:{T['card']}; border:1px solid {T['border']}; border-radius:10px; padding:10px 14px; margin-bottom:6px; display:flex; justify-content:space-between; align-items:center; }}
    .tx-left {{ font-size:13px; }}
    .tx-left .name {{ font-weight:700; color:{T['text']}; }}
    .tx-left .meta {{ color:{T['muted']}; font-size:11.5px; }}
    .tx-right {{ text-align:right; font-size:13px; font-weight:700; }}

    /* ---- 옅은/짙은 회색 버튼: 눌러도 색 안 바뀌게 강제 고정 ---- */
    div.stButton > button,
    div.stButton > button:hover,
    div.stButton > button:active,
    div.stButton > button:focus,
    div.stButton > button:focus:not(:active) {{
        background-color: {T['card2']} !important;
        color: {T['text']} !important;
        border: 1px solid {T['border']} !important;
        box-shadow: none !important;
        border-radius: 8px;
        font-weight: 600;
        font-size: 11px;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        padding: 0.2rem 0.35rem;
        min-height: 1.7rem;
    }}
    div.stButton > button p {{ color: {T['text']} !important; }}
    div.stFormSubmitButton > button,
    div.stFormSubmitButton > button:hover,
    div.stFormSubmitButton > button:active,
    div.stFormSubmitButton > button:focus {{
        background-color: {T['card2']} !important;
        color: {T['text']} !important;
        border: 1px solid {T['border']} !important;
        box-shadow: none !important;
    }}
    div[data-testid="stPopover"] > div > button,
    div[data-testid="stPopover"] > div > button:hover,
    div[data-testid="stPopover"] > div > button:active,
    div[data-testid="stPopover"] > div > button:focus {{
        background-color: {T['card2']} !important;
        color: {T['text']} !important;
        border: 1px solid {T['border']} !important;
        box-shadow: none !important;
    }}

    [data-testid="stExpander"],
    [data-testid="stExpander"] > details,
    [data-testid="stExpander"] summary,
    [data-testid="stExpander"] summary > div,
    [data-testid="stExpander"] div[data-testid="stExpanderDetails"] {{
        background-color: {T['card']} !important;
        border-color: {T['border']} !important;
        border-radius: 12px !important;
    }}
    [data-testid="stExpander"] summary,
    [data-testid="stExpander"] summary:hover,
    [data-testid="stExpander"] summary p,
    [data-testid="stExpander"] svg {{
        color: {T['text']} !important;
        fill: {T['text']} !important;
        background-color: {T['card']} !important;
    }}

    div[data-testid="stTextInput"] div,
    div[data-testid="stNumberInput"] div,
    div[data-testid="stSelectbox"] div,
    div[data-testid="stDateInput"] div,
    div[data-testid="stTextArea"] div,
    div[data-baseweb="input"],
    div[data-baseweb="base-input"],
    div[data-baseweb="textarea"],
    div[data-baseweb="select"] {{
        background-color: {T['card2']} !important;
        border-color: {T['border']} !important;
        box-shadow: none !important;
    }}
    div[data-testid="stTextInput"] input,
    div[data-testid="stNumberInput"] input,
    div[data-testid="stSelectbox"] input,
    div[data-testid="stDateInput"] input,
    div[data-testid="stTextArea"] textarea,
    input, textarea, select {{
        background-color: transparent !important;
        color: {T['text']} !important;
        border: none !important;
    }}
    div[data-baseweb="input"],
    div[data-baseweb="base-input"],
    div[data-baseweb="select"] > div:first-child {{
        border: 1px solid {T['border']} !important;
        border-radius: 8px !important;
    }}

    /* 드롭다운을 눌렀을 때 뜨는 목록(팝업)은 별도 레이어라 위 규칙이 안 먹어서 따로 지정 */
    div[data-baseweb="popover"],
    div[data-baseweb="popover"] div,
    div[data-baseweb="menu"],
    ul[role="listbox"] {{
        background-color: {T['card2']} !important;
    }}
    li[role="option"] {{
        background-color: {T['card2']} !important;
        color: {T['text']} !important;
    }}
    li[role="option"]:hover,
    li[role="option"][aria-selected="true"] {{
        background-color: {T['border']} !important;
        color: {T['text']} !important;
    }}
    button[data-testid="stNumberInputStepDown"],
    button[data-testid="stNumberInputStepUp"],
    button[data-testid="stNumberInputStepDown"]:hover,
    button[data-testid="stNumberInputStepUp"]:hover {{
        background-color: {T['card2']} !important;
        border: 1px solid {T['border']} !important;
        color: {T['text']} !important;
    }}
    svg {{ fill: {T['muted']} !important; }}

    /* 라디오/토글: 동그라미 표시를 완전히 숨기고 텍스트 알약(pill)만 남김 */
    div[data-testid="stToggle"] label,
    div[data-testid="stToggle"] span,
    div[data-testid="stToggle"] div,
    div[data-testid="stToggle"] [role="switch"] {{
        background-color: {T['card2']} !important;
        border-color: {T['border']} !important;
    }}
    div[data-testid="stToggle"] [role="switch"][aria-checked="true"] {{
        background-color: {T['muted2']} !important;
    }}
    div[data-testid="stToggle"] [role="switch"] > div {{
        background-color: #fff !important;
    }}
    div[role="radiogroup"] {{
        flex-wrap: nowrap !important;
        gap: 3px 4px !important;
    }}
    div[role="radiogroup"] label {{
        background-color: {T['card2']} !important;
        border: 1px solid {T['border']} !important;
        border-radius: 7px !important;
        padding: 2px 6px !important;
        margin: 0 !important;
        min-height: 0 !important;
        flex-shrink: 1 !important;
    }}
    div[role="radiogroup"] label > *:first-child,
    div[role="radiogroup"] label svg,
    div[role="radiogroup"] [data-baseweb="radio"] > div:first-child {{
        display: none !important;
        width: 0 !important;
        height: 0 !important;
    }}
    div[role="radiogroup"] label div[data-testid="stMarkdownContainer"] p {{
        font-size: 11px !important;
        color: {T['text']} !important;
        white-space: nowrap;
    }}
    div[role="radiogroup"] label[aria-checked="true"] {{
        border: 1.5px solid {T['muted2']} !important;
        font-weight: 700;
    }}
    /* 모든 가로 배치(달력 포함)를 어떤 화면 크기에서도 한 줄로 강제 */
    div[data-testid="stHorizontalBlock"] {{
        flex-direction: row !important;
        flex-wrap: nowrap !important;
        gap: 3px !important;
    }}
    div[data-testid="column"],
    div[data-testid="stColumn"] {{
        width: 0 !important;
        min-width: 0 !important;
        flex: 1 1 0 !important;
        padding: 0 1px !important;
    }}
    div.stButton > button {{
        padding-left: 0.2rem !important;
        padding-right: 0.2rem !important;
    }}
</style>
""", unsafe_allow_html=True)


def check_password() -> bool:
    if "app_password" not in st.secrets:
        return True
    if st.session_state.get("authed"):
        return True
    st.markdown('<div class="brand">SP Orchestra</div>', unsafe_allow_html=True)
    pw = st.text_input("비밀번호를 입력하세요", type="password")
    if pw:
        if pw == st.secrets["app_password"]:
            st.session_state["authed"] = True
            st.rerun()
        else:
            st.error("비밀번호가 틀렸습니다.")
    return False


if not check_password():
    st.stop()

col_title, col_label, col_theme = st.columns([2.2, 1, 0.9])
with col_title:
    st.markdown('<div class="brand">SP Orchestra</div>', unsafe_allow_html=True)
with col_label:
    st.markdown(
        f"<div style='text-align:right;font-size:11px;color:{T['muted']};padding-top:12px;'>화면</div>",
        unsafe_allow_html=True,
    )
with col_theme:
    is_dark = st.session_state["theme"] == "dark"
    new_dark = st.toggle("다크", value=is_dark, key="theme_switch", label_visibility="collapsed")
    if new_dark != is_dark:
        st.session_state["theme"] = "dark" if new_dark else "light"
        st.rerun()

holdings = load_holdings()
state = load_state()
tx = load_transactions()

top_l, top_r = st.columns([5, 2])
with top_r:
    refresh_clicked_top = st.button("시세 새로고침", use_container_width=True, key="refresh_btn_top")

if refresh_clicked_top:
    with st.spinner("종목명으로 시세를 찾는 중..."):
        holdings = refresh_all_prices(holdings)
        df_top, stock_val_top, total_assets_top, unreal_top = compute_metrics(holdings, state["cash"])
        snapshot_history(total_assets_top, total_assets_top + unreal_top)
        snapshot_sector_history(compute_sector_weights(df_top))
    st.rerun()

tab_port, tab_tx = st.tabs(["포트폴리오", "거래 기록"])

# ==================================================================== #
# 탭 1: 포트폴리오
# ==================================================================== #
with tab_port:
    df, stock_valuation, total_assets, unrealized_loss = compute_metrics(holdings, state["cash"])
    total_cost = df["매입금액"].sum()
    stock_profit = stock_valuation - total_cost
    stock_profit_pct = (stock_profit / total_cost * 100) if total_cost else 0

    capital_return = total_assets - state["initial"]
    capital_return_pct = (capital_return / state["initial"] * 100) if state["initial"] else 0

    today_str = today_kst_str()
    today_tx = tx[tx["날짜"].astype(str) == today_str]
    daily_pnl = pd.to_numeric(
        today_tx.loc[today_tx["구분"] == "매도", "실현손익"], errors="coerce"
    ).sum()

    color = UP_COLOR if stock_profit >= 0 else DOWN_COLOR
    sign = "+" if stock_profit >= 0 else ""
    cap_color = UP_COLOR if capital_return >= 0 else DOWN_COLOR
    cap_sign = "+" if capital_return >= 0 else ""
    daily_color = UP_COLOR if daily_pnl > 0 else (DOWN_COLOR if daily_pnl < 0 else T["muted"])
    daily_sign = "+" if daily_pnl > 0 else ""

    # ---- 오늘의 거래 요약 (매수/매도 총금액) ----
    buy_tx = today_tx[today_tx["구분"] == "매수"].copy()
    sell_tx = today_tx[today_tx["구분"] == "매도"].copy()
    buy_total_amt = (pd.to_numeric(buy_tx["수량"], errors="coerce") * pd.to_numeric(buy_tx["단가"], errors="coerce")).sum()
    sell_total_amt = (pd.to_numeric(sell_tx["수량"], errors="coerce") * pd.to_numeric(sell_tx["단가"], errors="coerce")).sum()
    total_trade_count = len(today_tx)

    daily_trade_html = f"""
    <div class="daily-trade-box">
        <div class="daily-trade-count">일일거래 총 {total_trade_count}회
            <span>(매수 {len(buy_tx)}건 · 매도 {len(sell_tx)}건)</span>
        </div>
        <div class="daily-trade-row"><span class="tag-label" style="color:{UP_COLOR}">매수</span>
            <span class="trade-chip"><b>{buy_total_amt:,.0f}원</b></span></div>
        <div class="daily-trade-row"><span class="tag-label" style="color:{DOWN_COLOR}">매도</span>
            <span class="trade-chip"><b>{sell_total_amt:,.0f}원</b></span></div>
    </div>
    """

    st.markdown(f"""
    <div class="summary-box">
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
        <div class="capital-line">최초 자본 10,000,000원 대비&nbsp;
            <b style="color:{cap_color}">{cap_sign}{capital_return:,.0f}원 ({cap_sign}{capital_return_pct:.2f}%)</b>
        </div>
        {daily_trade_html}
    </div>
    """, unsafe_allow_html=True)

    # ---- 섹터 비중 도넛 + 목표 비중 관리 ----
    with st.expander("섹터 비중 보기", expanded=False):
        include_cash = st.toggle("예수금 포함", value=st.session_state.get("include_cash", True), key="cash_toggle")
        st.session_state["include_cash"] = include_cash

        # 도넛/막대 공통 색상: 주식(예수금 제외) 비중 기준으로 순위를 매겨 고정 배정
        stock_weights = compute_sector_weights(df)  # {섹터그룹: 주식 총자산 대비 %}
        stock_weight_rank = sorted(stock_weights.items(), key=lambda x: x[1], reverse=True)
        color_map = {name: SECTOR_PALETTE[i % len(SECTOR_PALETTE)] for i, (name, _) in enumerate(stock_weight_rank)}

        df_grp = df.copy()
        df_grp["섹터그룹"] = df_grp["섹터"].apply(group_sector)
        sector_val = df_grp.groupby("섹터그룹")["평가금액"].sum().to_dict()
        if include_cash and state["cash"] > 0:
            sector_val[CASH_LABEL] = state["cash"]
        sector_items = sorted(sector_val.items(), key=lambda x: x[1], reverse=True)
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
            st.markdown('<div style="height:22px"></div>', unsafe_allow_html=True)

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
                color = color_map.get(name, "#888")
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
                        f'<div class="sector-bar-fill" style="background:{color}; width:{width_pct}%"></div>'
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

                        fig2, ax2 = plt.subplots(figsize=(4.6, 2.2))
                        fig2.patch.set_alpha(0)
                        ax2.set_facecolor("none")
                        x2 = list(range(len(dates)))
                        ax2.plot(x2, vals, color=color, linewidth=2.0, marker="o", markersize=3)
                        ax2.plot([x2[-1]], [vals[-1]], marker="o", markersize=7, color=color)
                        for xi, yi in zip(x2, vals):
                            ax2.annotate(f"{yi:.1f}%", (xi, yi), textcoords="offset points", xytext=(0, 7),
                                         ha="center", fontsize=8, color=T["text"])
                        if target is not None:
                            ax2.axhline(target, color=T["muted2"], linewidth=1, linestyle="--")
                        ax2.set_ylim(0, 40)
                        ax2.set_xticks(x2)
                        ax2.set_xticklabels([d[5:] for d in dates], fontsize=8, color=T["muted"])
                        ax2.tick_params(axis="y", labelsize=8, colors=T["muted"])
                        for spine in ax2.spines.values():
                            spine.set_visible(False)
                        ax2.grid(axis="y", color=T["border"], linewidth=0.6)
                        st.pyplot(fig2, use_container_width=True)
                        plt.close(fig2)
                    else:
                        st.info("시세 새로고침 또는 거래 기록을 하면 그날의 섹터 비중이 저장되어 추이가 쌓입니다.")

    # ---- Up/Down: 청산 종목 추적 ----
    with st.expander("Up/Down", expanded=False):
        mode = st.radio("모드", ["DOWN", "UP"], horizontal=True,
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

        results = st.session_state.get("updown_results")
        checked_at = st.session_state.get("updown_checked_at")

        if results is None:
            st.caption("새로고침을 누르면 청산(완전 매도)된 종목의 현재가를 마지막 매도가와 비교합니다.")
        else:
            if checked_at:
                st.caption(f"마지막 조회: {checked_at}")
            threshold = 3.0
            if mode == "DOWN":
                filtered = sorted([r for r in results if r["pct"] <= -threshold], key=lambda r: r["pct"])
                color = DOWN_COLOR
            else:
                filtered = sorted([r for r in results if r["pct"] >= threshold], key=lambda r: -r["pct"])
                color = UP_COLOR

            if not filtered:
                st.caption("조건에 해당하는 종목이 없습니다.")
            else:
                rows_html = "".join(
                    f'<div class="updown-row"><span class="name">{r["종목명"]}</span>'
                    f'<span class="pct" style="color:{color}">{"+" if r["pct"] >= 0 else ""}{r["pct"]:.1f}%</span>'
                    f'<span class="detail">{r["매도가"]:,.0f} → {r["현재가"]:,.0f}</span></div>'
                    for r in filtered
                )
                st.markdown(rows_html, unsafe_allow_html=True)

    # ---- 종목별 보유현황 ----
    SORT_OPTIONS = {"비중": "weight", "섹터": "sector", "현재가": "price",
                     "평가금액": "valuation", "손익": "profit"}
    if "sort_mode" not in st.session_state:
        st.session_state.sort_mode = "weight"

    last_updated = ""
    updated_vals = [v for v in df["업데이트시각"].tolist() if v]
    if updated_vals:
        last_updated = max(updated_vals)

    col_title2, col_updated = st.columns([2, 1.3])
    with col_title2:
        st.markdown("##### 종목별 보유현황")
    with col_updated:
        st.markdown(
            f"<div style='text-align:right;font-size:11px;color:{T['muted2']};padding-top:10px;'>{last_updated}</div>",
            unsafe_allow_html=True,
        )

    labels = list(SORT_OPTIONS.keys())
    cur_label = next(k for k, v in SORT_OPTIONS.items() if v == st.session_state.sort_mode)
    chosen = st.radio("정렬 기준", labels, index=labels.index(cur_label),
                       horizontal=True, label_visibility="collapsed", key="sort_radio")
    st.session_state.sort_mode = SORT_OPTIONS[chosen]

    sector_color_map = {}
    for i, s in enumerate(df.sort_values("평가금액", ascending=False)["섹터"].unique()):
        sector_color_map[s] = SECTOR_PALETTE[i % len(SECTOR_PALETTE)]

    mode = st.session_state.sort_mode
    if mode == "sector":
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

    rows = df_sorted.to_dict("records")

    if not rows:
        st.info("보유 종목이 없습니다. '거래 기록' 탭에서 매수를 기록해보세요.")
    else:
        for r in rows:
            pc = UP_COLOR if r["손익"] >= 0 else DOWN_COLOR
            psign = "+" if r["손익"] >= 0 else ""
            cc = UP_COLOR if r["등락률"] >= 0 else DOWN_COLOR
            csign = "+" if r["등락률"] >= 0 else ""
            sc = sector_color_map.get(r["섹터"], "#6b7280")

            st.markdown(f"""
            <div class="stock-card">
                <div class="stock-top">
                    <span><span class="stock-name">{r['종목명']}</span>
                        <span class="stock-weight-inline">비중 {r['비중']:.1f}%</span></span>
                    <span class="sector-tag" style="background:{sc}22;color:{sc}">{r['섹터']}</span>
                </div>
                <div class="stock-grid">
                    <div class="cell"><div class="top">{r['수량']:.0f}주</div></div>
                    <div class="cell"><div class="top">{r['현재가']:,.0f}</div><div class="bottom">{r['평단가']:,.0f}</div></div>
                    <div class="cell"><div class="top">{r['평가금액']:,.0f}</div><div class="bottom">{r['매입금액']:,.0f}</div></div>
                    <div class="cell">
                        <div class="top" style="color:{pc}">{psign}{r['손익']:,.0f}</div>
                        <div class="bottom"><span style="color:{pc}">{psign}{r['손익률']:.1f}%</span> <span style="color:{cc}">{csign}{r['등락률']:.1f}%</span></div>
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)

    # (시세 새로고침 버튼은 상단으로 이동했습니다)



# ==================================================================== #
# 탭 2: 거래 기록 + 자산 추이
# ==================================================================== #
with tab_tx:
    df3, stock_val3, total_assets3, unreal3 = compute_metrics(holdings, state["cash"])
    cap_return3 = total_assets3 - state["initial"]
    cap_return_pct3 = (cap_return3 / state["initial"] * 100) if state["initial"] else 0
    c3 = UP_COLOR if cap_return3 >= 0 else DOWN_COLOR
    s3 = "+" if cap_return3 >= 0 else ""

    total_realized = pd.to_numeric(tx.loc[tx["구분"] == "매도", "실현손익"], errors="coerce").sum()
    rc = UP_COLOR if total_realized >= 0 else DOWN_COLOR
    rs = "+" if total_realized >= 0 else ""

    st.markdown(f"""
    <div class="summary-box">
        <div class="summary-label">최초 자본 10,000,000원 대비</div>
        <span class="summary-main" style="color:{c3}">{s3}{cap_return3:,.0f}원</span>
        <span class="summary-sub" style="color:{c3}">{s3}{cap_return_pct3:.2f}%</span>
        <div class="summary-grid">
            <div>현재 총자산<b>{total_assets3:,.0f}원</b></div>
            <div>실현손익 누적<b style="color:{rc}">{rs}{total_realized:,.0f}원</b></div>
            <div>미실현 손실<b style="color:{DOWN_COLOR}">-{unreal3:,.0f}원</b></div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ---- 자산 추이 그래프 ----
    st.markdown("##### 자산 추이")
    hist = load_history()
    if len(hist) >= 1:
        hist = hist.sort_values("날짜").reset_index(drop=True)

        tx_realized = tx[tx["구분"] == "매도"].copy()
        tx_realized["실현손익"] = pd.to_numeric(tx_realized["실현손익"], errors="coerce").fillna(0)

        def _cum_realized_as_of(d):
            return tx_realized.loc[tx_realized["날짜"] <= d, "실현손익"].sum()

        profit_series = hist["날짜"].apply(_cum_realized_as_of)  # 누적 실현손익 (마이너스면 그대로 마이너스)
        loss_series = hist["조정자산"] - hist["총자산"]           # 미실현 손실 합계

        fig, ax = plt.subplots(figsize=(4.2, 2.6))
        fig.patch.set_alpha(0)
        ax.set_facecolor("none")
        x = range(len(hist))
        ax.plot(x, profit_series, color=UP_COLOR, linewidth=2.2, marker="o", markersize=3, label="실현손익")
        ax.plot(x, loss_series, color=DOWN_COLOR, linewidth=2.2, marker="o", markersize=3, label="미실현손실")
        ax.axhline(0, color=T["muted2"], linewidth=1, linestyle="--")
        ax.set_xticks(list(x))
        ax.set_xticklabels([d[5:] for d in hist["날짜"]], fontsize=8, color=T["muted"])
        ax.tick_params(axis="y", labelsize=8, colors=T["muted"])
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.grid(axis="y", color=T["border"], linewidth=0.6)
        st.pyplot(fig, use_container_width=True)
        plt.close(fig)
        st.markdown(f"""
        <div class="legend-wrap">
            <div class="legend-item"><span class="legend-dot" style="background:{UP_COLOR}"></span>누적 실현손익</div>
            <div class="legend-item"><span class="legend-dot" style="background:{DOWN_COLOR}"></span>미실현손실 합계</div>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.info("시세를 새로고침하면 그날의 자산 스냅샷이 하나씩 쌓여 그래프가 그려집니다.")

    st.divider()

    # ---- 매매일지 CSV 업로드 ----
    st.markdown("##### 매매일지 업로드 (CSV)")
    st.caption("같은 날짜에 다시 올리면 그날 내용은 최신 업로드로 교체되고, 다른 날짜 기록은 그대로 유지됩니다.")

    up_file = st.file_uploader("CSV 파일 선택", type=["csv"], key="daily_csv_uploader")
    up_date = st.date_input("거래 날짜", value=date.today(), key="daily_csv_date")

    if up_file is not None:
        file_bytes = up_file.getvalue()
        file_hash = hashlib.sha256(file_bytes).hexdigest()
        import_log = load_import_log()
        already = file_hash in import_log["hash"].values

        if already:
            st.warning("이 파일은 이미 반영된 것 같아요 (동일 파일이 감지됨).")
            force = st.checkbox("그래도 다시 반영하기", key="daily_csv_force")
        else:
            force = True

        if st.button("반영하기", key="daily_csv_apply", use_container_width=True, disabled=(already and not force)):
            try:
                parsed = parse_broker_daily_csv(file_bytes)
            except Exception as e:
                st.error(f"CSV를 읽는 중 문제가 발생했어요: {e}")
            else:
                if parsed.empty:
                    st.warning("파일에서 종목 데이터를 찾지 못했어요. 형식을 확인해주세요.")
                else:
                    trade_date_str = up_date.strftime("%Y-%m-%d")
                    holdings2, state2, tx2, n, replaced = import_daily_trades(parsed, holdings, state, tx, trade_date_str)
                    if n == 0:
                        st.info("이 파일에는 매수/매도 내역이 없어요 (반영할 거래가 없음).")
                    else:
                        save_holdings(holdings2)
                        save_state(state2)
                        save_transactions(tx2)

                        df5, val5, total5, unreal5 = compute_metrics(holdings2, state2["cash"])
                        snapshot_history(total5, total5 + unreal5)
                        snapshot_sector_history(compute_sector_weights(df5))

                        import_log = pd.concat([import_log, pd.DataFrame([{
                            "hash": file_hash, "날짜": trade_date_str, "처리시각": now_kst_str(),
                        }])], ignore_index=True)
                        save_import_log(import_log)

                        msg = f"{n}건의 거래를 반영했어요 ({trade_date_str} 기준)."
                        if replaced:
                            msg += f" (같은 날짜에 이미 있던 CSV 기록 {replaced}건은 이번 내용으로 교체됨)"
                        st.success(msg)
                        st.rerun()

    with st.expander("포트폴리오 다시 계산하기 (문제 생겼을 때만)", expanded=False):
        st.caption("보유 수량/현금이 이상하게 꼬였을 때 사용하세요. 거래 기록(transactions.csv) 전체를 "
                   "처음부터 다시 재생해서 보유종목/현금/실현손익을 정확히 재계산합니다. "
                   "거래 기록 자체는 지워지지 않습니다.")
        if st.button("지금 다시 계산하기", key="rebuild_btn", use_container_width=True):
            holdings_r, state_r, tx_r = rebuild_portfolio_from_transactions(tx, state.get("initial", 10_000_000.0))
            save_holdings(holdings_r)
            save_state(state_r)
            save_transactions(tx_r)
            df_r, val_r, total_r, unreal_r = compute_metrics(holdings_r, state_r["cash"])
            snapshot_history(total_r, total_r + unreal_r)
            snapshot_sector_history(compute_sector_weights(df_r))
            st.success("거래 기록 기준으로 포트폴리오를 다시 계산했어요.")
            st.rerun()

    with st.expander("GitHub 동기화 상태 (진단용)", expanded=False):
        token_set, repo_set, branch_set, _ = _github_cfg()
        if not token_set:
            st.warning("GitHub secrets(token/repo)가 설정되어 있지 않아요 — 지금은 로컬 저장만 되고 있어요.")
        else:
            st.caption(f"레포: {repo_set} ({branch_set} 브랜치)로 동기화 중")
        sync_log = st.session_state.get("github_sync_log", [])
        if not sync_log:
            st.caption("아직 이번 세션에서 GitHub 동기화가 시도된 적이 없어요.")
        else:
            for entry in reversed(sync_log[-15:]):
                icon = "✅" if entry["ok"] else "❌"
                line = f"{icon} [{entry['action']}] {entry['file']}"
                if entry["detail"]:
                    line += f" — {entry['detail']}"
                st.caption(line)

    st.divider()

    # ---- 거래 내역 (캘린더) ----
    st.markdown("##### 거래 내역")

    if "cal_year" not in st.session_state:
        st.session_state.cal_year = now_kst().year
        st.session_state.cal_month = now_kst().month
    if "selected_tx_date" not in st.session_state:
        st.session_state.selected_tx_date = today_kst_str()

    tx_dates = set(tx["날짜"].astype(str))
    year, month = st.session_state.cal_year, st.session_state.cal_month

    nav1, nav2, nav3 = st.columns([1, 3, 1])
    with nav1:
        if st.button("◀", key="cal_prev", use_container_width=True):
            m, y = month - 1, year
            if m < 1:
                m, y = 12, y - 1
            st.session_state.cal_month, st.session_state.cal_year = m, y
            st.rerun()
    with nav2:
        st.markdown(
            f"<div style='text-align:center;font-weight:700;padding-top:6px;color:{T['text']}'>"
            f"{year}년 {month}월</div>",
            unsafe_allow_html=True,
        )
    with nav3:
        if st.button("▶", key="cal_next", use_container_width=True):
            m, y = month + 1, year
            if m > 12:
                m, y = 1, y + 1
            st.session_state.cal_month, st.session_state.cal_year = m, y
            st.rerun()

    last_day = calendar.monthrange(year, month)[1]

    if st.session_state.selected_tx_date.startswith(f"{year:04d}-{month:02d}"):
        cur_day = int(st.session_state.selected_tx_date.split("-")[2])
    else:
        cur_day = min(now_kst().day, last_day) if (year, month) == (now_kst().year, now_kst().month) else 1

    st.markdown('<div class="cal-grid">', unsafe_allow_html=True)
    wd_cols = st.columns(7)
    for i, wd in enumerate(["일", "월", "화", "수", "목", "금", "토"]):
        wd_cols[i].markdown(
            f"<div style='text-align:center;font-size:10.5px;color:{T['muted2']}'>{wd}</div>",
            unsafe_allow_html=True,
        )

    cal_obj = calendar.Calendar(firstweekday=6)
    weeks = cal_obj.monthdayscalendar(year, month)
    for week in weeks:
        cols = st.columns(7)
        for i, day in enumerate(week):
            with cols[i]:
                if day == 0:
                    st.write("")
                    continue
                d_str = f"{year:04d}-{month:02d}-{day:02d}"
                has_tx = d_str in tx_dates
                is_sel = day == cur_day
                label = f"{day}●" if has_tx else f"{day}"
                if st.button(label, key=f"day_{d_str}", use_container_width=True,
                             type="primary" if is_sel else "secondary"):
                    st.session_state.selected_tx_date = d_str
                    st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

    st.divider()

    sel = st.session_state.selected_tx_date
    day_tx = tx[tx["날짜"].astype(str) == sel]
    day_realized = pd.to_numeric(day_tx.loc[day_tx["구분"] == "매도", "실현손익"], errors="coerce").sum()

    head_html = f"<b style='color:{T['text']}'>{sel}</b>"
    if day_realized:
        rc = UP_COLOR if day_realized >= 0 else DOWN_COLOR
        rs = "+" if day_realized >= 0 else ""
        head_html += f" <span style='color:{rc};font-size:13px'>({rs}{day_realized:,.0f}원)</span>"
    st.markdown(head_html, unsafe_allow_html=True)

    if day_tx.empty:
        st.info("이 날짜엔 기록된 거래가 없습니다.")
    else:
        for _, r in day_tx.iterrows():
            realized = r["실현손익"]
            right_html = ""
            if r["구분"] == "매도" and str(realized) not in ("", "nan"):
                rv = float(realized)
                rc = UP_COLOR if rv >= 0 else DOWN_COLOR
                rs = "+" if rv >= 0 else ""
                right_html = f'<span style="color:{rc}">{rs}{rv:,.0f}원</span>'
            memo_html = f' · {r["메모"]}' if str(r["메모"]) not in ("", "nan") else ""
            st.markdown(f"""
            <div class="tx-card">
                <div class="tx-left">
                    <span class="name">{r['종목명']}</span>
                    <span class="meta">{r['구분']} {float(r['수량']):.0f}주 @ {float(r['단가']):,.0f}원{memo_html}</span>
                </div>
                <div class="tx-right">{right_html}</div>
            </div>
            """, unsafe_allow_html=True)
