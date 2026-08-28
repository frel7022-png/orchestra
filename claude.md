# 한국 주식 포트폴리오 트래커 — 프로젝트 개요

개인용 Streamlit 웹앱. 국내 증권사(메리츠증권) 매매일지 CSV를 업로드하면
보유종목/현금/손익을 자동 반영하고, 섹터별 비중과 목표 대비 현황을 보여준다.
GitHub 레포와 동기화되어 Streamlit Cloud 재배포에도 데이터가 유지된다.

이 문서는 이전 버전(Streamlit 채팅으로 반복 수정하며 꼬였던 버전)에서 얻은
교훈을 정리한 것. **아래 "반드시 지킬 것"은 실제로 겪었던 버그의 재발 방지책이므로
가볍게 넘기지 말 것.**

> **문제가 안 풀리거나 이상하게 동작할 때**: 이 문서엔 "지금 어떻게 동작하는가"만
> 담겨있다. "왜 이렇게 됐는지"(제거된 기능, 조사하다 보류한 이슈, 과거 설계 변경
> 경위·디버깅 서사)는 전부 같은 폴더의 **`ARCHIVE.md`**로 옮겨뒀다 — 평소엔 안 읽어도
> 되지만, 실마리가 안 보이면 거기부터 찾아볼 것. 같은 번호(§1-7, §6-9 등)로 이 문서와
> `ARCHIVE.md`를 연결해뒀다. (2026-08-25, 이 문서가 계속 길어지는 걸 보고 사용자가
> 요청 — "평소엔 안 읽는데 가끔 결정적인 단서" 성격의 내용을 분리한 것. 파일을 나눈 건
> "토큰 절약"이 목적이 아니라(어차피 CLAUDE.md 전체는 세션마다 자동으로 로드됨) 사람이
> 읽기 편하게 하려는 목적 — ARCHIVE.md는 필요할 때만 세션이 일부러 열어봐야 함.)

---

## 1. 반드시 지킬 것 (실제 겪은 버그 → 원칙)

### 1-1. 거래 기록(transactions)이 유일한 진실 공급원(source of truth)
- 보유종목(holdings/portfolio)은 **transactions를 재생(replay)해서 계산되는 파생 데이터**로 취급한다.
  절대로 holdings를 독립적으로 손으로 고치거나 별도 로직으로 갱신하지 말 것 — 다음 재계산 때 덮어써져서
  무의미해진다.
- 재계산 로직(rebuild): `초기자본 → 빈 holdings` 상태에서 시작해, transactions를
  **날짜순 → 같은 날짜 내 입력순**으로 정렬해 하나씩 재생하며 holdings/cash/실현손익을 계산한다.

### 1-2. 증권사 CSV는 "그날 하루 전체 누적"이다 — 델타가 아니다
- 매매일지 CSV의 "금일매수/금일매도"는 **그날 지금까지의 전체 누적**이지, 마지막 업로드 이후의
  증분이 아니다. 같은 날짜 CSV를 여러 번 올리면 매번 전체가 다시 들어있다.
- 따라서 CSV 업로드 시: **같은 날짜에 이미 반영된 CSV 기반 거래가 있으면 전부 삭제하고, 이번 업로드
  내용으로 교체**한 뒤 전체를 재계산해야 한다. "추가(append)"하면 반드시 중복 누적된다 (실제로 겪음:
  네이버 1주가 3주로 뻥튀기됨).
- 다른 날짜의 기록은 건드리지 않는다.

### 1-3. "거래로부터 유추 불가능한 메타데이터"는 별도 영구 캐시로 분리
- 종목코드, 섹터 같은 정보는 transactions.csv 안에 없다 (종목명/수량/단가만 있음).
- 만약 이런 정보를 holdings 생성 시점에만 채워 넣으면, **재계산할 때마다 초기화되어 날아간다**
  (실제로 겪음: CSV 업로드할 때마다 종목코드/섹터가 전부 리셋됨 → 시세조회 매번 처음부터 다시 검색
  → 느려짐 + 섹터 전부 "미분류"로 리셋).
- 해결: `stock_code_cache.csv` (종목명→종목코드), `stock_sector_cache.csv` (종목명→섹터) 같은
  **독립적인 영구 캐시 파일**을 두고, holdings를 재생성할 때마다 이 캐시에서 먼저 조회한다.
  캐시는 한 번 채워지면 재계산해도 사라지지 않는다.

### 1-4. 시세 API는 한 번에 너무 많이 요청하면 잘린다
- 네이버 realtime 시세 API(`polling.finance.naver.com/api/realtime/domestic/stock/{코드1,코드2,...}`)에
  종목코드를 한꺼번에 다 이어붙여서 요청하면, 종목 수가 많을 때(40개 이상) 응답이 일부만 온다
  (실제로 겪음: 65개 중 19개만 갱신됨).
- 해결: **20개씩 청크(chunk)로 나눠서 여러 번 요청** 후 결과를 합친다.

### 1-5. GitHub 동기화 — 로컬 디스크는 언제든 초기화될 수 있다
- Streamlit Cloud는 재배포/재시작 시 로컬 파일시스템을 완전히 새로 만든다. CSV를 로컬에만 저장하면
  다음 재배포 때 전부 사라진다 (실제로 겪음: 코드 배포 한 번으로 하루치 매매기록 소실).
- 원칙: **모든 CSV 저장(write) 시 GitHub 레포에도 동일 내용을 커밋**하고, **로드(read) 시 로컬에
  파일이 없으면 먼저 GitHub에서 받아온 뒤** 읽는다.
- 커밋 실패를 **절대 조용히 무시(`except: pass`)하지 말 것.** 실패 이유(토큰 없음/권한 없음/404/네트워크
  오류 등)를 로그로 남기고 사용자가 확인할 수 있는 화면을 반드시 둔다 (실제로 이게 없어서 원인 파악에
  오래 걸림).
- GitHub 설정은 Streamlit secrets에 둔다:
  ```toml
  [github]
  token = "ghp_..."
  repo = "owner/reponame"
  branch = "main"
  # path_prefix = "data/"   # 레포 하위 폴더에 저장할 경우만
  ```

### 1-6. 증권사 CSV 파싱 원칙
- 인코딩은 **cp949**(한글 윈도우 기본)인 경우가 많다. cp949 → utf-8-sig → utf-8 순으로 시도.
- 컬럼명이 병합 헤더 + 개행문자로 지저분하다 (`"금일매수","",""` 처럼 하위헤더가 별도 행에 있음).
  **컬럼명이 아니라 열 위치(순서)로 파싱**해야 안정적이다.
