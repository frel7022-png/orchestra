"""both_accounts.csv 동기화 — new1(orchestra) + meritz(orchestration) 계좌수익(anchor=8/14=0
리베이스) 시계열을 한 파일로 합쳐 두 레포에 똑같이 써준다. §6-21 "VIP vs Orchestra vs Orchestration".

매매일지 반영(ingest_daily.py)을 어느 앱이든 돌린 뒤 세션이 실행:
    python sync_both_accounts.py
그다음 new1 / meritz 두 레포에서 각각 both_accounts.csv를 git commit/push.

**장 마감(15:30 KST) 후에 돌릴 것 (2026-09-10)**: both_accounts.csv는 이제 "마감된 날들의
확정 히스토리 + VIP 패널 선그래프 소스" 역할. 장중에 돌리면 그 시각 값으로 얼어서 부정확.
라이브 "오늘 점"은 Supabase account_snapshot(§6-21 런타임 채널)이 공급하므로, 이 파일은
확정 종가 기준으로만 갱신하면 된다.

메리츠 쪽 계좌수익은 meritz 폴더에서 그 repo의 portfolio_core로 계산해야 하므로 subprocess로
불러온다(모듈 이름이 겹쳐서 같은 프로세스에서 둘 다 import 못 함).
"""

import subprocess
import sys
from pathlib import Path

import pandas as pd

import portfolio_core as core

MERITZ_DIR = Path(r"C:\Users\frel\Desktop\meritz")
_TMP = "_acct_series.csv"


def _new1_series() -> pd.DataFrame:
    tx, st = core.load_transactions(), core.load_state()
    r = core.compute_index_vs_account(
        tx, core.load_history(), core.load_index_history(),
        st["initial"], st.get("fee_rate", 0.0), kospi_weight=None)
    me = r["me"][["날짜", "계좌수익"]].copy()
    me.columns = ["날짜", "orchestra"]
    me["날짜"] = me["날짜"].astype(str)
    return me


def _meritz_series() -> pd.DataFrame:
    code = (
        "import portfolio_core as c\n"
        "tx, st = c.load_transactions(), c.load_state()\n"
        "r = c.compute_index_vs_account(tx, c.load_dom_asset_history(), c.load_index_history(),"
        " st['initial'], st.get('fee_rate_krw', 0.0), st.get('fee_rate_usd', 0.0))\n"
        "me = r['me'][['\\ub0a0\\uc9dc', '\\uacc4\\uc88c\\uc218\\uc775']].copy()\n"
        "me['\\ub0a0\\uc9dc'] = me['\\ub0a0\\uc9dc'].astype(str)\n"
        f"me.to_csv(r'{MERITZ_DIR / _TMP}', index=False)\n"
    )
    subprocess.run([sys.executable, "-c", code], cwd=str(MERITZ_DIR), check=True)
    m = pd.read_csv(MERITZ_DIR / _TMP)
    (MERITZ_DIR / _TMP).unlink(missing_ok=True)
    m.columns = ["날짜", "orchestration"]
    m["날짜"] = m["날짜"].astype(str)
    return m


def main() -> None:
    both = pd.merge(_new1_series(), _meritz_series(), on="날짜", how="outer").sort_values("날짜")
    for target in (Path("both_accounts.csv"), MERITZ_DIR / "both_accounts.csv"):
        both.to_csv(target, index=False)
        print(f"[완료] {target}  ({len(both)}일)")
    print(both.tail(3).to_string(index=False))


if __name__ == "__main__":
    main()
