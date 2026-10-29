"""Phase 14 관리자 조회: 접근기록, 전체 감사로그(사용자·권한 변경 포함), 백업 이력, 보안 설정 점검."""
import uuid
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.security import require_roles
from app.core.startup_checks import production_problems
from app.models.audit import AuditLog
from app.models.organization import User, UserRole
from app.models.security import AccessLog, BackupLog

router = APIRouter(prefix="/admin", tags=["관리자(보안·백업)"])
ADMIN = require_roles(UserRole.SYSTEM_ADMIN)
KST = ZoneInfo("Asia/Seoul")


def _range(date_from: date | None, date_to: date | None):
    start = datetime.combine(date_from, time(0), tzinfo=KST) if date_from else None
    end = datetime.combine(date_to + timedelta(days=1), time(0), tzinfo=KST) if date_to else None
    return start, end


def _names(db: Session, ids) -> dict:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {u.id: f"{u.name}({u.email})" for u in db.execute(select(User).where(User.id.in_(ids))).scalars()}


@router.get("/access-logs")
def access_logs(user_id: uuid.UUID | None = None, loan_id: uuid.UUID | None = None, dormitory_id: uuid.UUID | None = None,
                action: list[str] | None = Query(default=None), date_from: date | None = None, date_to: date | None = None,
                limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0),
                db: Session = Depends(get_db), current_user: User = Depends(ADMIN)):
    """누가 어떤 차입금을 언제 조회·변경·다운로드했는지. action: VIEW/CHANGE/DOWNLOAD/DENIED/LOGIN_SUCCESS/LOGIN_FAILED/LOGIN_LOCKED"""
    q = select(AccessLog)
    if user_id:
        q = q.where(AccessLog.user_id == user_id)
    if loan_id:
        q = q.where(AccessLog.loan_id == loan_id)
    if dormitory_id:
        q = q.where(AccessLog.dormitory_id == dormitory_id)
    if action:
        q = q.where(AccessLog.action.in_(action))
    start, end = _range(date_from, date_to)
    if start:
        q = q.where(AccessLog.at >= start)
    if end:
        q = q.where(AccessLog.at < end)
    rows = db.execute(q.order_by(AccessLog.at.desc()).limit(limit).offset(offset)).scalars().all()
    names = _names(db, [r.user_id for r in rows])
    return [{"at": r.at, "user_id": r.user_id, "user": names.get(r.user_id) or r.email, "action": r.action, "method": r.method,
             "path": r.path, "query": r.query, "status_code": r.status_code, "loan_id": r.loan_id, "dormitory_id": r.dormitory_id,
             "ip": r.ip, "duration_ms": r.duration_ms, "detail": r.detail} for r in rows]


@router.get("/audit-logs")
def audit_logs(table_name: str | None = None, loan_id: uuid.UUID | None = None, changed_by: uuid.UUID | None = None,
               date_from: date | None = None, date_to: date | None = None,
               limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0),
               db: Session = Depends(get_db), current_user: User = Depends(ADMIN)):
    """전체 변경이력. table_name=app_user 이면 사용자·권한 변경 이력."""
    q = select(AuditLog)
    if table_name:
        q = q.where(AuditLog.table_name == table_name)
    if loan_id:
        q = q.where(AuditLog.loan_id == loan_id)
    if changed_by:
        q = q.where(AuditLog.changed_by == changed_by)
    start, end = _range(date_from, date_to)
    if start:
        q = q.where(AuditLog.changed_at >= start)
    if end:
        q = q.where(AuditLog.changed_at < end)
    rows = db.execute(q.order_by(AuditLog.changed_at.desc()).limit(limit).offset(offset)).scalars().all()
    names = _names(db, [r.changed_by for r in rows])
    return [{"changed_at": r.changed_at, "table_name": r.table_name, "record_id": r.record_id, "loan_id": r.loan_id,
             "action": r.action, "field": r.changed_field, "before": r.before_value, "after": r.after_value,
             "changed_by": r.changed_by, "changed_by_name": names.get(r.changed_by), "reason": r.change_reason} for r in rows]


@router.get("/backups")
def backups(limit: int = Query(50, ge=1, le=500), db: Session = Depends(get_db), current_user: User = Depends(ADMIN)):
    rows = db.execute(select(BackupLog).order_by(BackupLog.started_at.desc()).limit(limit)).scalars().all()
    last_ok = db.execute(select(BackupLog).where(BackupLog.kind == "BACKUP", BackupLog.status == "SUCCESS")
                         .order_by(BackupLog.started_at.desc())).scalars().first()
    last_drill = db.execute(select(BackupLog).where(BackupLog.kind == "RESTORE_TEST", BackupLog.status == "SUCCESS")
                            .order_by(BackupLog.started_at.desc())).scalars().first()
    return {"last_success_at": last_ok.started_at if last_ok else None,
            "last_restore_test_at": last_drill.started_at if last_drill else None,
            "items": [{"kind": r.kind, "status": r.status, "started_at": r.started_at, "finished_at": r.finished_at,
                       "file_name": r.file_name, "size_bytes": r.size_bytes, "sha256": r.sha256, "encrypted": r.encrypted,
                       "message": r.message} for r in rows]}


@router.get("/security-status")
def security_status(current_user: User = Depends(ADMIN)):
    """운영 전환 전 점검표 — 현재 설정이 운영 기준을 통과하는지(비밀값 자체는 보여주지 않는다)."""
    return {"app_env": settings.APP_ENV, "production_ready": not production_problems(), "problems": production_problems(),
            "login_lock": {"max_failures": settings.LOGIN_MAX_FAILURES, "lock_minutes": settings.LOGIN_LOCK_MINUTES},
            "token_expire_minutes": settings.ACCESS_TOKEN_EXPIRE_MINUTES,
            "event_approval_required": settings.EVENT_APPROVAL_REQUIRED,
            "data_encryption_key_set": bool(settings.DATA_ENCRYPTION_KEY), "backup_encrypt": settings.BACKUP_ENCRYPT,
            "checked_at": datetime.now(timezone.utc)}