- 첫 데이터 행은 실제 데이터가 아니라 "평균가/수량/매입금액" 같은 서브헤더인 경우가 있음 — 건너뛸 것.
- **CSV를 다운로드할 때 전체 종목이 다 포함됐는지 항상 확인.** 실제로 겪음: 특정 내보내기가 30개로
  잘려서 일부 종목의 거래가 통째로 누락됨 (파싱 실패가 아니라 사용자가 받은 파일 자체가 불완전했음).

### 1-7. 수동 입력 UI를 만들지 않는다 (CSV 업로드가 유일한 데이터 입력 경로)
- "종목 편집"(수동 종목 추가/평단가/섹터 editor), "새 거래 기록하기"(수동 매수/매도 입력 폼) 둘 다
  한 번씩 만들었다가 "CSV로만 관리할 거라 필요 없다"며 삭제 요청받았음.
- **결론: 이 앱은 수동 입력 UI가 거의 없고, CSV 업로드가 유일한 데이터 입력 경로**라는 게 사용자의
  명확한 선호. 재구현 시 이 원칙을 지킬 것 (불필요한 수동 입력 폼을 다시 만들지 말 것).
  (예외 아님, 참고: §6-6 "Fishing" 관심종목 기능도 CSV 반영 방식을 그대로 따름 — 보유/거래
  상태를 바꾸는 UI가 아니라 순수 조회용이라 이 원칙과 충돌하지 않음.)
- 섹터만 고칠 수 있는 가벼운 도구가 필요할 수 있음 — **경위와 현재 상태는 `ARCHIVE.md` §1-7 참고**
  (한때 있었다고 문서에 적혀있었는데 실제로는 없었던 사례가 있었음).

---

## 2. 실제 파일 구조 (2026-08-18 리팩터로 적용됨)

`app.py`가 919줄에 함수 2개뿐인 절차형 스크립트였던 걸 아래처럼 분리했다 — 이제 실제로
이렇게 되어있다 (예전엔 이 섹션이 "권장안"이었는데, 지금은 실제 구조):

```
app.py                 # 진입점. 페이지 설정, CSS, 로그인, 데이터 로드, 새로고침 핸들러, 탭 조립만.
constants.py            # 색상/팔레트/섹터 목표비중/테마 — 순수 데이터, 로직 없음.
portfolio_core.py        # 데이터 계층 전체(로드/저장/replay/시세조회/CSV파싱/지표계산).
                          #   ingest_daily.py와 공유해서 쓰므로 일부러 안 쪼갬 — 이미 함수 단위로
                          #   잘 분리돼 있고, storage/market_data/portfolio/csv_import로 더
                          #   쪼개는 건 이 파일 규모(약 670줄)에 비해 실익이 적다고 판단함.
ui_portfolio_tab.py       # render_portfolio_tab() — 요약카드/섹터비중/Up-Down/종목현황/Volume/Foreigner.
ui_transactions_tab.py     # render_transactions_tab() — 실현손익 그래프/거래 캘린더/누적요약.
ingest_daily.py              # 일일 매매일지 CSV 반영 스크립트 (§6-2 참고, 내부적으로 rebuild_portfolio_incremental 사용 — §6-14).
db_fetch_daily_prices.py       # watchlist 153종목 시세/거래량/수급을 매일 Supabase에 적재하는 cron 스크립트 (§6-9, §6-12 참고).
tests/                            # pytest 회귀 테스트 (§6-11 참고).
```

`portfolio.py`/`storage.py`/`market_data.py`/`csv_import.py`로의 추가 분리는 하지 않기로
했음 — `portfolio_core.py` 하나로 충분히 관리 가능하다고 판단(2026-08-18). 앞으로 이 파일이
많이 커지면 그때 다시 고려.

---

## 3. 데이터 파일 스키마

| 파일 | 역할 | 주요 컬럼 |
|---|---|---|
| `transactions.csv` | **유일한 진실 공급원.** 모든 매수/매도 이력 | id, 날짜, 종목명, 구분(매수/매도), 수량, 단가, 실현손익, 메모, 정산반영 |
| `portfolio_data.csv` | transactions에서 파생된 현재 보유종목 스냅샷 | 종목명, 종목코드, 섹터, 수량, 평단가, 현재가, 등락률, 업데이트시각 |
| `account_state.csv` | 현금/초기자본 | 예수금, 최초자본 |
| `asset_history.csv` | 일별 총자산 스냅샷 (새로고침/거래 시마다 오늘자 갱신) | 날짜, 총자산, 조정자산 |
| `sector_history.csv` | 일별 섹터그룹 비중 스냅샷 | 날짜, 섹터그룹, 비중 |
| `stock_code_cache.csv` | 종목코드 영구 캐시 | 종목명, 종목코드 |
| `stock_sector_cache.csv` | 섹터 영구 캐시 (수동 지정 포함) | 종목명, 섹터 |
| `import_log.csv` | CSV 업로드 중복 방지 로그 | hash, 날짜, 처리시각 |
| `watchlist.csv` | Fishing 관심종목 리스트(§6-6) | 종목명, 종목코드 |
| `checkpoint_holdings.csv` | transactions 재생 체크포인트 — 보유종목 스냅샷(§6-14) | portfolio_data.csv와 동일 컬럼 |
| `checkpoint_state.csv` | transactions 재생 체크포인트 — 현금/자본 스냅샷(§6-14) | 체크포인트날짜, 예수금, 초기자본, fee_rate |

**Supabase(DB) 스키마는 §6-9/§6-12에 별도로 있음** — 로컬 CSV가 아니라 클라우드 DB라 여기 표에는 안 넣음.

---

## 4. 핵심 기능

### 포트폴리오 탭
- 총자산 요약 카드 (최초자본 대비 손익, 현재 총자산, 실현손익 누적, 미실현손실)
- 자산 추이 그래프
- 섹터 비중: 도넛차트 + 섹터별 막대(0~40% 고정 스케일, 목표선 표시, 전일 대비 변화) + 섹터 클릭 시
  일별 추이(0~40% y축, raw 값 표시)
- Up/Down: 완전 청산한 종목 중 마지막 매도가 대비 ±3% 이상 변한 것 추적 (전용 새로고침 버튼,
  자동 실행 안 됨)
