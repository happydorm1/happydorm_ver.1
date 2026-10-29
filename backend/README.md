# 기숙사 차입금 원리금 산출·상환관리 시스템 — Backend (v1.0, Phase 15 최종 검증 완료)

재단의 엑셀 기반 기숙사 차입금 원리금 산출·상환관리 업무를 웹 업무시스템으로 옮긴 백엔드(FastAPI + PostgreSQL)입니다.
화면은 `frontend` 저장소(React)를 함께 씁니다.

## 1. 설치 (운영·개발 공통)

준비물: Python 3.12~3.13, PostgreSQL 14 이상(검증: 16), `pg_dump`/`pg_restore`(백업용)

```bash
python -m venv venv && source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt                         # 최종 검증 때 76개 테스트를 통과한 버전 조합

# DB와 계정(예시) — 복구 훈련을 하려면 CREATEDB 권한 필요
#   CREATE ROLE loanapp LOGIN PASSWORD '…' CREATEDB;  CREATE DATABASE dormitory_loan OWNER loanapp;

cp .env.example .env          # DATABASE_URL, SECRET_KEY, DATA_ENCRYPTION_KEY, CORS_ORIGINS, KASI_SERVICE_KEY 등 입력
python -m app.jobs.init_db --admin-email admin@재단도메인     # 테이블 생성·기준정보·최초 관리자(비밀번호는 입력창)
uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers   # 앞단 웹서버(HTTPS)가 /api 를 넘겨준다
```
- 버전을 올릴 때도 같은 명령 `python -m app.jobs.init_db` — 새 테이블·새 칸·선택값만 추가하고 기존 데이터는 건드리지 않습니다.
  바꾸지 않고 확인만: `python -m app.jobs.init_db --check`(차이가 있으면 종료코드 1).
- 운영은 `.env`에 `APP_ENV=production`. 위험한 설정이면 서버가 시작되지 않습니다(부록 Phase 14 '서버 보안 설정').
- 공휴일: 공공데이터포털 '한국천문연구원 특일 정보' 서비스키를 `KASI_SERVICE_KEY`에 넣고 `POST /api/v1/holidays/sync?year=2026`.
  서비스키가 없으면 관리자 로그인 때마다 안내 팝업이 뜹니다.

## 2. 정기 작업 (서버 작업 스케줄러/cron)

| 주기 | 명령 | 하는 일 |
|---|---|---|
| 매일 06:00 | `python -m app.jobs.daily_scan` | 공휴일 갱신 → 상환일·금리변동 알림 생성 → 전체 차입금 계산검증(§37) |
| 매일 02:00 | `python -m app.jobs.backup run` | 암호화 백업 + 보관 규칙 정리 |
| 분기 1회 | `python -m app.jobs.backup restore-test` | 복구 모의훈련(임시 DB에 복원·검증 후 삭제) |

예) `0 6 * * * cd /srv/loan/backend && venv/bin/python -m app.jobs.daily_scan >> logs/daily.log 2>&1`

## 3. 검증 (Phase 15)

```bash
pytest -q                                                    # 76개: 계산·엑셀 대조·중도변수·이력·권한·보안·백업·전체 업무흐름
python -m verification.excel_reconciliation --out Excel대조결과.xlsx   # 원본 엑셀 7개 사례 1,102칸 대조표(테스트용 DB에서)
python -m verification.performance_check --loans 90          # 빈 DB에서 90건 규모 응답시간 측정
python -m app.jobs.verify_all --all-versions                 # 모든 원리금 테이블 9개 항목 계산검증
```
- 엑셀 대조 테스트는 첨부 원본(`/mnt/user-data/uploads/첨부_*.xlsx`)이 있어야 돌고, 없으면 건너뜁니다.
- 테스트는 테스트 전용 DB에서만 돌립니다(검증용 차입금이 생깁니다).
- 계산검증 API: `GET /api/v1/loans/{id}/schedule/verification` — §37의 9개 항목별 정상/실패/해당없음과 근거.

## 4. 구조

```
app/
├── main.py            앱·보안헤더·접근기록 미들웨어
├── core/              설정, DB, 인증·권한, 비밀번호 정책, 암호화(AES-256-GCM), 운영설정 검사, 접근기록
├── engine/            계산엔진(원금균등·원리금균등·엑셀 ROUND·영업일·공휴일 원천·Excel/공문 출력) — DB 무관 순수 계산
├── domain/            업무 규칙(원리금 생성, 금리변동, 상환유예, 가속·조기상환, 대환, 취소, 납부, 공문, 알림,
│                      대시보드, 중도변수 미처리 판정, 계산검증, 팝업)
├── models/            테이블(물리삭제 없음 — 사용중지·취소·버전 보존)
├── api/v1/            REST API
└── jobs/              init_db, daily_scan, backup, verify_all, encrypt_existing, seed_demo
verification/          최종 검증 도구(엑셀 대조표, 성능 점검)
tests/                 단계별 테스트(phase1~15)
```

---
# 부록 — 단계별 개발 기록

