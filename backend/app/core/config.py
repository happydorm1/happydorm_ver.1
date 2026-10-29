from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    PROJECT_NAME: str = "기숙사 차입금 원리금 산출·상환관리 시스템"
    DATABASE_URL: str = "postgresql+psycopg://user:password@localhost:5432/dormitory_loan"
    SECRET_KEY: str = "CHANGE_ME_IN_PRODUCTION"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 8

    # Phase 14 보안
    APP_ENV: str = "development"            # production이면 시작 시 보안 설정을 검사하고, API 문서 화면을 닫는다
    CORS_ORIGINS: str = "*"                 # 운영: 사내 도메인만(쉼표 구분). production에서 *는 거부
    LOGIN_MAX_FAILURES: int = 5             # 연속 실패 횟수 → 잠금
    LOGIN_LOCK_MINUTES: int = 15
    DATA_ENCRYPTION_KEY: str | None = None  # 계좌번호 등 민감필드·백업파일 AES-256 키(base64 32바이트). production 필수
    EVENT_APPROVAL_REQUIRED: bool = False   # 중도변수 이벤트 등록(기안)·승인(결재) 분리 — 업무 확정 후 켠다

    # 백업
    BACKUP_DIR: str = "./backups"
    BACKUP_ENCRYPT: bool = True             # 백업 파일 AES-256 암호화
    BACKUP_KEEP_DAILY_DAYS: int = 35        # 일 백업 보관 일수
    BACKUP_KEEP_MONTHLY: int = 60           # 월 첫 백업 보관 개월 수(5년)
    BACKUP_ALERT_HOURS: int = 36            # 이 시간 넘게 성공한 백업이 없으면 관리자에게 안내
    # 증빙자료 첨부(Phase 17) — 백업 대상에 이 폴더도 포함할 것(DB 백업에는 파일 본문이 들어가지 않는다)
    ATTACHMENT_DIR: str = "./attachments"
    # 공문 PDF 변환기(LibreOffice) 실행파일 경로. 비우면 PATH와 기본 설치 위치(Windows·Mac)를 자동으로 찾는다
    LIBREOFFICE_PATH: str = ""
    ATTACHMENT_MAX_MB: int = 20
    ATTACHMENT_EXTENSIONS: str = "pdf,hwp,hwpx,doc,docx,xls,xlsx,csv,ppt,pptx,jpg,jpeg,png,gif,txt,zip"

    # 공휴일 자동 동기화 — 공공데이터포털 '한국천문연구원 특일 정보'(data.go.kr/data/15012690)
    # 서비스키는 재단이 공공데이터포털에서 발급받아 .env에만 넣는다(코드/저장소에 넣지 않음). 비어 있으면 동기화 비활성.
    KASI_SERVICE_KEY: str | None = None
    KASI_API_URL: str = "https://apis.data.go.kr/B090041/openapi/service/SpcdeInfoService/getRestDeInfo"
    # 법정공휴일 외에 추가로 영업일에서 뺄 날(MM-DD:이름, 쉼표 구분). 업무기준 확정(2026-10-28): 5/1·12/31은 휴무일이 아님 → 기본 없음.
    # 기관 방침이 바뀌어 고정 휴무일이 생길 때만 .env에 넣는다. 규칙에서 빠진 날은 다음 동기화 때 사용중지된다.
    BANK_EXTRA_CLOSURES: str = ""
    HOLIDAY_RESYNC_DAYS: int = 7  # 임시공휴일이 늦게 지정될 수 있어 주기적으로 다시 받는다


settings = Settings()