- Fishing: 관심종목 ±3% 스크리너 (§6-6)
- Volume / Foreigner: 거래량·외국인 수급 이상치 스크리너 (§6-12)
- 종목별 보유현황 카드 (비중은 예수금 포함 총자산 대비) — 카드의 WATERING 칩으로 매수/매도 내역 +
  물타기 그래프 상세를 펼침 (§6-10)

### 거래 기록 탭
- 최초 자본 대비 손익 요약 카드
- 실현손익 그래프 (누적 실현손익 vs 미실현손실 추이)
- 누적 매수/매도(건수+금액+일평균건수) + 누적 실현손익(금액+매수대비%) 요약 (§6-13)
- 거래 내역 캘린더

이 탭들의 "정확한 동작"은 코드가 최신이고 이 섹션이 늦게 갱신될 수 있다는 걸 감안해서,
**의심스러우면 항상 §6(실제 운영 흐름)이나 코드 자체를 우선**할 것 — 예전에 이 섹션이 실제
구현과 다른 내용을 담고 있던 적이 있었음(경위는 `ARCHIVE.md` §4 참고).

---

## 5. 스타일/UX 원칙
- 모바일 웹뷰 기준 (좁은 화면), 컴팩트하고 심플한 디자인 선호
- 밝은 테마 기본, 상승=빨강/하락=파랑 (국내 관례)
- 파일 업로드 위젯 등 Streamlit 기본 다크 스타일 요소는 라이트 테마에 맞게 재정의 필요

---

## 6. 실제 운영 흐름 (세션 인수인계용)

이 섹션은 "이상적인 설계"가 아니라 **지금 실제로 이렇게 돌아가고 있다**는 사실을 적어둔 것.
새 세션은 여기부터 읽으면 바로 일을 이어갈 수 있어야 함. 각 항목의 "왜 이렇게 됐는지" 서사는
`ARCHIVE.md`의 같은 번호 아래 있음.

### 6-1. 이 저장소는 GitHub과 수동으로 동기화된다 (자동 아님)
- 앱 코드(`app.py`, `portfolio_core.py`, `ui_*.py`) 어디에도 GitHub 연동 로직이 없다.
- 실제로는 **세션(어시스턴트)이 데이터 파일을 바꿀 때마다 직접 `git add/commit/push`**를 실행해서
  origin(`https://github.com/frel7022-png/orchestra.git`, main 브랜치)에 반영해왔다.
  "GitHub에 올려줘"는 앱이 아니라 세션이 매번 수행해야 하는 수동 단계.

### 6-1-1. 로컬에서 `streamlit run app.py`를 돌리려면 빈 secrets.toml이 필요함
- `check_password()`가 `"app_password" not in st.secrets`를 검사하는데, **secrets.toml 파일
  자체가 아예 없으면** Streamlit이 빈 걸로 처리하지 않고 `StreamlitSecretNotFoundError`를 던져서
  로컬 실행이 통째로 안 된다.
- 해결: `.streamlit/secrets.toml`을 로컬에 빈 파일(또는 주석만)로 만들어두면 로그인 게이트가
  자동 통과된다. 이 파일은 `.gitignore`에 등록돼 있어 git에는 안 올라감 — 실수로 비밀번호나
  GitHub 토큰이 든 진짜 secrets.toml을 커밋할 위험도 같이 막아줌.

### 6-1-2. Streamlit Cloud 재배포 로그가 성공이어도 실제 화면 반영은 늦을 수 있음
- push 후 배포 로그에 `Updated app!`까지 정상적으로 찍혀도, **실제 화면(특히 모바일)에는
  몇 분이 아니라 하루 정도까지 늦게 반영될 수 있음**(여러 번 재발 확인됨, 경위는 `ARCHIVE.md`
  §6-1-2 참고).
- **판단 기준**: 배포 로그에 `Updated app!`가 찍혔고 에러 트레이스백이 없다면 코드는 정상
  배포된 것으로 보고 더 이상 원인을 파고들 필요 없음 — "화면에 아직 안 보인다"는 걸로
  롤백/재배포 시도하며 삽질하지 말고, 시간이 해결해준다는 걸 사용자에게 안내하고 넘어갈 것.

### 6-2. 일일 매매일지 반영 흐름 (todaytrans/)
- `todaytrans/` 폴더는 로컬 전용 스테이징 폴더 (`.gitignore`에 등록, git에는 안 올라감).
- 사용자가 증권사(메리츠) 매매일지 CSV를 `todaytrans/transactions.csv`에 덮어써서 넣어둔다.
  **세션은 이걸 실시간으로 감지하지 못하므로, 사용자가 "넣었어" 같은 짧은 메시지로 알려줘야 한다.**
- 반영 절차: `python ingest_daily.py todaytrans/transactions.csv <YYYY-MM-DD>` 실행
  (날짜는 보통 그날 당일) → 스크립트가 해당 날짜의 기존 반영분을 지우고 이번 파일로 교체 후
  전체 재계산 → 결과(보유종목 수/현금/평가금액/총자산) 확인 → 코드 미확인 종목 있으면
  `core.refresh_all_prices()`로 보충 → git commit/push.
- **이 4단계(반영→확인→코드보충→push)는 검증된 흐름이므로 매번 물어보지 말고 바로 진행할 것.**
  파싱 실패, 보유종목 수가 크게 이상함, 코드가 끝내 안 풀림, git 충돌처럼 실제 문제가 있을
  때만 멈추고 사용자에게 확인받는다.

### 6-3. 바탕화면 `backup` 폴더
- `C:\Users\frel\Desktop\backup` — git 저장소 아님, `new1`의 단순 스냅샷 복사본.
- 앱을 계속 고치는 중이라 "고침이 확실해지기 직전" 상태를 담아두는 안전망. 리스키한 수정 전에
  이 폴더를 최신화할지는 사용자에게 확인하고 진행.

### 6-4. 알려진 오차: 앱 "실현손익" vs 증권사 "당일실현"
- 수수료/세금 차감 방식 차이로 앱 쪽 실현손익이 증권사보다 항상 조금 더 크게 나옴(몇백 원 수준).
  **현금(예수금) 잔액 자체는 정확함** — 부풀려 보이는 건 "실현손익" 표시값 하나뿐.