## Phase 16 — 업무 화면용 API
- `GET /api/v1/events` — 권한 범위 전체 차입금의 중도변수 이력(구분·상태·기간 조건), 차입금마다 가장 최근 적용 이벤트에 `cancellable=true`
- `GET /reference/funds|codes?include_inactive=true`, `POST /reference/funds/{id}/active?value=`, `POST /reference/codes/{id}/active?value=` — 삭제 대신 사용중지(감사로그)
- `GET /auth/me`에 `event_approval_required`(결재 모드 여부), 이벤트·변경이력에 사용자 이름

## Phase 2 추가 사항
- `app/seed_reference.py`: 재원(FUND)/코드(CODE) 기본값 시드 (최초 1회 `python -m app.seed_reference`)
- `api/v1/reference.py`: 재원·상환주기·상환유형·거치유형 조회/등록 (44번 요구사항 — 관리자가 코드 수정 없이 추가)
- `api/v1/loan.py`: 차입금 등록/조회, 금리이력 추가, **적용금리 조회**(1단계에서 실데이터로 검증한 규칙 그대로 구현 — 회차의 이자계산종료일 기준, 일할안분 없음)
- `domain/rate_service.py`: 금리 조회 핵심 로직(Phase 3 계산엔진이 그대로 재사용할 예정)
- `tests/test_phase2_loan.py`: 1단계 Test A 시나리오(2015-08-21 공자, 금리변경 2022-02-28)를 실제 DB로 재현해 검증

## Phase 5 추가 사항
- `engine/excel_export.py`: 원리금 테이블 Excel 다운로드(19번 컬럼 구성 그대로)
- `GET /loans/{id}/schedule/versions`: 버전 이력 전체 조회
- `GET /loans/{id}/schedule/download`: Excel 다운로드 (`version_no` 생략 시 현재 버전)

## Phase 6 추가 사항 — 상환유예
- `models/event.py`: LoanEvent(공통 부모) / Deferral / DeferralRecoveryRow
- `domain/deferral_service.py`: 2단계에서 첨부3 실사례로 역산한 알고리즘 구현
  - 접수시점과 일치하는 회차 1건 동결(원금/원금+이자/이자)
  - 동결일+1년부터 입력된 주기(분기/반기)·횟수로 분할 회수(기존 정규회차에 가산, 없으면 회수 전용 행 신규 삽입)
  - **중요 발견**: 동결원금에 대한 추가이자는 누적 잔여액이 아니라 '해당 회차 회수원금 자체'에,
    회수가 시작된 시점부터 이어지는 분기 단위 일수체인 기준으로 계산됨(기존 반기 간격이 아님)
  - 마지막 회수회차 자동보정, 기존 버전은 보존하고 새 버전(V+1) 생성
- `api/v1/event.py`: `POST /loans/{id}/events/deferral`
- `tests/test_phase6_deferral.py`: 첨부3 실사례(2014-12-18 주택도시기금, 6회차 동결)를
  동결원금·이자·회수 10회차(원금/이자/추가이자 전부)·최종잔액까지 전 항목 실제 API+DB로 재현해 완전 일치 확인

## Phase 7 추가 사항 — 금리변동
- `domain/rate_change_service.py`: 변동금리(공공자금관리기금) 재계산
  - 1단계에서 확인한 규칙 그대로: 새 금리는 이자계산종료일이 적용시작일 이후인 회차부터 전체 적용
  - 영향받는 회차부터만 재계산하고, 과거 회차는 완전히 보존(새 버전 V+1 생성)
  - 거치기간 중/상환기간 중 금리변경 둘 다 지원
  - 원리금균등(PPMT/IPMT, 주택도시기금·고정금리)은 대상 아님 — 요청 시 422로 명확히 거부
- `api/v1/event.py`: `POST /loans/{id}/events/rate-change`
- `tests/test_phase7_rate_change.py`: 과거 회차 불변 확인, 변경 후 이자액 수동계산 일치 확인,
  거치기간 중 변경 케이스, PPMT 방식 거부 케이스까지 4가지 검증

## Phase 8 추가 사항 — 원금가속상환 / 조기상환 / 대환대출
모든 이벤트는 기존 버전을 보존하고 새 버전(V+1)을 만듭니다. (`domain/version_utils.py` 공통 헬퍼)

| 이벤트 | API | 검증 근거 |
|---|---|---|
| 원금가속상환 | `POST /loans/{id}/events/acceleration` | **첨부4 실사례**: 가속구간 38행의 원금·잔액·이자 전부 일치, 41회차 종료 |
| 조기상환(일부/전액) | `POST /loans/{id}/events/early-repayment` | 수동 계산 대조(원금균등·원리금균등·전액+경과이자). **실사례 자료 없음 → 아래 확인 필요** |
| 대환(일부) | `POST /loans/{id}/events/refinancing` (`PARTIAL`) | **첨부6 실사례**: 대환 이후 34행(전환/잔류 두 트랙+재합류) 전부 일치 |
| 대환(전액) | 동일 (`FULL`) | **첨부7 실사례**: 대환 이후 19행 전부 일치 |

