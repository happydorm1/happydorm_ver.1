"""운영(APP_ENV=production) 시작 전 보안 설정 검사. 하나라도 걸리면 서버가 뜨지 않는다(잘못된 설정으로 운영되는 것을 막음)."""
import base64

from app.core.config import settings

WEAK_SECRETS = {"CHANGE_ME_IN_PRODUCTION", "change-me-in-production", "test-secret-key-for-sandbox-only"}


class InsecureSettingsError(RuntimeError):
    pass


def production_problems(s=settings) -> list[str]:
    problems = []
    if s.SECRET_KEY in WEAK_SECRETS or len(s.SECRET_KEY) < 32:
        problems.append("SECRET_KEY가 기본값이거나 32자 미만입니다.")
    origins = [o.strip() for o in s.CORS_ORIGINS.split(",") if o.strip()]
    if not origins or "*" in origins:
        problems.append("CORS_ORIGINS에 사내 도메인을 지정해야 합니다(* 불가).")
    if not s.DATA_ENCRYPTION_KEY:
        problems.append("DATA_ENCRYPTION_KEY(계좌번호·백업 암호화 키)가 없습니다.")
    else:
        try:
            if len(base64.b64decode(s.DATA_ENCRYPTION_KEY)) != 32:
                problems.append("DATA_ENCRYPTION_KEY는 base64로 인코딩한 32바이트여야 합니다.")
        except ValueError:
            problems.append("DATA_ENCRYPTION_KEY가 base64 형식이 아닙니다.")
    if s.ACCESS_TOKEN_EXPIRE_MINUTES > 60 * 12:
        problems.append("ACCESS_TOKEN_EXPIRE_MINUTES가 12시간을 넘습니다.")
    if "user:password@" in s.DATABASE_URL:
        problems.append("DATABASE_URL이 예시 계정입니다.")
    return problems


def check_production_settings() -> None:
    if settings.APP_ENV != "production":
        return
    problems = production_problems()
    if problems:
        raise InsecureSettingsError("운영 보안 설정 오류 — " + " / ".join(problems))