- 사용자가 "몇백 원 차이니 넘어가자"고 명시적으로 판단. **다시 파고들 필요 없음.** 고치는 방법과
  경위는 `ARCHIVE.md` §6-4 참고 — 나중에 정확히 맞추고 싶다는 요청이 오면 거기부터 볼 것.

### 6-5. `temporary/` 폴더 — 외부 일시 데이터 스테이징
- `todaytrans/`(매매일지)처럼 특정 용도가 정해진 폴더가 아니라, **일회성으로 외부에서 들어오는
  데이터를 임시로 놔두는 범용 폴더**다. 로컬 전용(`.gitignore` 처리됨).
- 예: 관심종목(Fishing) 리스트 CSV처럼, 매일 반복되진 않지만 가끔 사용자가 외부 파일을 던져주는
  경우 여기에 넣는다. 세션은 실시간 감지를 못 하니 사용자가 알려줘야 하는 건 다른 스테이징
  폴더와 동일.

### 6-6. "Fishing" 관심종목 리스트
- 보유/거래와 완전히 무관한 **순수 관찰용 워치리스트** 기능. 포트폴리오 탭의 "섹터 비중 보기",
  "Up/Down" 밑에 "Fishing"이라는 이름의 expander로 있음 (`ui_portfolio_tab.py`).
- 데이터: `watchlist.csv`(종목명/종목코드만 저장, git에 커밋됨 — 캐시 파일들과 같은 성격).
  섹터는 별도 저장 안 하고 **`stock_sector_cache.csv`에서 매번 조회**해서 보여준다(보유종목과
  같은 캐시 재사용 — 중복 소스 만들지 않으려고 일부러 이렇게 함).
- 반영: `python import_watchlist.py <파일경로>` — `temporary/`에 사용자가 올려둔 CSV(첫 번째
  열에 종목명만 있으면 됨, 헤더명 무관)를 읽어서 `watchlist.csv`를 통째로 교체한다.
- **오탈자 자동 보정**: 종목명에 오탈자가 있을 수 있다는 전제(예: "오또기"→"오뚜기",
  "동진세미컴"→"동진쎄미켐")로, `portfolio_core.match_stock_name()`이 네이버 자동완성
  검색으로 정정한다. 네이버 자동완성은 접두어 일치라 오탈자가 있으면 그대로는 안 걸리므로,
  검색이 비면 앞부분 글자 수를 4→3→2→1로 줄여가며 재검색하고, 후보들 중 원래 입력과
  difflib 유사도가 가장 높은 것(0.5 미만이면 포기)을 고른다. 종목코드는 6자리면 되고
  숫자로만 제한하지 않음 — 우선주 등은 "37550K"처럼 끝이 영문인 코드도 있음.
- **알려진 한계**: 네이버 자동완성은 한 검색어당 최대 10개까지만 반환하고 인기도 순으로
  정렬되는 것으로 보여서, 거래량이 적은 종목은 정확한 이름을 넣어도 상위 10개 안에 아예
  안 들어오면 실패로 뜬다. 이런 경우 스크립트가 "매칭 실패" 목록으로 출력해주니, 사람이 직접
  종목코드를 찾아서 `watchlist.csv`에 손으로 한 줄 추가하면 된다.
- **동작 방식 (v3 — 최초가 소스는 Supabase DB)**: 153개 관심종목 중 **±3% 이상 움직인 종목만
  걸러서 보여주는 스크리너**, 기준(누적/전일)과 방향(DOWN/UP)을 라디오로 고를 수 있음. 종목마다
  `refresh_watchlist_prices()`가 이렇게 계산한다(반환값만 있고 **로컬 파일 저장은 안 함**):
  - **최초가**: `get_first_day_prices_db()`로 **Supabase `price_history` 테이블의 최초
    관측일(가장 이른 날짜) 종가**를 매번 새로 조회. DB에 그 종목 히스토리가 아직 없으면(막
    추가된 종목이라 §6-9의 cron이 한 번도 못 돈 경우) 네이버 `change_pct`로 역산한 "어제
    종가"를 임시로 씀 — 다음날 cron이 돌면 자동으로 정확한 값으로 바뀜.
  - **최근가/전일대비**: 매 새로고침마다 네이버 실시간 시세로 갱신(전일대비는 API가 주는
    `change_pct` 그대로, 직접 계산 안 함).
  - **누적 등락률** = `(최근가-최초가)/최초가`(화면 표시 시점에 계산, 저장 안 함), **전일 등락률**
    = `전일대비` 그대로. 그 종목의 DB 히스토리가 하루치뿐이면 최초가=전일종가라서 두 값이
    같게 나옴 — 의도된 동작.
  - UI: "기준"(누적/전일) 라디오 + "방향"(DOWN/UP) 라디오로 고른 값이 ±3% 넘는 종목만, 넘는
    정도가 큰 순으로 "종목명  누적%  전일%"(둘 다 항상 같이 표시) 형식으로 보여줌.
- v2에서 v3로 왜 바뀌었는지(실제 겪은 버그), "오늘의 순위" TOP20을 왜 없앴는지는
  `ARCHIVE.md` §6-6 참고. **결론만: 로컬 캐시 파일(`watchlist_prices.csv`)은 다시 만들거나
  최초가를 로컬 파일에 저장하는 방식으로 되돌리지 말 것.**

### 6-9. Supabase DB 파이프라인 (거래량/수급 포함)
- GitHub Actions(`.github/workflows/daily-price-fetch.yml`)가 **매 평일 07:13 UTC(=16:13
  KST, 장마감 30분 후)**에 자동 실행 → `db_fetch_daily_prices.py`가 Supabase `watchlist`
  테이블의 종목코드 전부(153개)에 대해:
  1. 시세+거래량 조회(`fetch_quotes`) → `price_history`에 upsert
  2. 종목별 외국인/기관 수급 조회(`fetch_investor_flow`, 종목당 1회 스크레이핑) → `investor_flow`에 upsert
  3. 코스피/코스닥 시장 전체 거래량+수급 조회(`fetch_market_flow`) → `market_flow`에 upsert
  사람 개입도, 로컬 PC가 켜져있을 필요도 없음. `workflow_dispatch`로 수동 실행도 가능(GitHub
  Actions 탭에서 "Run workflow", 또는 API로 직접 트리거 가능 — `gh` CLI 없이 `git credential
  fill`로 얻은 토큰 + curl로도 가능, 2026-08-25 실제로 이렇게 확인함).
  - **cron이 07:13인 이유(정각 아님)**: 정각 예약은 GitHub 자체에서 지연이 심함 — **앞으로
    이 워크플로우의 cron 시각을 다시 정각으로 되돌리지 말 것.** 자세한 경위는 `ARCHIVE.md`
    §6-9 참고.