- 가속배율은 입력값(기본 1.5). 시작시점 = 접수일 이후 첫 정규 상환회차, 이후 단리 전환, 잔액 소진 회차에서 마감.
- 일부대환은 전환금액·잔류금액·재합류일을 담당자가 입력. 전환금액+잔류금액=대환시점 잔액, 전환금액=구간 내 정기상환 원금 합계가 아니면 **임의 보정하지 않고 422로 거부**.
- 전액대환은 같은 원장을 이어가며(신규 차입금 미생성) 기존 회차·원금을 승계하고 재원·금리만 교체.

### 이번에 발견·반영한 계산 규칙 2가지
1. **Excel 반올림 통일**: Python `round()`는 0.5를 짝수쪽으로 처리해 Excel `ROUND`와 1~수원 차이가 났음 → `excel_round`(0.5 올림)로 엔진 전체 통일.
2. **차입금별 이자 반올림 방식**: 구 차입금(2009~2010년, 첨부6·7)은 이자를 `ROUNDDOWN`(절사)으로 계산. `loan.interest_rounding`(`ROUND` 기본 / `ROUNDDOWN`) 필드로 관리.
   기존 DB에는 다음 1회 실행이 필요합니다:
   ```sql
   ALTER TABLE loan ADD COLUMN IF NOT EXISTS interest_rounding VARCHAR(12) NOT NULL DEFAULT 'ROUND';
   ```

### 현재 한계 (업무기준 확인 후 확장 예정)
- 금리변동·대환은 원금균등(단리) 원장만 지원. 원리금균등(PPMT) 원장은 422로 명확히 거부.
- 조기상환의 경과이자 정산(직전 상환일~조기상환일 단리)은 **기본 구현**이며 실사례로 대조되지 않음.
- 상환유예 회수 구간과 겹치는 가속상환/조기상환/대환은 지원하지 않음(422).

## Phase 9 추가 사항 — 버전·변경이력 관리
| 기능 | API | 설명 |
|---|---|---|
| 이벤트 이력 | `GET /loans/{id}/events` | 유예·가속·조기상환·대환·금리변동 타임라인(취소 포함), 이벤트별 상세와 결과 버전 번호 |
| 버전 비교 | `GET /loans/{id}/schedule/compare?from_version=&to_version=` | 상환예정일별 원금·이자·합계 차이와 총계 차이(§37-9) |
| 이벤트 취소 | `POST /loans/{id}/events/{event_id}/cancel` | 삭제 아님. **최신 적용 이벤트만** 취소 가능, 직전 버전을 복원한 새 버전 생성. 금리변동·전액대환은 금리이력/재원도 복원 |
| 변경이력 | `GET /loans/{id}/audit-logs` | 누가·언제·무엇을·변경 전/후·사유(§42). 등록은 자동 기록, 수정/취소는 명시 기록 |

- 차입금 `PUT`은 변경 전/후/사유를 기록하며(`change_reason`), **원리금 테이블이 이미 있으면 계약조건(배정금액·거치·상환기간 등)은 직접 수정할 수 없고** 이벤트로만 변경합니다(§50: 기존 데이터를 임의로 덮어쓰지 않음).
- 금리변동도 이제 LoanEvent로 기록됩니다. 취소된 금리이력은 삭제되지 않고 `is_deleted=true`로 표시됩니다.
- 기존 DB 1회 실행 필요: `ALTER TABLE refinancing ADD COLUMN IF NOT EXISTS old_fund_id UUID REFERENCES fund(id);`
  (신규 테이블 `audit_log`, `actual_payment`는 `create_all`이 생성)

## Phase 10 추가 사항 — 실제 납부관리
- `POST /loans/{id}/payments` : 상환예정일별 실제 납부 입력(원금/이자, 분할납부 가능, 재단 담당자·관리자만)
- `GET /loans/{id}/payments/summary?as_of=` : 예정액 vs 실제액 비교, 상태(PAID/OVERPAID/PARTIAL/UNPAID/PENDING), 부족액, 연체 여부, 회수율
- `GET /payments/overdue?as_of=` : 전체 미납 목록(대시보드 '미납건수' 용, SPC 담당자는 자기 차입금만)
- `POST /loans/{id}/payments/{pid}/cancel` : 삭제 대신 취소(사유 필수, 변경이력 기록)
- 실제 납부는 행 ID가 아니라 (차입금, 상환예정일)에 연결 → 이벤트로 새 버전이 생겨도 납부이력이 유지됩니다.
  이벤트 취소 등으로 일정에서 사라진 상환일의 납부는 `unmatched_payments`로 표시됩니다.
- 예정액 집계에는 유예 회수 원금(별도 테이블)이 포함됩니다. 연체이자는 고정 공식이 없어(확정사항) 자동 계산하지 않고 미납 사실·부족액만 제공합니다.

### 이번에 바로잡은 결함 (Phase 6)
원금균등 원장에서 연도경계(12/31)가 걸린 상환일에는 같은 날짜에 이자전용 행과 원금포함 행이 둘 다 실지급인데,
상환유예가 첫 행만 동결해 원금이 동결되지 않던 문제를 수정했습니다(상환일의 실지급 행 전체를 동결).
첨부3 실사례(PPMT)는 영향이 없었고 기존 테스트도 그대로 통과합니다.

