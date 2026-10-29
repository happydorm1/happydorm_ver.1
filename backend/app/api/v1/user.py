import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.access_log import write_access_log
from app.core.config import settings
from app.core.database import get_db
from app.core.password_policy import password_problems
from app.core.security import (
    create_access_token,
    effective_can_download,
    get_current_user,
    hash_password,
    require_roles,
    verify_password,
)
from app.domain.audit import record_audit
from app.models.organization import SPC, User, UserRole
from app.schemas.organization import UserCreate, UserRead, UserUpdate

router = APIRouter(tags=["사용자"])

# 사용자·권한 변경은 차입금과 무관하므로 loan_id 없이 감사로그(app_user)에 남긴다
AUDITED_FIELDS = ("name", "role", "spc_id", "department", "phone", "is_active", "can_download")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _client(request: Request) -> dict:
    return {"ip": request.client.host if request.client else None,
            "user_agent": (request.headers.get("user-agent") or "")[:200] or None}


def _check_password(pw: str, email: str | None) -> None:
    problems = password_problems(pw, email)
    if problems:
        raise HTTPException(status_code=422, detail="비밀번호 정책 위반: " + " ".join(problems))


def _check_spc(db: Session, role: UserRole, spc_id: uuid.UUID | None) -> None:
    if role == UserRole.SPC_MANAGER:
        if spc_id is None:
            raise HTTPException(status_code=422, detail="SPC 담당자는 소속 SPC를 지정해야 합니다.")
        spc = db.get(SPC, spc_id)
        if spc is None or spc.is_deleted:
            raise HTTPException(status_code=422, detail="소속 SPC를 찾을 수 없습니다.")