- **`db_fetch_daily_prices.py`의 방어적 구조 (중요)**: 위 2~3단계(수급/시장베이스라인)가
  실패해도 **1단계(시세/거래량 적재)는 절대 안 깨지게 각각 try/except로 감쌈**. price_history
  upsert는 `volume` 컬럼이 아직 없으면 그 필드를 빼고 자동으로 재시도하는 fallback도 있음 —
  이 파일 고칠 때 이 방어 구조를 걷어내지 말 것(cron이 깨지면 사람이 안 챙겨도 조용히 실패한
  채로 방치되기 쉬움). 종목별 수급 조회는 153번 순차 요청(비공식 스크레이핑이라 요청 사이
  0.3초 지연을 둠).
- **DB 스키마** (Supabase 프로젝트: `frel7022-png's Project`, ID `ghpxaznihogafhvdqijw`,
  Tokyo 리전, 무료 티어):
  - `watchlist(id, user_id, stock_code, stock_name, sector, created_at)` — `user_id`는 항상
    고정 placeholder UUID(`00000000-...`)로 채움(NULL로 두면 UNIQUE 제약이 무력화되는 버그가
    있어서 — 다시 NULL 허용으로 바꾸지 말 것).
  - `price_history(id, stock_code, trade_date, close_price, change_pct, volume, created_at)`,
    `unique(stock_code, trade_date)`.
  - `investor_flow(id, stock_code, trade_date, volume, institution_net, foreign_net,
    foreign_pct, created_at)`, `unique(stock_code, trade_date)`.
  - `market_flow(id, market('KOSPI'|'KOSDAQ'), trade_date, volume(천주), individual_net,
    foreign_net, institution_net(전부 억원), created_at)`, `unique(market, trade_date)`.
  - RLS 켜져 있고 지금은 "test_all_*" 정책으로 anon key가 읽기/쓰기 다 열려있는 **테스트
    단계 상태**. 나중에 실사용 단계 가면 쓰기 권한을 cron 전용 키로 잠그는 걸 고려할 것 —
    지금 이 anon key를 가진 사람은 누구나 이 테이블들을 읽고 쓸 수 있음.
  - 접속 정보(URL/anon key)는 `.streamlit/secrets.toml`의 `[supabase]` 섹션(로컬 전용,
    git에 안 올라감)과, GitHub Actions용으로 레포 Settings → Secrets에
    `SUPABASE_URL`/`SUPABASE_ANON_KEY`로 등록돼 있음.
- **필터 빌더는 만들었다가 제거함** — 자세한 경위와 재도입 참고사항은 `ARCHIVE.md` §6-9.
  `load_watchlist_history_db()`는 Fishing 최초가 조회가 여전히 쓰고 있어서 안 지우고 남겨둠.
- **작업 사본**: `Desktop/DBtest`에서 먼저 검증(스키마/이관/적재 왕복 테스트)한 뒤 `new1`으로
  이식함. `DBtest`는 `new1`과 origin이 동일한 저장소를 물고 있어서(폴더 통째 복사 때 `.git`도
  같이 옴) **거기서 직접 git push하면 안 됨** — 이후 실험 사본을 또 만들 때도 이 점 주의.

### 6-10. 보유종목 카드 클릭 → 매수/매도 내역 + "물타기 적정성" 그래프
- **동기**: 물을 탈 때(하락 중 추가매수) 내가 현재가를 적절히 따라가며 사고 있는지(너무
  늦게/일찍 사는 건 아닌지) 눈으로 보고 싶다는 요청.
- **UI**: `ui_portfolio_tab.py`의 "종목별 보유현황" 카드 우상단 **"WATERING" 칩**을 누르면
  그 카드 바로 밑에 상세가 펼쳐짐(`st.session_state.holding_detail_open`에 종목코드 저장,
  한 번에 하나만 펼쳐짐 — 아코디언 방식).
  - WATERING 칩은 `st.container(key=f"holding_wrap_{{code}}")`로 카드 markdown과 버튼을 함께
    감싸고, `app.py`의 CSS로 카드 우상단(섹터태그 왼쪽)에 절대위치시킨 것 — `[class*="st-key-
    holding_wrap_"] {{ position:relative; }}` + `[class*="st-key-watering_"] {{ position:
    absolute; top:11px; right:108px; }}`. `position:absolute`라 정상 레이아웃 흐름에서 빠지므로
    카드 높이에 영향을 안 줌(버튼 줄이 카드마다 항상 보이던 걸 없애면서 화면이 길어지는 문제
    해결). 열림 상태는 `type="primary"`(채움)/`"secondary"`(테두리)로 구분. 텍스트는 검정
    (`T['text']`) + 일반굵기(400) — 비중/현재가 등 다른 보조 텍스트와 톤 통일.
  - 종목명이 길면(예: 신세계인터내셔날) WATERING과 겹칠 수 있어서, 종목명은 `.stock-title-group`
    (`max-width:calc(100% - 180px)`) 안에서 13px·`white-space:nowrap`으로 표시하고, 비중은
    이 줄에서 빼서 `stock-grid`의 "수량" 칸 밑으로 옮겨둠. 겹침을 왜 이렇게 고쳤는지 경위는
    `ARCHIVE.md` §6-10 참고.
- **거래 요약**: `portfolio_core.get_holding_trade_summary(tx, 종목명)` — 그 종목의 매수/매도
  건수·누적금액·실현손익 합계. **평단가는 여기서 재계산하지 않고 holdings(portfolio_data.csv)의
  기존 값을 그대로 씀** — 매도가 껴있으면 "총매수금/총매수수량" 단순평균은 틀림(예: 2주@1000원
  매수 후 1주 매도, 다시 1주@900원 매수하면 정답은 950원이지 966원이 아님). `apply_transaction`이
  거래를 순서대로 재생하며 이미 정확히 계산해주므로 그걸 재사용하는 게 맞다.