## Phase 11 추가 사항 — 안내공문 자동생성 (§28~29)
기숙사 + 상환예정일만 고르면 공문의 **모든 숫자가 원리금 테이블에서 자동으로 채워집니다**(수기 입력 없음).
| API | 설명 |
|---|---|
| `POST /document-templates` | 공문 양식 등록. **입금계좌·예금주는 코드가 아니라 여기서 등록**합니다(계좌번호는 코드/시드에 넣지 않음) |
| `GET /dormitories/{id}/notices/preview?due_date=` | 발급 전 미리보기(저장 안 함) |
| `POST /dormitories/{id}/notices` | 발급 — 발급 당시 값을 스냅샷으로 저장, 포함된 차입금마다 변경이력 기록 |
| `GET /notices/{id}/download?format=xlsx\|pdf` | 첨부2 양식(6개 항목, 초록 음영, 합계행) 그대로 내려받기 |

- 한 기숙사의 여러 차입금(재원별)을 그 상환일에 청구할 건만 모아 재원별 행·합계를 만듭니다. 같은 재원이 둘이면 차입금 코드를 붙여 구분합니다.
- 파생 칸(이자기간·상환 후 잔액·원리금 합계·합계·TRUE/FALSE 검증)은 원본처럼 **수식**입니다. LibreOffice로 재계산해 값을 검증했습니다.
- 발급된 공문은 스냅샷에서 만들기 때문에, 이후 원리금 테이블이 바뀌어도(조기상환 등) 이미 발송한 공문은 그대로 재현됩니다.
- 납부기한이 휴일이면 **임의로 옮기지 않고 경고**합니다(휴일 납부일 조정 방향은 업무기준 미확정). `payment_deadline`으로 담당자가 직접 지정할 수 있습니다.
- PDF는 서버에 LibreOffice(+한글 폰트)가 있어야 합니다. 없으면 501로 안내하고 Excel은 항상 가능합니다. (`apt install libreoffice-calc fonts-noto-cjk`)
- 검증: 첨부2 실제 공문과 대조 — 공공자금관리기금 전 항목 일치, 주택도시기금 이자는 첨부1 원리금 테이블 값(32,361,807원)과 일치.
  (첨부2에는 32,361,800원으로 적혀 있었음 — 손으로 옮겨 적으며 생긴 7원 오차로, 자동연결로 해소됨)

## Phase 12 추가 사항 — 영업일·공휴일·알림 (§31~33, §35)
**공휴일 기준정보** (`/holidays`, 재단 담당자·관리자): 토·일은 계산으로, 공휴일/은행 휴무일은 DB로 관리합니다.
법정 공휴일은 매년 달라지므로(대체·임시공휴일) **코드에 넣지 않았습니다 — 매년 `POST /holidays/bulk`로 등록해야 하며,
미등록 연도는 "확인 필요" 항목으로 표시됩니다.** 삭제는 사용중지(되살릴 수 있음).

**영업일 계산** (`engine/business_day.py`): D-N = 상환일 이전 N번째 **영업일**(달력일 아님).
`GET /calendar/business-day?date=`, `GET /calendar/repayment-alert-dates?due_date=`

**알림 배치** `POST /notifications/scan` 또는 `python -m app.jobs.daily_scan` (하루 한 번 스케줄러가 호출, 여러 번 실행해도 중복 없음)
- 상환일정: D-15/10/5/3/1(영업일). 대상 SPC 담당자 + 재단 담당자. 이미 납부 완료된 상환일은 제외.
  배치가 멈췄다 돌아도 밀린 알림을 쏟아내지 않고 **이미 도래한 기준 중 가장 임박한 하나**만 보냅니다.
- 금리변동: 변동금리 차입금의 현재 금리 구간 종료일 D-30 이내, 재단 자금담당자 대상(변경예정금리가 미등록이면 "미정"으로 표시).
- 알림 내용은 §32·§33 항목을 모두 담습니다. 내 알림함 `GET /notifications`, 읽음 처리 `POST /notifications/{id}/read`.
- cron 예: `0 8 * * 1-5  cd /path/backend && python -m app.jobs.daily_scan`

**대시보드 업무 알림** `GET /alerts/summary` — 상환 예정 · 금리변동 예정 · 미납 · 확인 필요(계산 오류, 잔여원금 불일치, 금리 미등록, 공휴일 미등록). SPC 담당자는 자기 차입금만.

### 현재 한계 (확인 후 확장)
- 알림 채널은 **시스템 내 알림함(IN_APP)뿐**입니다. 이메일·문자 등 외부 발송은 기관 서버·보안 환경 확인 후 같은 레코드를 읽어 보내는 어댑터로 붙입니다(§46).
- 금리변동 D-30은 영업일 지정이 없어 **달력일**로 셉니다(가정 — 확정 필요).
- '중도변수 미처리'는 판정 기준이 없어 확인 필요 항목에 넣지 못했습니다.
- 상환일 자체가 휴일일 때 D-N은 "상환일 이전 N번째 영업일"로 계산하며 실제 납부일 순연은 하지 않습니다(업무기준 미확정).

