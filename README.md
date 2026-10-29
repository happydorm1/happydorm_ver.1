# 기숙사 차입금 원리금 산출·상환관리 시스템 — 로컬 실행 패키지

Phase 17 백엔드(FastAPI)와 프론트엔드(React)를 **내 PC에서 바로 켤 수 있게** 묶은 패키지입니다.
화면은 미리 빌드해 두었기 때문에 **Python과 PostgreSQL만 있으면 됩니다**(Node.js 불필요).
실행하면 브라우저에서 `http://127.0.0.1:8000` 하나로 화면과 API가 함께 열립니다.

---

## 0. 설치 없이 GitHub Codespaces에서 실행하기 (시연·개발용)

내 PC에 아무것도 설치하지 않고 브라우저에서 실제 시스템을 띄우는 방법입니다.

### ① 저장소에 올리기
1. github.com → **New repository** → 이름 입력 → **Private** 선택 → Create
2. 이 폴더(`dormitory-loan-local`) **안의 내용 전체**를 저장소 맨 위에 올립니다.
   올린 뒤 저장소 첫 화면에 `.devcontainer`, `backend`, `frontend`, `web`, `README.md`가 바로 보여야 합니다.
   (`dormitory-loan-local` 폴더가 한 겹 더 보이면 Codespaces가 설정을 찾지 못합니다.)
   - **GitHub Desktop**(권장): File → Add local repository → 이 폴더 선택 → "create a repository" → Publish repository(Private 체크)
   - **웹 업로드**: 저장소 화면의 "uploading an existing file" → 이 폴더 안의 항목들을 끌어다 놓기.
     단, 웹 업로드는 이름이 `.`으로 시작하는 폴더(`.devcontainer`)가 빠질 수 있으니, 올린 뒤 꼭 보이는지 확인하세요.
   - **명령어**: `git init && git add . && git commit -m "init" && git branch -M main && git remote add origin https://github.com/계정/저장소.git && git push -u origin main`

### ② Codespace 만들기
1. 저장소 화면 → 초록색 **Code** 버튼 → **Codespaces** 탭 → **Create codespace on main**
2. 처음 한 번은 **5~10분** 걸립니다(PostgreSQL 준비 → 패키지 설치 → 데모 데이터 생성). 진행 상황은 아래 터미널에 보입니다.
3. "기숙사 차입금 관리 시스템 실행 중"이 뜨면 새 탭이 자동으로 열립니다.
   안 열리면 아래 **포트(PORTS)** 탭 → 8000번 줄의 🌐(브라우저에서 열기) 아이콘.
4. 로그인: `demo-admin@example.com` / `demo1234!`

### ③ 다시 쓸 때 / 끌 때
- 30분 정도 쓰지 않으면 자동으로 멈춥니다(데이터는 남아 있음). github.com/codespaces 에서 다시 열면 서버가 자동으로 켜집니다.
- 서버만 다시 켜기: 터미널에서 `bash .devcontainer/start.sh`
- 다 쓴 Codespace는 github.com/codespaces → … → **Delete**로 지우세요(무료 사용시간·저장공간 절약).

### Codespaces 주의사항
- **시연·개발·테스트 전용**입니다. 실제 계좌·업무 데이터를 넣지 마세요. 실제 운영은 아래 1~3번처럼 재단 PC·서버에 설치합니다.
- 8000번 포트는 기본이 **Private**(본인 GitHub 로그인 필요)입니다. 다른 사람에게 보여줄 때만 포트를 Public으로 바꾸고, 끝나면 되돌리세요.
- 데모 데이터의 기준일은 Codespace를 만든 날입니다. 상단 '기준일'을 바꾸면 로그인 알림 팝업이 다시 뜰 수 있는데 정상입니다('나중에'는 기준일별로 기억함).
- GitHub **Pages**로는 실행되지 않습니다(정적 파일만 제공 — 로그인·계산을 하는 백엔드와 DB가 없음).

---

## 1. 준비물 (최초 1회)

| 프로그램 | 버전 | 받는 곳 / 주의 |
|---|---|---|
| Python | **3.12 또는 3.13** | python.org — 설치 첫 화면에서 **"Add python.exe to PATH" 체크** |
| PostgreSQL | 14 이상 (권장 16) | postgresql.org/download → Windows installer. 설치 중 정하는 **postgres 비밀번호를 기억**하세요. 포트는 기본 5432 |

> Python 3.14는 고정된 패키지 버전과 맞지 않을 수 있어 3.12/3.13을 권장합니다.

## 2. 설치 (최초 1회)

**Windows**: `1_setup_windows.bat` 더블클릭
**Mac/Linux**: 터미널에서 `./1_setup_mac_linux.sh`

진행 중 두 가지를 묻습니다.
1. **PostgreSQL 관리자(postgres) 비밀번호** — PostgreSQL 설치 때 정한 것
2. **데모로 시작할까요?**
   - `Y`(기본) → 가상 기숙사 8곳·차입금 9건·납부 269건이 들어간 데모 DB(`dormitory_demo`)
   - `N` → 빈 DB(`dormitory_loan`) + 최초 시스템관리자 계정 생성(실제 업무용)

설치 도우미가 자동으로 하는 일: 앱 전용 DB 계정(`dormuser`)·DB 생성 → `backend/.env` 작성(비밀키·암호화 키 무작위 생성) → 테이블·기준정보 생성 → 데모 데이터 또는 관리자 계정.

## 3. 실행