- **"누적" 요약**: 위 거래 요약 줄 위에 `get_holding_trade_summary_all_time()`로 계산한
  "누적"(사이클 구분 없이 전체 매매 이력) 매수/매도/실현손익을 한 줄 더 보여줌 — 아래의
  "이번 사이클" 요약과 나란히 둬서, 종목별로 지금까지 총 얼마 벌고 잃었는지 계속 트래킹.
  과거에 청산했던 사이클의 실현손익도 여기엔 포함됨.
- **"현재 보유 사이클"만 표시**: `get_holding_trade_summary`/`get_holding_trade_points`/
  `get_holding_avg_price_path`는 전부 내부적으로 `_current_cycle_transactions()`를 거친다 —
  그 종목을 **전량매도(보유수량 0)했다가 나중에 재진입한 적이 있으면, 마지막 전량매도 이전의
  거래는 전부 잘라내고 그 이후(현재 사이클)만 반환**한다. 예를 들어 10000원에 사서 물타다
  9000원에 전량매도(1차 사이클 종료)한 뒤 5000원에 재진입한 경우, 전체 이력을 다 쓰면
  최초진입가/평단가/그래프에 1차 사이클 거래까지 섞여 들어와서 "지금 사이클에서 물타기가
  적절한가"가 왜곡됨(§1-1의 "거래 재생" 원칙과 같은 맥락). 실제 보유 종목 15개(전량매도
  이력이 있는 것들)로 검증 — 이 함수들이 계산한 최종 평단가가 전부 holdings의 실제 평단가와
  정확히 일치함을 확인함.
- **그래프** (`_render_holding_detail()`, plotly, `ui_transactions_tab.py`의 자산추이 차트와
  같은 스타일 — `paper_bgcolor`/`plot_bgcolor` 투명, `T[]` 테마색, `dragmode=False`):
  - 최초매입일→오늘 두 점을 잇는 점선(연한 회색, "현재가") — **보유종목엔 연속된 일별 시세
    히스토리가 없어서**(그건 Fishing 관심종목만 §6-9의 Supabase에 매일 쌓이는 중) 두 점만
    이을 수 있음. 장기적으로 보유종목 시세도 DB로 연결하면 그 사이 구간을 실제 일별 시세선으로
    채울 수 있음(사용자가 이미 이 방향을 언급함) — 지금은 아직 아님.
  - **평단가는 계단식(hv) 선으로 표시** — `get_holding_avg_price_path()`가 (매수 시점마다
    apply_transaction과 동일한 로직으로 재계산한 평단가, 날짜) 쌍을 반환하고(매도는 평단가에
    영향 없음), 마지막에 (오늘, 현재 평단가) 점을 이어붙여 오늘까지 선을 연장한다.
    `line_shape='hv'`라 매수와 매수 사이는 자동으로 평평하게 유지되다가 매수 시점에 계단처럼
    꺾인다.
  - 매수 시점(파랑 ▲)/매도 시점(빨강 ▼) 각각 점으로 표시(`get_holding_trade_points()`) — 매도도
    같이 보여줘야 "판 다음 다시 물탄" 흐름이 왜곡 없이 보임(단, 현재 사이클 안에서의 부분매도만
    — 전량매도로 사이클이 끝난 건 위 항목대로 아예 안 보임).
  - 수평 기준선: 최초진입가(회색 점선) 하나만.
  - 그래프 밑 텍스트: "현재가는 최초진입가 대비 ±X%" / "내 평단가는 최초진입가 대비 ±Y%"
    — 두 값이 비슷하면 물타기가 현재가를 잘 따라가고 있다는 뜻.
- **변경 범위**: `portfolio_core.py`에 함수 4개(`_current_cycle_transactions`,
  `get_holding_trade_summary`, `get_holding_trade_points`, `get_holding_avg_price_path`) —
  `ui_portfolio_tab.py`에 `_render_holding_detail()` 추가 + 카드 루프에 버튼 삽입.

### 6-11. 자동화 테스트
- **구조**:
  - `conftest.py`(레포 루트) — pytest가 `portfolio_core` 등을 최상위 모듈로 import할 수 있게
    레포 루트를 sys.path에 잡아주는 용도. 내용은 사실상 비어있음.
  - `tests/test_portfolio_core.py` — `portfolio_core.py`는 Streamlit에 안 묶인 순수 로직이라
    테스트하기 쉬움. 이 문서 §1(반드시 지킬 것)에 적힌 "실제로 겪은 버그"들을 회귀 테스트
    케이스로 옮겨 담음.
  - `tests/test_app_smoke.py` — `streamlit.testing.v1.AppTest`(Streamlit 공식 테스트 도구)로
    `app.py`를 실제로 실행해서 예외 없이 뜨는지, WATERING 칩 클릭 시 세션 상태가 의도대로
    열리고 닫히는지 확인. **한계: 진짜 브라우저가 아니라서 CSS/픽셀 정렬은 이 테스트로 검증
    못 함** — 그런 건 사람이 화면으로 보거나 Playwright(§6-9 아래 "작업 사본" 근처가 아니라
    →) [[project_playwright_visual_check]]로 확인해야 함. 실제 `transactions.csv`/
    `portfolio_data.csv` 데이터를 그대로 읽어서 실행되므로 보유종목이 아예 없는 상태가 되면
    WATERING 클릭 테스트는 조용히 스킵됨(의도된 동작).
  - `requirements-dev.txt` — `requirements.txt` + `pytest`. Streamlit Cloud 배포에는
    `requirements.txt`만 쓰이므로 pytest가 배포 환경에 섞여 들어가지 않음.
  - `.github/workflows/tests.yml` — `main`에 push될 때마다 자동으로 pytest 실행. `app.py`가
    `check_password()`에서 `st.secrets`에 접근하기만 해도 `secrets.toml` 파일 자체가 없으면
    즉시 에러가 나므로(§6-1-1), CI에서도 로컬 개발과 똑같이 빈 `.streamlit/secrets.toml`을
    만들어주는 스텝이 필요함.
- **실행 방법**: `pip install -r requirements-dev.txt` 후 `pytest tests/ -v`.
- **`requirements.txt`의 `pandas>=2.0,<3.0` 상한을 되돌리지 말 것** — pandas 3.0에서 실제로
  터진 버그를 잡고 예방 차원에서 건 상한. 자세한 경위는 `ARCHIVE.md` §6-11 참고.
- **알려진 한계 (수정 안 함, 참고만)**: 테스트 실행 중 `apply_transaction`의 `pd.concat`
  호출에서 pandas `FutureWarning`(빈/all-NA 항목과의 concat 관련)이 뜸 — 지금 당장 동작에
  문제는 없음. 나중에 pandas 버전을 올리다가 이게 실제 에러로 바뀌면 그때 고칠 것.