## 업무기준 확정 반영 (Phase 12 후속)
1. **휴일 상환일 → 다음 영업일이 납부기한**: 상환예정일이 휴일(주말·공휴일·은행 휴무)이면 다음 영업일을 납부기한으로 본다.
   "다음 날"이 다시 휴일일 수 있어(토→일) 휴일이 끝나는 첫 영업일로 구현했다(2026-10-28 재단 확인 완료).
   미납·연체 판단, D-15/10/5/3/1 알림, 대시보드, 공문 납부기한이 모두 이 납부기한을 기준으로 한다.
   공문은 자동 지정한 경우 `payment_deadline_adjusted=true`와 안내 문구를 함께 준다(담당자가 직접 지정하면 그 값 우선).
   이자 계산 기간(상환예정일 기준)은 바꾸지 않는다 — 원리금 테이블과 첨부 대조 결과는 그대로다.
2. **금리변동 D-30 → 영업일 기준**: 변경예정일 이전 30번째 영업일부터 알린다. 알림에 `business_days_left`(영업일)와 `days_left`(달력일, 참고)를 함께 준다.
3. **은행 휴무일**: 법정공휴일(대체·임시공휴일 포함) 외에 5/1·12/31은 휴무일이 아니다(2026-10-28 확정) → 추가 휴무 기본값 없음.
   예전 기본값으로 들어간 5/1·12/31(source=BANK_RULE)은 일일 배치·동기화 때 자동으로 사용중지된다(삭제 아님).
4. **공휴일 연동 미설정 재안내**: 서비스키 미등록·올해/내년 공휴일 미등록·동기화 실패는 시스템관리자 로그인 팝업으로 안내.
   관리자가 '확인'하지 않으면 접속할 때마다, 확인해도 7일 뒤까지 해결되지 않으면 다시 안내한다.
5. **조기상환 경과이자**: 실사례가 나오면 재단이 추가로 알려주기로 함 — 그전까지 기존 기본 구현 유지.
6. **중도변수 미처리 → 알림 팝업**(2026-10-28 확정). 판정 기준(`app/domain/midterm_check.py`):
   - 금리변동 미등록: 변동금리 구간 종료일(변경일)이 지났는데 다음 금리가 없음
   - 금리변동 미반영: 금리 이력은 등록됐지만 앞으로 올 회차가 예전 금리로 계산돼 있음(금리변동 이벤트 미적용)
   - 조기상환 미등록 의심: 예정액보다 더 낸 회차가 있음
   - 납부 회차 연결 필요: 이벤트로 상환일이 바뀌어 기존 납부가 현재 회차와 맞지 않음
   - 결재 대기: (결재 모드) 기안된 이벤트가 아직 승인되지 않음
   대시보드 '확인 필요'에 항상 표시되고, 재단 자금담당·결재권자·관리자가 로그인하면 팝업으로 뜬다.
   '확인했습니다' → 그날은 숨기고 다음 날에도 미처리면 다시. '나중에' → 이번 접속 동안만 닫음. 처리하면 저절로 사라진다.
   금리이력을 먼저 등록한 뒤 같은 적용일로 금리변동 이벤트를 적용해도 이력이 중복되지 않는다(먼저 넣은 이력은 사용중지).

## 공휴일 자동 동기화 (수기 입력 대체)
- 원천: 공공데이터포털 **'한국천문연구원 특일 정보'**(getRestDeInfo) — 공휴일·대체공휴일·임시공휴일. 포털 달력들이 쓰는 공식 원천과 같은 데이터다.
  (네이버 달력 페이지는 사람이 보는 검색 화면이라 시스템이 안정적으로 읽어올 수 있는 데이터 형식이 아니어서 쓰지 않았다.)
- **설정**: 재단이 공공데이터포털에서 활용신청 → 서비스키 발급 → `.env`의 `KASI_SERVICE_KEY`에 등록(코드·저장소에 넣지 않음).
- 법정공휴일 외 추가 휴무일은 없음(확정). 기관 방침으로 고정 휴무일이 생기면 `BANK_EXTRA_CLOSURES`에 넣는다(기본 비어 있음).
- 일일 배치(`python -m app.jobs.daily_scan`)가 알림 전에 올해·내년을 자동 갱신(마지막 성공이 7일 넘었을 때만 — 늦게 발표되는 임시공휴일 대비).
  수동 실행: `POST /holidays/sync?year=2027`, 이력: `GET /holidays/sync-logs`, 설정 여부: `GET /holidays/sync-status`.
- 반영 규칙: 담당자가 직접 넣은 공휴일(source=MANUAL)은 절대 바꾸지 않음 / 원천에서 빠진 날짜(임시공휴일 취소)는 삭제 대신 사용중지 /
  호출이 하나라도 실패하면 DB는 그대로 두고 FAILED 이력만 남김 / 0건 응답이면 기존 데이터를 지우지 않음.
- 기존 DB 1회 실행: `ALTER TABLE holiday ADD COLUMN IF NOT EXISTS source VARCHAR(10) NOT NULL DEFAULT 'MANUAL';`
  (`holiday_sync_log` 테이블은 `create_all`이 생성)
