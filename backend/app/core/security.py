import uuid
from datetime import datetime, timezone, timedelta

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
import jwt
from jwt import PyJWTError as JWTError
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.organization import User, UserRole

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(subject: str) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": subject, "exp": expire, "iat": int(now.timestamp())}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


def get_current_user(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="인증 정보가 유효하지 않습니다.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        user_id: str | None = payload.get("sub")
        if user_id is None:
            raise credentials_exception
        uid = uuid.UUID(user_id)
    except (JWTError, ValueError):
        raise credentials_exception

    user = db.get(User, uid)
    if user is None or not user.is_active or user.is_deleted:
        raise credentials_exception
    # 비밀번호가 바뀌면(본인 변경·관리자 초기화) 그 전에 발급된 토큰은 모두 무효
    if user.password_changed_at is not None and int(payload.get("iat", 0)) < int(user.password_changed_at.timestamp()):
        raise credentials_exception
    return user


def require_roles(*allowed_roles: UserRole):
    """엔드포인트별 RBAC 제어. 예: Depends(require_roles(UserRole.SYSTEM_ADMIN))"""

    def checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="해당 작업에 대한 권한이 없습니다.",
            )
        return user

    return checker


def effective_can_download(user: User) -> bool:
    """다운로드 제한은 조회자에게만 적용(§45 '다운로드 가능 여부는 별도 설정'). 다른 역할은 항상 가능."""
    return user.can_download if user.role == UserRole.VIEWER else True


def ensure_can_download(user: User) -> None:
    if not effective_can_download(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="다운로드 권한이 없습니다. 관리자에게 요청하세요.")