- **다음 세션 참고**: `portfolio_core.py`에 새 함수를 추가하거나 기존 계산 로직(특히 거래
  재생/평단가/사이클 관련)을 고칠 때는, 가능하면 `tests/test_portfolio_core.py`에 케이스를
  같이 추가해서 이 안전망을 계속 키워나갈 것 — 스크래치 스크립트로 한 번 검증하고 버리는
  방식으로 되돌아가지 말 것.

### 6-12. 거래량/외국인 수급 트래킹
- **동기**: "오늘 이 종목 거래량이 평소보다 튀었나, 외국인이 평소보다 더 사고 있나"를
  판단하려면 원시값을 매일 쌓아두고 나중에 평균/등락폭을 계산해야 함. §6-9 DB 파이프라인
  (watchlist 153종목 전체 대상 — 51개 보유종목만이 아니라 신규 편입 후보 판단 근거로도
  쓰려는 목적)에 얹어서 확장함.
- **데이터 소스 (전부 비공식 HTML/JSON 스크레이핑, 공식 API 아님 — 네이버가 페이지/응답
  구조를 바꾸면 조용히 깨질 수 있다는 전제로 설계)**:
  - **거래량**: 이미 매일 부르고 있던 실시간 시세 API(`fetch_quotes`) 응답 안에
    `accumulatedTradingVolume` 필드가 이미 들어있었음 — 새 API 호출 없이 필드 하나만 더
    파싱해서 추가.
  - **종목별 외국인/기관 수급**: `portfolio_core.fetch_investor_flow(code)` — 네이버
    개별종목 페이지(`/item/frgn.naver?code=XXX`)의 "외국인 기관 순매매 거래량" 표를
    긁음(euc-kr 인코딩, BeautifulSoup + 표준 html.parser로 파싱, `beautifulsoup4`를
    requirements.txt에 신규 추가). **한 번 호출로 최근 약 20영업일치가 한꺼번에 나와서**
    도입 시점에 하루씩 쌓일 때까지 기다릴 필요 없이 바로 한 달 가까이 백필됨.
  - **시장 전체(코스피/코스닥) 거래량+수급 베이스라인**: `portfolio_core.fetch_market_flow
    (market)` — 두 페이지를 합쳐서 씀: 거래량은 `/sise/sise_index_day.naver?code=KOSPI|
    KOSDAQ`(지수 일별시세), 수급(개인/외국인/기관 순매수, 억원 단위)은
    `/sise/investorDealTrendDay.naver?sosok=&bizdate=...`(sosok 빈 값=코스피,
    `02`=코스닥). **코스피/코스닥을 둘 다 저장하는 이유**: 보유종목엔 코스피/코스닥이
    섞여있어서(예: CJ제일제당=코스피), 코스닥 종목을 코스피 평균과 비교하면 기준이
    안 맞음 — 종목이 속한 시장에 맞는 베이스라인과만 비교해야 함.
- **DB 스키마**: §6-9 표 참고 (`price_history.volume`, `investor_flow`, `market_flow`).
- **"등락폭"은 화면 표시 시점에 계산, 별도 컬럼 저장 안 함**: 네이버가 거래량/외국인
  등락률을 직접 안 주므로(가격의 등락률과 다름), DB엔 그날그날 원시값만 쌓고 "평균 대비",
  "어제 대비" 같은 파생값은 화면에서 최근 N일치를 조회해 그때그때 계산 — 나중에 "최근
  며칠 평균" 기준을 사용자가 직접 바꿀 수 있게 하려는 목적. **외국인 쪽은 보유율(%)을
  메인으로 쓰기로 함** — 외국인순매수(주식 수)는 마이너스로 갈 수 있어 "%변화"가 어색해지는데,
  보유율은 원래 %라 "평균 대비 ±%" 패턴이 자연스러움. 다만 보유율은 하루 변동폭이 원래
  작으므로(예: 12.93%→13.04%), "어제 대비"는 상대변화율(%)이 아니라 **%p(퍼센트포인트)
  차이**로 보여주는 게 직관적임 — 화면 만들 때 이 점 지킬 것. 그날의 외국인순매수(주식 수)는
  참고용 숫자로 같이 보여주기로 함.
  - **알려진 한계 (당장 안 고치기로 함)**: "평균"이 지금은 쌓인 기간(약 20일) 전체의
    단순평균이라, 그 안에 이상치가 하나 껴있으면 평균이 크게 왜곡돼서 "평균 대비 %"가 실제
    체감과 안 맞을 수 있음(사례는 `ARCHIVE.md` §6-12 참고). 사용자가 "일단 데이터 더 쌓이면
    나아질 문제"라고 판단해서 지금은 안 고치기로 함. 그래도 계속 거슬리면 "최근 며칠 평균"
    기능(기간을 사용자가 지정, 아직 미구현)이나 단순평균 대신 중앙값(median)으로 바꾸는 걸
    고려할 것 — 둘 다 이상치에 덜 흔들림.