- 이 개발 환경에서는 외부 API 접속이 막혀 있어 **실제 호출은 검증하지 못했다** — 응답 형식대로 만든 가짜 응답으로 파싱·반영 규칙을 검증했다.
  운영 서버에서 서비스키 등록 후 `POST /holidays/sync?year=2026` 한 번으로 실제 연동을 확인해야 한다.

## Phase 13 추가 사항 — 대시보드 (§34~36)

| API | 설명 |
|---|---|
| `GET /api/v1/dashboard?as_of=YYYY-MM-DD&…` | KPI·월별 상환예정(12개월)·재원별 현황·차입금 표·업무알림을 한 번에 반환 |
| `GET /api/v1/dashboard/filter-options` | 검색조건 선택지(로그인 사용자 권한 범위 안에서만) |

검색조건(§36): `dormitory_id, school(부분일치), spc_id, manager_user_id, fund_id, rate_type, repay_method_code_id, repay_cycle_code_id, status(여러 개), has_rate_change, has_midterm_event, period_from+period_to`.
기간은 시작·종료를 함께 넣어야 하며(한쪽만이면 422), 넣으면 `kpis.period_scheduled`가 추가됩니다. SPC 담당자는 자기 SPC 차입금만 보입니다(조건으로도 다른 SPC 조회 불가).

KPI 정의
- 총 잔여원금 = 배정액 − (기준일까지 도래한 **예정** 원금) → *계획 기준*. 실제 납부 반영 잔액은 '실제 상환관리'에서 확인
- 금월/익월/금년 상환예정 = 해당 기간 상환일의 원금+이자(+미지급이자), 금년 이자예정은 그중 이자
- 금리변동 예정·미납 건수는 업무알림(§31) 규칙과 동일(영업일 D-30, 납부기한 경과)
- 업무알림·KPI는 진행중(ACTIVE) 차입금만, 표는 상태 조건을 따름

### 데모 데이터 (화면 확인용, 가상 기숙사)
```bash
createdb -O dormuser dormitory_demo
DATABASE_URL=postgresql+psycopg://dormuser:dormpass@127.0.0.1:5432/dormitory_demo \
  python -m app.jobs.seed_demo --as-of 2026-10-28
DATABASE_URL=...dormitory_demo uvicorn app.main:app --port 8000
```
빈 DB에만 만들어집니다(차입금이 있으면 중단). 로그인(비밀번호 모두 `demo1234!`): 관리자 `demo-admin@`, 자금담당 `demo-fund@`, 결재권자 `demo-approver@`, SPC 담당자 `demo-spc@`, 조회자 `demo-viewer@`(다운로드 제한) — 모두 `example.com`.
중도변수 미처리 예시(가람관 새 금리 미반영) 1건이 들어 있어 관리자·자금담당 로그인 시 팝업이 뜬다.
공휴일은 일부러 넣지 않으므로 '확인 필요: 공휴일 미등록'이 보이는 것이 정상입니다(서비스키 등록 후 동기화하면 사라짐).

## Phase 14 — 권한·보안·백업

### 권한(§45 RBAC)
| 역할 | 할 수 있는 일 |
|---|---|
| 시스템관리자 | 전 기능 + 사용자·권한, 접근기록·감사로그·백업 조회, 잠금 해제 |
| 재단 자금담당 | 차입금·이벤트·납부·공문 등록, 기준 조회. 결재 모드에선 기안·(타인 기안) 승인 |
| 재단 결재권자 | 조회 + 이벤트 승인·반려(본인 기안 승인 불가). 다른 변경 불가 |
| SPC 담당자 | 소속 SPC 기숙사만 조회(입력 불가) |
| 조회자 | 전체 조회. 다운로드는 사용자별 설정(`can_download`) |

- **전 API 자동 점검 테스트**: 새 API가 생겨도 조회자·SPC 담당자·결재권자가 변경 API를 못 쓰는지, SPC 담당자가 다른 SPC의
  자료를 id로 못 여는지 모든 경로를 돌며 검사한다(`tests/test_phase14_security.py`).
  이 점검에서 **원리금 테이블 조회·버전·Excel 다운로드, 적용금리 조회, SPC 상세가 다른 SPC 담당자에게 열려 있던 문제**를 찾아 막았다.

### 계정 보안
- 로그인 5회 연속 실패 → 15분 잠금(`LOGIN_MAX_FAILURES`, `LOGIN_LOCK_MINUTES`), 관리자 즉시 해제 `POST /users/{id}/unlock`.
  없는 계정과 비밀번호 오류는 같은 메시지(계정 존재 여부 비노출).
- 비밀번호 정책(KISA): 2종류 조합 10자 이상 또는 3종류 조합 8자 이상, 같은 문자 4연속·이메일 아이디 포함 금지.
- `POST /auth/change-password`(본인), 관리자 초기화(`PUT /users/{id}`의 `password`). **바뀌는 순간 기존 토큰 모두 무효**.
- `GET /auth/me`: 화면이 역할·다운로드 권한에 맞춰 메뉴/버튼을 보인다.
- 사용자 생성·역할/소속/활성/다운로드 권한 변경·비밀번호 초기화·잠금 해제는 모두 감사로그(`table_name=app_user`)에 남는다.
  본인 강등·비활성화는 막는다(다른 관리자가 해야 함). SPC 담당자는 소속 SPC 필수.

