from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.v1 import api_router
from app.core.access_log import AccessLogMiddleware
from app.core.config import settings
from app.core.startup_checks import check_production_settings
from app.models import audit_listeners  # noqa: F401  (등록 이력 자동 기록 리스너)

check_production_settings()   # 운영(APP_ENV=production)에서 위험한 설정이면 서버를 띄우지 않는다

IS_PROD = settings.APP_ENV == "production"
app = FastAPI(title=settings.PROJECT_NAME,
              docs_url=None if IS_PROD else "/docs", redoc_url=None, openapi_url=None if IS_PROD else "/openapi.json")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """§45 보안 헤더 — 클릭재킹·MIME 추측 방지, 금융자료가 브라우저/프록시 캐시에 남지 않게."""
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        h = response.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Referrer-Policy", "no-referrer")
        h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if request.url.path.startswith("/api/"):
            h["Cache-Control"] = "no-store"
            h["Pragma"] = "no-cache"
        if IS_PROD:
            h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response


origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
app.add_middleware(AccessLogMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=origins != ["*"],   # 와일드카드와 자격증명을 함께 허용하지 않는다
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(api_router)


# 금리이력이 회차 기간을 덮지 못하면(예: 적용시작일이 배정일보다 늦음) 500 대신 이유를 알려준다.
from fastapi.responses import JSONResponse  # noqa: E402
from app.domain.rate_service import RateNotRegisteredError  # noqa: E402


@app.exception_handler(RateNotRegisteredError)
def rate_not_registered_handler(request, exc):
    return JSONResponse(status_code=422, content={"detail": (
        f"{exc} 금리이력 탭에서 적용시작일을 배정일(첫 이자기간 시작일) 이전으로 하는 금리를 등록한 뒤 다시 생성하세요.")})


@app.get("/health")
def health_check():
    return {"status": "ok"}
