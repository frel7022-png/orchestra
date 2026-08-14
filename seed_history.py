"""
lps/ 폴더의 종목별 체결내역 전체를 모아서 transactions.csv를 새로 만들고,
처음부터 재생(replay)해서 holdings/현금을 계산하는 일회성 초기 세팅 스크립트.

사용법:
    python seed_history.py            # 미리보기만 (파일 저장 안 함)
    python seed_history.py --save     # 실제로 transactions.csv/portfolio_data.csv/account_state.csv에 저장

기본은 미리보기 모드다 — 계산 결과를 실제 계좌 스냅샷(real_holdings_snapshot.csv)과
대조해서 보여주기만 하고 파일은 건드리지 않는다. 대조 결과가 문제없을 때만 --save로
다시 실행해서 실제로 반영한다.
"""

import sys
import uuid

import pandas as pd

import portfolio_core as core

INITIAL_CAPITAL = 10_000_000.0
REAL_CASH = 6_364_635.0
LPS_DIR = core.HERE / "lps"
REAL_SNAPSHOT_FILE = core.HERE / "real_holdings_snapshot.csv"


def load_canonical_names() -> dict:
    """list.csv 기준 정확한 종목명 매핑 (소문자 키 → 정확한 표기).
    lps 파일명이 영문 종목은 소문자로 저장된 경우가 있어, 화면에 보이는 이름을
    list.csv(실제 계좌 표기)와 일치시키기 위해 사용한다."""
    list_file = core.HERE / "list.csv"
    if not list_file.exists():
        return {}
    df = pd.read_csv(list_file, encoding="cp949", skiprows=2, header=None)
    names = [n.strip() for n in df[0].astype(str).tolist()]
    return {n.lower(): n for n in names}


def build_transactions_from_lps() -> pd.DataFrame:
    canonical = load_canonical_names()
    rows = []
    errors = []
    for path in sorted(LPS_DIR.glob("*.csv")):
        stock_name = canonical.get(path.stem.lower(), path.stem)
        raw = path.read_bytes()
        try:
            parsed = core.parse_execution_log_csv(raw, stock_name)
        except Exception as e:
            errors.append(str(e))
            continue
        for _, r in parsed.iterrows():
            rows.append({
                "id": str(uuid.uuid4())[:8],
                "날짜": r["날짜"],
                "_시각": r["시각"],
                "종목명": r["종목명"],
                "구분": r["구분"],
                "수량": r["수량"],
                "단가": r["단가"],
                "실현손익": "",
                "메모": "초기 히스토리",
                "정산반영": True,
            })

    if errors:
        print(f"[경고] {len(errors)}개 파일을 건너뜀:")
        for e in errors:
            print("  -", e)

    if not rows:
        print("[오류] lps/ 에서 거래를 하나도 읽지 못했습니다.")
        sys.exit(1)

    tx = pd.DataFrame(rows)
    tx = tx.sort_values(["날짜", "_시각"]).drop(columns=["_시각"]).reset_index(drop=True)
    return tx[core.TX_COLUMNS]


def compare_with_real_snapshot(holdings: pd.DataFrame, state: dict):
    if not REAL_SNAPSHOT_FILE.exists():
        print(f"[알림] {REAL_SNAPSHOT_FILE.name}이 없어서 실제 스냅샷과 대조를 건너뜁니다.")
        return

    real = pd.read_csv(REAL_SNAPSHOT_FILE, encoding="cp949")
    real["종목명_norm"] = real["종목명"].astype(str).str.strip().str.lower()
    real_qty = dict(zip(real["종목명_norm"], real["잔고수량"]))

    computed = holdings.copy()
    computed["종목명_norm"] = computed["종목명"].astype(str).str.strip().str.lower()
    computed_qty = dict(zip(computed["종목명_norm"], computed["수량"]))

    all_names = set(real_qty) | set(computed_qty)
    mismatches = []
    for n in sorted(all_names):
        rq = real_qty.get(n, 0)
        cq = computed_qty.get(n, 0)
        if int(rq) != int(round(cq)):
            mismatches.append((n, rq, cq))

    print("--- 종목 수량 대조 (실제 스냅샷 vs replay 계산) ---")
    print(f"실제 종목 수: {len(real_qty)}  /  replay 계산 종목 수: {len(computed_qty)}")
    if mismatches:
        print(f"[불일치 {len(mismatches)}건]")
        for n, rq, cq in mismatches:
            print(f"  - {n}: 실제={rq}  replay계산={cq}")
    else:
        print("전부 일치!")

    print()
    print("--- 현금 대조 ---")
    print(f"replay 계산 예수금: {state['cash']:,.0f}원")
    print(f"실제 예수금(사용자 제공): {REAL_CASH:,.0f}원")
    print(f"차액: {state['cash'] - REAL_CASH:,.0f}원")


def main():
    save = "--save" in sys.argv

    tx = build_transactions_from_lps()
    print(f"총 {len(tx)}건의 거래를 lps/ 에서 읽었습니다 (종목 {tx['종목명'].nunique()}개).")

    total_volume = (tx["수량"].astype(float) * tx["단가"].astype(float)).sum()
    no_fee_holdings, no_fee_state, _ = core.rebuild_portfolio_from_transactions(tx, INITIAL_CAPITAL)
    gap = no_fee_state["cash"] - REAL_CASH
    fee_rate = max(gap, 0) / total_volume if total_volume else 0.0
    print(f"거래대금 합계: {total_volume:,.0f}원 / 수수료 추정 갭: {gap:,.0f}원 "
          f"→ 적용 수수료율: {fee_rate*100:.4f}%")

    holdings, state, tx = core.rebuild_portfolio_from_transactions(tx, INITIAL_CAPITAL, fee_rate)
    print(f"replay 결과(수수료 적용): 보유종목 {len(holdings)}개, 예수금 {state['cash']:,.0f}원")
    print()
    compare_with_real_snapshot(holdings, state)

    if not save:
        print()
        print("[미리보기 모드] 파일을 저장하지 않았습니다. 대조 결과에 문제가 없으면 "
              "'python seed_history.py --save'로 다시 실행하세요.")
        return

    core.save_transactions(tx)
    core.save_holdings(holdings)
    core.save_state(state)
    print()
    print("[저장 완료] transactions.csv / portfolio_data.csv / account_state.csv")


if __name__ == "__main__":
    main()