### 접근기록(누가 어떤 차입금을 언제 조회했는지)
- 모든 API 호출을 `access_log`에 기록: 사용자, 조회(VIEW)/변경(CHANGE)/다운로드(DOWNLOAD)/거부(DENIED), 차입금·기숙사 ID, IP, 소요시간.
  로그인 성공·실패·잠금(LOGIN_*)도 기록. 기록 실패가 업무를 막지는 않는다.
- 조회: `GET /admin/access-logs?loan_id=&user_id=&action=DOWNLOAD&date_from=&date_to=` (관리자)
- 전체 변경이력: `GET /admin/audit-logs?table_name=app_user` 등

### 민감정보 암호화
- 공문 입금계좌(양식·발급 스냅샷)를 **AES-256-GCM**으로 암호화 저장. 양식 목록은 끝 4자리만 표시, 발급 공문·미리보기는 실제 계좌.
- 키: `.env`의 `DATA_ENCRYPTION_KEY`(base64 32바이트). 생성: `python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"`
  **키를 잃으면 암호화된 값·백업을 복구할 수 없다 — DB·백업과 다른 곳에 따로 보관.**
- 기존 데이터 1회 암호화: `python -m app.jobs.encrypt_existing`

### 서버 보안 설정
- 보안 헤더(nosniff, 프레임 금지, no-referrer), API 응답 캐시 금지(no-store), 운영 시 HSTS.
- `APP_ENV=production`이면 시작 시 검사 — SECRET_KEY 기본값/32자 미만, CORS `*`, 암호화 키 없음, 토큰 12시간 초과, 예시 DB 계정이면 **서버가 뜨지 않는다**.
  운영에서는 API 문서(/docs)도 닫힌다. 점검표: `GET /admin/security-status`.
- 전송구간 HTTPS는 앞단 웹서버(리버스 프록시)에서 적용하고 `uvicorn --proxy-headers`로 실제 IP를 기록한다.
- 2단계 인증은 설계서대로 '운영 단계 결정' 항목으로 남겨 두었다.

### 이벤트 등록·승인 분리(내부통제) — `EVENT_APPROVAL_REQUIRED`
- 기본값 `false`(지금처럼 등록 즉시 적용). `true`로 켜면 이벤트 등록 API(상환유예·금리변동·가속상환·조기상환·대환)는
  **202 + 기안**만 만들고 원리금 테이블은 그대로 둔다. 기안자가 아닌 사람이 `POST /event-requests/{id}/approve` 하는 순간
  즉시 적용과 같은 계산으로 새 버전이 생긴다. 반려(사유 필수)·회수(기안자 본인). 한 차입금에 결재 대기는 1건만.
- 결재 대기 건은 '중도변수 미처리(결재 대기)'로 대시보드·로그인 팝업에 나온다. 승인·반려·회수는 차입금 변경이력에 남는다.

### 백업·복구
```bash
python -m app.jobs.backup run            # 매일(cron 예: 0 2 * * *). 끝나면 보관 규칙으로 오래된 파일 정리
python -m app.jobs.backup restore-test   # 분기 1회 복구 모의훈련: 최신 백업을 임시 DB에 복원·검증 후 삭제
python -m app.jobs.backup restore --file backups/dormitory_loan_20261028_020000.dump.enc --target-db dormitory_loan_restored
python -m app.jobs.backup list
```
- pg_dump 전체 백업 + SHA-256 체크섬 + AES-256 암호화(`BACKUP_ENCRYPT`). 손상(체크섬 불일치) 파일은 복원 전에 막는다.
- 보관: 최근 35일은 매일분, 그 이전은 달마다 첫 백업 60개월(5년) — `BACKUP_KEEP_DAILY_DAYS`, `BACKUP_KEEP_MONTHLY`.
  백업 파일은 DB 서버와 다른 저장소(사내 NAS 등)로 복사해 두는 것을 권장.
- 실제 복구는 **항상 새 DB에** 복원한다(운영 DB 덮어쓰기 금지). 확인 후 `.env`의 `DATABASE_URL`을 새 DB로 바꾸고 재시작.
- 복구 훈련·복구에는 DB 계정에 `CREATEDB` 권한이 필요하다: `ALTER ROLE <계정> CREATEDB;`
- 성공한 백업이 36시간 넘게 없거나(`BACKUP_ALERT_HOURS`) 마지막 백업이 실패하면 관리자 로그인 팝업으로 안내. 이력: `GET /admin/backups`.
- **시점 복구(PITR)**: 일 백업 사이의 변경까지 살리려면 PostgreSQL WAL 보관을 켠다(DB 서버 설정, 재시작 필요):
  ```
  wal_level = replica
  archive_mode = on
  archive_command = 'test ! -f /backup/wal/%f && cp %p /backup/wal/%f'
  ```
  주 1회 `pg_basebackup -D /backup/base/$(date +%Y%m%d) -Ft -z -X stream`과 함께 쓰면 원하는 시각으로 되돌릴 수 있다(DB 관리자 작업).