@router.post("/auth/login")
def login(request: Request, form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """
    로그인. 연속 실패 LOGIN_MAX_FAILURES회 → LOGIN_LOCK_MINUTES분 잠금(관리자가 즉시 해제 가능).
    없는 계정과 비밀번호 오류는 같은 메시지로 답한다(계정 존재 여부 노출 방지). 모든 시도는 접근기록에 남긴다.
    """
    email = form_data.username.strip()
    user = db.execute(select(User).where(User.email == email, User.is_deleted.is_(False))).scalar_one_or_none()
    now = _now()
    meta = {"email": email[:120], "method": "POST", "path": "/api/v1/auth/login", **_client(request)}

    if user is not None and user.locked_until is not None and user.locked_until > now:
        mins = max(1, int((user.locked_until - now).total_seconds() // 60) + 1)
        write_access_log(user_id=user.id, action="LOGIN_LOCKED", status_code=423, **meta)
        raise HTTPException(status_code=423, detail=f"로그인 {settings.LOGIN_MAX_FAILURES}회 실패로 계정이 잠겼습니다. "
                                                    f"약 {mins}분 뒤 다시 시도하거나 관리자에게 잠금 해제를 요청하세요.")

    if user is None or not verify_password(form_data.password, user.hashed_password):
        detail = "이메일 또는 비밀번호가 올바르지 않습니다."
        if user is not None:
            user.failed_login_count += 1
            if user.failed_login_count >= settings.LOGIN_MAX_FAILURES:
                user.locked_until = now + timedelta(minutes=settings.LOGIN_LOCK_MINUTES)
                user.failed_login_count = 0
                detail = (f"비밀번호를 {settings.LOGIN_MAX_FAILURES}회 잘못 입력해 계정이 "
                          f"{settings.LOGIN_LOCK_MINUTES}분간 잠겼습니다.")
            else:
                detail += f" (남은 시도 {settings.LOGIN_MAX_FAILURES - user.failed_login_count}회)"
            db.commit()
        write_access_log(user_id=user.id if user else None, action="LOGIN_FAILED", status_code=401, **meta)
        raise HTTPException(status_code=401, detail=detail)

    if not user.is_active:
        write_access_log(user_id=user.id, action="LOGIN_FAILED", status_code=403, detail="비활성 계정", **meta)
        raise HTTPException(status_code=403, detail="비활성화된 계정입니다.")

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    db.commit()
    write_access_log(user_id=user.id, action="LOGIN_SUCCESS", status_code=200, **meta)
    return {"access_token": create_access_token(subject=str(user.id)), "token_type": "bearer",
            "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60}


class MeRead(BaseModel):
    id: uuid.UUID
    name: str
    email: str
    role: UserRole
    spc_id: uuid.UUID | None
    can_download: bool
    last_login_at: datetime | None
    password_changed_at: datetime | None
    event_approval_required: bool = False    # 이벤트 기안·결재 분리 모드(화면이 '기안' 문구·결재함을 보이기 위해)


@router.get("/auth/me", response_model=MeRead)
def me(current_user: User = Depends(get_current_user)):
    """화면이 메뉴·버튼(다운로드 등)을 권한에 맞게 보여주기 위한 내 정보."""
    return MeRead(id=current_user.id, name=current_user.name, email=current_user.email, role=current_user.role,
                  spc_id=current_user.spc_id, can_download=effective_can_download(current_user),
                  last_login_at=current_user.last_login_at, password_changed_at=current_user.password_changed_at,
                  event_approval_required=settings.EVENT_APPROVAL_REQUIRED)


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(..., max_length=128)


@router.post("/auth/change-password")
def change_password(payload: PasswordChange, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """본인 비밀번호 변경. 변경 즉시 이전에 발급된 토큰(다른 PC 로그인 포함)은 모두 무효가 되어 다시 로그인해야 한다."""
    if not verify_password(payload.current_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="현재 비밀번호가 올바르지 않습니다.")
    if verify_password(payload.new_password, current_user.hashed_password):
        raise HTTPException(status_code=422, detail="새 비밀번호가 현재 비밀번호와 같습니다.")
    _check_password(payload.new_password, current_user.email)
    current_user.hashed_password = hash_password(payload.new_password)
    current_user.password_changed_at = _now()
    record_audit(db, table_name="app_user", record_id=current_user.id, loan_id=None, action="UPDATE",
                 user_id=current_user.id, field="password", before="***", after="***", reason="본인 비밀번호 변경")
    db.commit()
    return {"detail": "비밀번호를 변경했습니다. 다시 로그인하세요."}


@router.get("/users", response_model=list[UserRead])
def list_users(db: Session = Depends(get_db), current_user: User = Depends(require_roles(UserRole.SYSTEM_ADMIN))):
    stmt = select(User).where(User.is_deleted.is_(False)).order_by(User.name)
    return db.execute(stmt).scalars().all()


@router.post("/users", response_model=UserRead, status_code=201)
def create_user(payload: UserCreate, db: Session = Depends(get_db),
                current_user: User = Depends(require_roles(UserRole.SYSTEM_ADMIN))):
    existing = db.execute(select(User).where(User.email == payload.email)).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="이미 등록된 이메일입니다.")
    _check_password(payload.password, payload.email)
    _check_spc(db, payload.role, payload.spc_id)

    data = payload.model_dump(exclude={"password"})
    user = User(**data, hashed_password=hash_password(payload.password), created_by=current_user.id)
    db.add(user)
    db.flush()
    record_audit(db, table_name="app_user", record_id=user.id, loan_id=None, action="CREATE", user_id=current_user.id,
                 field="role", after=f"{user.email} / {user.role.value}" + (f" / SPC {user.spc_id}" if user.spc_id else ""))
    db.commit()
    db.refresh(user)
    return user


@router.put("/users/{user_id}", response_model=UserRead)
def update_user(user_id: uuid.UUID, payload: UserUpdate, db: Session = Depends(get_db),
                current_user: User = Depends(require_roles(UserRole.SYSTEM_ADMIN))):
    """사용자·권한 변경. 역할·소속·활성 여부 등 모든 변경을 감사로그에 남긴다. 비밀번호를 넣으면 관리자 초기화."""
    user = db.get(User, user_id)
    if user is None or user.is_deleted:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
    changes = payload.model_dump(exclude_unset=True)
    new_pw = changes.pop("password", None)
    reason = changes.pop("change_reason", None)
    if user.id == current_user.id and (changes.get("role", user.role) != user.role or changes.get("is_active") is False):
        raise HTTPException(status_code=422, detail="본인의 역할 변경·비활성화는 다른 관리자가 해야 합니다.")
    _check_spc(db, changes.get("role", user.role), changes.get("spc_id", user.spc_id))

    for field, value in changes.items():
        before = getattr(user, field)
        if before != value:
            record_audit(db, table_name="app_user", record_id=user.id, loan_id=None, action="UPDATE", user_id=current_user.id,
                         field=field, before=getattr(before, "value", before), after=getattr(value, "value", value), reason=reason)
            setattr(user, field, value)
    if new_pw:
        _check_password(new_pw, user.email)
        user.hashed_password = hash_password(new_pw)
        user.password_changed_at = _now()
        user.failed_login_count, user.locked_until = 0, None
        record_audit(db, table_name="app_user", record_id=user.id, loan_id=None, action="UPDATE", user_id=current_user.id,
                     field="password", before="***", after="***", reason=reason or "관리자 비밀번호 초기화")
    user.updated_by = current_user.id
    db.commit()
    db.refresh(user)
    return user


@router.post("/users/{user_id}/unlock", response_model=UserRead)
def unlock_user(user_id: uuid.UUID, db: Session = Depends(get_db),
                current_user: User = Depends(require_roles(UserRole.SYSTEM_ADMIN))):
    """로그인 실패로 잠긴 계정 즉시 해제."""
    user = db.get(User, user_id)
    if user is None or user.is_deleted:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
    record_audit(db, table_name="app_user", record_id=user.id, loan_id=None, action="UNLOCK", user_id=current_user.id,
                 field="locked_until", before=user.locked_until, after=None)
    user.failed_login_count, user.locked_until = 0, None
    db.commit()
    db.refresh(user)
    return user


@router.delete("/users/{user_id}", status_code=204)
def deactivate_user(user_id: uuid.UUID, db: Session = Depends(get_db),
                    current_user: User = Depends(require_roles(UserRole.SYSTEM_ADMIN))):
    """계정 비활성화 — 물리삭제 금지. 비활성화 즉시 기존 토큰도 쓸 수 없다."""
    user = db.get(User, user_id)
    if user is None or user.is_deleted:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
    if user.id == current_user.id:
        raise HTTPException(status_code=422, detail="본인 계정은 비활성화할 수 없습니다.")
    if user.is_active:
        record_audit(db, table_name="app_user", record_id=user.id, loan_id=None, action="UPDATE", user_id=current_user.id,
                     field="is_active", before=True, after=False)
    user.is_active = False
    user.updated_by = current_user.id
    db.commit()