- **UI**: Fishing expander 밑에 "Volume", 그 밑에 "Foreigner" 섹션(둘 다 `st.expander`,
  Fishing과 같은 아코디언 패턴). 둘 다 새로고침 버튼 → `load_investor_flow_db`/
  `load_market_flow_db`로 DB 조회 → `st.session_state["flow_hist"]`/`["market_hist"]`에
  캐싱(Fishing과 동일 패턴, 매 rerun마다 DB 재조회 안 하려고).
  - **Volume**: 상단에 코스피/코스닥 시장 전체 거래량 평균대비 %(캡션), 그 밑에
    `compute_volume_flags()` 결과 상위 15개(종목명 + vs평균% + "전일 ±% 주가 ±%").
  - **Foreigner**: 상단에 코스피/코스닥 시장 전체 외국인 순매수(오늘/평균, 억원) 캡션,
    그 밑에 `compute_foreign_flags()` 결과 상위 15개(종목명 + vs평균%p + "전일 ±%p
    주가 ±%").
  - **원시 수량(거래량/외국인순매수 주식수)은 화면에 안 보여줌** — DB엔 그대로 쌓지만(나중에
    DB 필터로 검색할 때 쓸 것), 화면엔 "평균 대비 %(메인 pct)" → "전일 대비 %(p)" → "그날
    실제 주가 등락률"만 보여줌. 수급 지표만 보고는 판단하기 어려워서,
    `compute_volume_flags`/`compute_foreign_flags`에 `price_hist`(선택 인자,
    `load_watchlist_history_db()` 결과) 넣어서 종목별 그날 등락률을 매칭해 붙여줌 —
    `_latest_change_pct_map()` 헬퍼.
  - `.updown-row`에 `.flow-row` 변형 클래스(`app.py` CSS, `.detail` 118px→150px, `.pct`
    62px→56px) — 라벨은 "어제대비"가 아니라 "전일"로 축약(3자리 %일 때 줄바꿈되던 문제
    있었음). 픽셀 검증은 Playwright(로컬 Chrome, [[project_playwright_visual_check]])로
    실제 모바일 폭(390px)에서 확인함.
- **검증 기록**: 실제 라이브 데이터(153종목 백필분)로 계산 함수들 검증함 — 자세한 목록은
  `ARCHIVE.md` §6-12 참고. `tests/test_portfolio_core.py`에 `fetch_investor_flow`/
  `fetch_market_flow` 파싱 로직 회귀 테스트 있음(실제 페이지 구조를 고정 HTML fixture로
  박아두고 `requests.get`을 monkeypatch).

### 6-13. 거래 내역 누적 요약
- **거래 기록 탭, 캘린더 위**: 누적 매수/매도(건수 + 일평균건수 + 금액) + 누적 실현손익(금액 +
  원금 대비 %) 두 줄 요약 (`ui_transactions_tab.py`, `.tx-cum-summary` 클래스 — 실현손익
  그래프 범례와 글자 크기를 맞춤, 13px). "일평균"은 금액 평균이 아니라 **건수 평균**(누적건수 /
  전체 거래일수, 소수점 버림)이니 헷갈리지 말 것.
- 실현손익 %의 분모는 **원금(state["initial"])** — 매수총액 대비였던 초기 버전은 물타기로
  매수총액 자체가 계속 불어나서 같은 실현손익이라도 %가 작아 보이는 문제가 있어
  2026-08-26에 원금 대비로 변경함(사용자 요청).

### 6-14. transactions 재생 체크포인트 (2026-08-25 도입)
- **왜**: `ingest_daily.py`가 매번 `transactions.csv` 전체를 처음부터 재생(replay)해서
  holdings를 다시 계산하는 구조였는데(§1-1), 거래가 지금(몇백 건)은 문제없어도 몇 년 치
  쌓여 수천~수만 건이 되면 반영할 때마다 점점 느려지는 구조라 미리 체크포인트를 도입함.
- **동작**: `portfolio_core.rebuild_portfolio_incremental()`이 `ingest_daily.py`가 실제로
  쓰는 유일한 경로(§6-2)다. `rebuild_portfolio_from_transactions()`(전체 재생)는 지우지
  않고 "정답 기준"으로 남겨뒀고, `tests/test_portfolio_core.py`가 두 함수의 결과가 항상
  같아야 한다는 걸 계속 검증한다. 내부적으로 두 함수 다 `_replay_transactions()`(재생 루프
  하나만 존재 — 복제 금지)를 공유한다.
  - **최근 safety_days일(기본 3일)치는 절대 체크포인트로 확정하지 않고 매번 다시 재생**한다.
    이유(§1-2): 증권사 CSV는 "그날 하루 전체 누적"이라 최근 며칠은 재업로드로 통째로 바뀔 수
    있음 — 그 구간까지 체크포인트로 굳혀버리면 재업로드 시 옛날 값이 안 지워지고 남는 버그가
    생김.
  - 그보다 오래된(=이제 안 바뀔 게 확실한) 구간만 `checkpoint_holdings.csv` /
    `checkpoint_state.csv`에 저장해두고, 다음 반영부터는 그 이후 거래만 이어서 재생한다.
- **처음부터 유일한 경로로 씀**: "거래 많아지면 그때 켜는 대비책"이 아니라, 지금 거래가 적어
  체감 이득이 없어도 매일 실제로 이 경로를 타게 해서 Fishing v2→v3(§6-6)나 WATERING 칩처럼
  실사용을 통해 다듬어지게 하려는 의도(사용자 판단, 2026-08-25) — 별도 폴백 경로를 만들지
  말 것.
- **파일**: `checkpoint_holdings.csv`(§3 HOLD_COLUMNS와 동일 스키마), `checkpoint_state.csv`
  (체크포인트날짜, 예수금, 초기자본, fee_rate) — 둘 다 `portfolio_data.csv`류와 같은 로컬
  CSV라 §1-5 원칙대로 세션이 git commit/push해야 유지된다(현재는 다른 데이터 파일과 함께
  `ingest_daily.py` 실행 후 커밋하는 루틴에 자연히 포함됨 — 별도 스텝 아님).
- **섹터를 수동으로 고칠 때 반드시 `checkpoint_holdings.csv`도 같이 고칠 것** (2026-08-28
  실제로 겪음): `portfolio_data.csv`의 섹터를 직접 고치고 `stock_sector_cache.csv`도
  업데이트했는데, 다음 `ingest_daily.py` 실행 후 섹터가 옛날 값으로 되돌아간 사례가 있었음.
  원인: 이미 보유 중인 종목은 `apply_transaction`의 "기존 종목 업데이트" 분기를 타서 섹터를
  건드리지 않고(캐시 재조회 없음), 그 분기가 참조하는 기준값은 checkpoint 이후 재생에서
  다시 만들어지는 `portfolio_data.csv`가 아니라 **checkpoint 시점에 이미 확정된
  `checkpoint_holdings.csv`의 값**이기 때문. `load_holdings()`도 섹터가 빈 문자열일 때만
  캐시에서 보충하지, "미분류가 아닌 잘못된 값"은 안 건드림. 즉 섹터를 캐시에서 자동
  재조회하는 경로는 그 종목의 **최초 매수가 체크포인트 이전 시점**일 때만 우회된다 —
  결론적으로 이미 보유 중인 종목 섹터를 고칠 땐 `portfolio_data.csv`뿐 아니라
  `checkpoint_holdings.csv`의 해당 행도 직접 같은 값으로 고쳐야 다음 반영에서 안 되돌아감.
  (meritz는 체크포인트가 없고 매번 전체 재생이라 이 문제가 없음 — new1만 해당.)