### 기존 DB에 1회 실행(Phase 14)
```sql
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS failed_login_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS locked_until TIMESTAMPTZ;
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMPTZ;
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ;
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS can_download BOOLEAN NOT NULL DEFAULT TRUE;
```
새 테이블(`access_log`, `backup_log`, `event_request`, `popup_ack`)은 `create_all`이 만든다. 이어서 `python -m app.jobs.encrypt_existing`.

## Phase 17 — 증빙자료 첨부 · 상환일 메모

### 증빙자료(§21·22 근거·첨부자료)
- 붙일 수 있는 대상: 차입금(`LOAN`), 이벤트(`LOAN_EVENT`), 결재 기안(`EVENT_REQUEST`), 납부(`PAYMENT` — 입금증).
- API
  - `POST /api/v1/loans/{loan_id}/attachments` (multipart: `file`, `owner_type`, `owner_id`, `description`) — 자금담당·관리자
  - `GET  /api/v1/loans/{loan_id}/attachments?owner_type=&owner_id=&include_inactive=` — 차입금을 볼 수 있는 사용자(SPC 담당자는 자기 SPC만)
  - `GET  /api/v1/attachments/{id}/download` — 다운로드 권한(`can_download`) 필요, 접근기록에 DOWNLOAD로 남음
  - `POST /api/v1/attachments/{id}/deactivate {reason}` — 지우지 않고 사용중지(감사로그 CANCEL)
  - `GET  /api/v1/attachments/settings` — 최대 용량·허용 확장자
- 저장: 파일 본문을 **AES-256-GCM으로 암호화**해 `ATTACHMENT_DIR`에 무작위 이름으로 둔다. 원본 SHA-256을 DB에 남기고 내려받을 때 확인한다(손상 시 410).
- 검사: 확장자 허용목록(`ATTACHMENT_EXTENSIONS`), 최대 용량(`ATTACHMENT_MAX_MB`, 기본 20MB), 빈 파일 거부, 확장자와 상관없이 실행파일(MZ/ELF/스크립트) 거부, 경로 문자 제거.
- 결재 모드에서는 기안자가 기안에 증빙을 붙이고, **승인되면 만들어진 이벤트에도 같은 파일이 연결**된다(파일 복사 없음).

### 상환일 메모(§20 사용자 비고)
- 원리금 테이블 행은 버전마다 새로 만들어지므로, 메모는 **차입금 + 상환예정일**에 붙어 버전이 바뀌어도 그대로 보인다(작성 당시 버전 번호도 남김).
- API: `GET/POST /api/v1/loans/{loan_id}/schedule-notes`, `POST /api/v1/schedule-notes/{id}/deactivate` (작성·사용중지는 자금담당·관리자).
- 원리금 테이블 Excel과 리포트(원리금 테이블 묶음)의 비고 열에 `… | 메모: 내용`으로 함께 나간다.

### 설정·운영
```
ATTACHMENT_DIR=./attachments        # 운영에서는 백업되는 별도 디스크 경로 권장
ATTACHMENT_MAX_MB=20
ATTACHMENT_EXTENSIONS=pdf,hwp,hwpx,doc,docx,xls,xlsx,csv,ppt,pptx,jpg,jpeg,png,gif,txt,zip
```
- 백업(`python -m app.jobs.backup run`)은 DB 덤프와 같은 시각 이름으로 증빙 폴더를 `…_files.tar`로 묶는다(파일은 이미 암호화돼 있어 다시 암호화하지 않음). 보관 규칙으로 덤프를 정리하면 짝이 되는 묶음도 함께 정리된다.
- **암호화 키(`DATA_ENCRYPTION_KEY`)를 잃으면 증빙파일도 열 수 없다.** 키는 DB 백업과 다른 곳에 따로 보관한다.
- 기존 DB: `python -m app.jobs.init_db`를 한 번 실행하면 `attachment`, `schedule_note` 테이블이 만들어진다.

## DB 참고사항
RowType enum에 `DEFERRAL_RECOVERY` 값이 추가되었습니다. 기존 DB에 Phase 5까지만 적용되어 있다면:
```sql
ALTER TYPE rowtype ADD VALUE IF NOT EXISTS 'DEFERRAL_RECOVERY';
```

## Phase 3 추가 사항
- `engine/schedule_engine.py`: 순수 계산엔진(DB를 모름) — 거치기간/원금균등/원리금균등, 회계연도 경계 분리, §37 자동검증
- `domain/schedule_service.py`: 엔진과 DB(Loan/InterestRateHistory/Code)를 연결 — 금리는 하드코딩이 아니라 Phase2의 rate_service를 통해 실시간 조회
- `models/schedule.py`: RepaymentScheduleVersion/Row (버전관리, is_current)
- `api/v1/schedule.py`: `POST /loans/{id}/schedule/generate`, `GET /loans/{id}/schedule`
- `tests/test_phase3_schedule.py`: **1단계 Test A/B를 실제 API+DB로 재현해 원본 엑셀과 행 단위로 대조** — Test A는 161/161행 완전 일치, Test B는 90행 중 88행 일치(나머지 2행은 원본의 수동보정을 의도적으로 재현하지 않은 부분이며 최종 잔액은 동일하게 0원으로 수렴함)