**Windows**: `2_start_windows.bat` 더블클릭 → 브라우저가 자동으로 열립니다.
**Mac/Linux**: `./2_start_mac_linux.sh`

끄려면 검은 창에서 `Ctrl + C` 또는 창 닫기.

### 데모 계정 (비밀번호 모두 `demo1234!`)
| 역할 | 아이디 |
|---|---|
| 시스템관리자 | demo-admin@example.com |
| 재단 자금담당 | demo-fund@example.com |
| 재단 결재권자 | demo-approver@example.com |
| SPC 담당자 | demo-spc@example.com |
| 조회자(다운로드 제한) | demo-viewer@example.com |

로그인 직후 뜨는 팝업(중도변수 미처리, 공휴일 미등록 안내)은 데모에 일부러 넣어 둔 정상 동작입니다.

## 4. 메뉴 한눈에 보기
- **대시보드**: KPI 9종, 상환예정·금리변동·미납·확인필요 알림, 월별 상환예정 차트, 재원별 잔여원금, Excel 자료 8종
- **차입금 관리 / 원리금 테이블**: 기숙사·차입금 등록, 원리금 테이블 자동 생성(버전관리), 계산검증 9항목, 버전 비교, Excel
- **이벤트 관리**: 금리변동·상환유예·원금가속·조기상환·대환, 취소, 결재함
- **실제 상환관리**: 미납 현황, 납부 입력·취소, 입금증 첨부
- **안내공문**: 기숙사·상환일 선택 → 미리보기 → 발급 → Excel/PDF
- **알림 / 기준정보 / 사용자·권한 / 감사로그**

## 5. 운영에 쓸 때 꼭 알아둘 것
- **`backend/.env`의 `DATA_ENCRYPTION_KEY`를 따로 안전하게 보관하세요.** 잃으면 암호화된 계좌·증빙파일·백업을 열 수 없습니다.
- 데모 ↔ 실사용 전환: 설치 스크립트를 다시 실행해 모드를 고르면 됩니다(기존 DB와 암호화 키는 그대로 유지).
- **공휴일**: 공공데이터포털 '한국천문연구원 특일 정보' 서비스키를 `.env`의 `KASI_SERVICE_KEY=`에 넣고, 기준정보 › 공휴일에서 동기화하세요. 넣기 전까지는 주말만 휴일로 계산됩니다.
- **매일 작업**(알림 생성·계산검증): `3_daily_scan_windows.bat`을 Windows 작업 스케줄러에 매일 06:00으로 등록.
- **백업**: `backend` 폴더에서 `venv\Scripts\python -m app.jobs.backup run` (PostgreSQL의 `pg_dump`가 PATH에 있어야 함 — 보통 `C:\Program Files\PostgreSQL\16\bin`).
- **공문 PDF**는 LibreOffice가 설치돼 있어야 합니다(없으면 Excel만 가능).
- 포트 변경: `PORT=8080` 환경변수(Windows: 실행 전 `set PORT=8080`). 다른 PC에서 접속하려면 `HOST=0.0.0.0` — 단 이 경우 사내망 보안(HTTPS 프록시·방화벽)을 갖춘 뒤 `APP_ENV=production`으로 운영하세요(원본 backend/README.md의 Phase 14 참고).

## 6. 화면을 고치고 싶을 때 (개발자용)
`frontend` 폴더에 원본 소스가 그대로 있습니다. Node.js 20+ 설치 후:
```bash
cd frontend
npm ci
npm run dev        # http://localhost:5173 (API는 127.0.0.1:8000으로 자동 연결 — 2_start 실행 중이어야 함)
npm run build      # 고친 뒤 빌드 → dist 폴더 내용을 ../web 으로 복사하면 2_start에 반영
```

## 7. 문제 해결
| 증상 | 해결 |
|---|---|
| `Python 3.12 or 3.13 is required` | Python 3.13 설치(PATH 체크) 후 다시 실행 |
| `PostgreSQL에 접속하지 못했습니다` | 서비스(postgresql-x64-16) 실행 여부, postgres 비밀번호 확인 |
| 브라우저에 아무것도 안 뜸 | 검은 창에 오류가 있는지 확인, `http://127.0.0.1:8000` 직접 입력 |
| `address already in use` | 이미 실행 중이거나 8000 포트 사용 중 → `set PORT=8080` 후 실행 |
| 로그인 5회 실패로 잠김 | 15분 뒤 재시도 또는 관리자가 사용자·권한에서 잠금 해제 |
| `비밀번호 정책 위반` | 2종류 조합 10자 이상 또는 3종류 조합 8자 이상, 이메일 아이디 포함 금지 |

## 8. 검증 결과 (이 패키지로 실제 실행해 확인)
- 백엔드 자동 테스트: **81개 통과**, 7개 건너뜀(원본 엑셀 첨부 파일이 있어야 도는 대조 테스트)
- 화면 E2E 4종 통과: 대시보드·권한·모바일 폭 / 차입금·원리금 테이블 / 이벤트·납부·공문·관리자 / 증빙자료·메모
- 이 패키지(설치 스크립트 → 단일 포트 실행)로 새 DB에서 처음부터 설치 후 E2E 재통과, 빈 DB(실사용) 모드 설치 확인
- Windows `.bat`은 같은 단계를 그대로 옮긴 것으로, Windows 실기기에서는 아직 실행해 보지 않았습니다.

원본 개발 문서: `backend/README.md`, `frontend/README.md`
