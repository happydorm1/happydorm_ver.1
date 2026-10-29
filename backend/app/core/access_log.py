"""
접근기록 미들웨어 — 모든 API 호출을 '누가·언제·무엇을(차입금/기숙사)·결과' 단위로 남긴다(§45).
토큰은 서명만 확인해 사용자 ID를 얻는다(권한 판정은 각 API가 따로 한다). 기록이 실패해도 업무 응답은 막지 않는다.
"""
import logging
import re
import time
import uuid

import jwt
from jwt import PyJWTError as JWTError
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.security import AccessLog

log = logging.getLogger("access_log")
UUID_RE = r"([0-9a-fA-F-]{36})"
SKIP = ("/health", "/docs", "/openapi.json", "/redoc")


def _uuid(pattern: str, path: str):
    m = re.search(pattern, path)
    try:
        return uuid.UUID(m.group(1)) if m else None
    except ValueError:
        return None


def _user_id(auth: str | None):
    if not auth or not auth.lower().startswith("bearer "):
        return None
    try:
        sub = jwt.decode(auth[7:], settings.SECRET_KEY, algorithms=["HS256"]).get("sub")
        return uuid.UUID(sub) if sub else None
    except (JWTError, ValueError):
        return None


def classify(method: str, path: str, status: int, content_disposition: str | None) -> str:
    if status in (401, 403):
        return "DENIED"
    if content_disposition and "attachment" in content_disposition:
        return "DOWNLOAD"
    return "VIEW" if method == "GET" else "CHANGE"


def write_access_log(**kw) -> None:
    db = SessionLocal()
    try:
        db.add(AccessLog(**kw))
        db.commit()
    except Exception:  # noqa: BLE001 — 기록 실패가 업무를 막으면 안 된다
        db.rollback()
        log.exception("접근기록 저장 실패")
    finally:
        db.close()


class AccessLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path
        if request.method == "OPTIONS" or not path.startswith("/api/") or path.startswith(SKIP) or path.endswith("/auth/login"):
            return await call_next(request)   # 로그인은 API가 직접 성공/실패/잠금을 기록
        t0 = time.perf_counter()
        response = await call_next(request)
        write_access_log(
            user_id=_user_id(request.headers.get("authorization")),
            action=classify(request.method, path, response.status_code, response.headers.get("content-disposition")),
            method=request.method, path=path[:300], query=str(request.url.query)[:500] or None,
            status_code=response.status_code,
            loan_id=_uuid(r"/loans/" + UUID_RE, path) or _uuid(r"loan_id=" + UUID_RE, str(request.url.query)),
            dormitory_id=_uuid(r"/dormitories/" + UUID_RE, path) or _uuid(r"dormitory_id=" + UUID_RE, str(request.url.query)),
            ip=(request.client.host if request.client else None),
            user_agent=(request.headers.get("user-agent") or "")[:200] or None,
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )
        return response
