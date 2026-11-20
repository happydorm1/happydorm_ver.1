"""Phase 14 관리자 조회: 접근기록, 전체 감사로그(사용자·권한 변경 포함), 백업 이력, 보안 설정 점검."""
import uuid
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import hashlib
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field
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


class BackupDownloadIn(BaseModel):
    password: str = Field(..., min_length=8, max_length=200)


@router.post("/backups/download")
def backup_download(payload: BackupDownloadIn, db: Session = Depends(get_db), current_user: User = Depends(ADMIN)):
    """웹에서 백업 — 데이터·증빙파일 전체를 '백업 비밀번호'로 암호화한 파일(.dlt) 하나로 내려준다.
    서버에 pg_dump·예약 작업이 없는 환경(Render 무료, Codespaces)에서도 쓸 수 있다. 내려받은 파일은 PC 등 서버 밖에 보관한다."""
    from app.domain.audit import record_audit
    from app.jobs import transfer
    started = datetime.now(timezone.utc)
    try:
        data, meta = transfer.export_bytes()
        blob = transfer.seal(data, payload.password)
    except Exception as e:  # noqa: BLE001
        db.add(BackupLog(kind="BACKUP", status="FAILED", started_at=started, finished_at=datetime.now(timezone.utc),
                         message=f"웹 백업 실패: {str(e)[:300]}"))
        db.commit()
        from app.core.crypto import CryptoError
        if isinstance(e, CryptoError):
            raise HTTPException(status_code=500, detail="암호화된 값을 풀지 못해 백업하지 못했습니다 — 서버의 암호화 키(DATA_ENCRYPTION_KEY)가 바뀐 것 같습니다. 관리자에게 알려 주세요.")
        raise HTTPException(status_code=500, detail="백업 파일을 만들지 못했습니다. 잠시 뒤 다시 시도하세요.")
    name = f"행복기숙사_백업_{datetime.now(KST):%Y%m%d_%H%M}.dlt"
    db.add(BackupLog(kind="BACKUP", status="SUCCESS", started_at=started, finished_at=datetime.now(timezone.utc),
                     file_name=name, size_bytes=len(blob), sha256=hashlib.sha256(blob).hexdigest(), encrypted=True,
                     message=f"웹에서 내려받음 — {current_user.name} · 차입금 {meta['counts'].get('loan', 0)}건"))
    record_audit(db, table_name="backup_log", record_id=current_user.id, loan_id=None, action="DOWNLOAD", user_id=current_user.id,
                 field="backup", after=name)
    db.commit()
    return Response(content=blob, media_type="application/octet-stream",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


MAX_RESTORE_BYTES = 200 * 1024 * 1024


@router.post("/backups/restore")
async def backup_restore(file: UploadFile = File(...), password: str = Form(..., min_length=1, max_length=200),
                         confirm: str = Form(...), db: Session = Depends(get_db), current_user: User = Depends(ADMIN)):
    """백업 파일(.dlt)로 되돌리기 — 지금 DB의 데이터를 모두 지우고 파일 내용으로 바꾼다. 확인 문구 '되돌리기'를 입력해야 실행된다."""
    from app.jobs import transfer
    if confirm.strip() != "되돌리기":
        raise HTTPException(status_code=422, detail="확인 문구로 '되돌리기'를 정확히 입력하세요.")
    blob = await file.read()
    if len(blob) > MAX_RESTORE_BYTES:
        raise HTTPException(status_code=413, detail="파일이 너무 큽니다(200MB 이하).")
    try:
        meta = transfer.read_meta(blob, password)
    except transfer.TransferError as e:
        raise HTTPException(status_code=422, detail=str(e))
    who = f"{current_user.name}({current_user.email})"
    # 백업 이력은 되돌리지 않고 지금 것을 그대로 이어 간다(되돌린 뒤 '백업 없음' 안내가 다시 뜨지 않게)
    keep_logs = [{c.name: getattr(r, c.name) for c in BackupLog.__table__.columns}
                 for r in db.execute(select(BackupLog)).scalars()]
    db.rollback()          # 이 요청이 잡고 있는 읽기 잠금을 풀어야 전체 비우기(TRUNCATE)가 기다리지 않는다
    db.close()
    started = datetime.now(timezone.utc)
    try:
        r = transfer.import_bytes(blob, password)
    except Exception as e:  # noqa: BLE001  (한 덩어리 작업이라 실패하면 아무것도 바뀌지 않음)
        raise HTTPException(status_code=500, detail=f"되돌리기에 실패해 모두 취소했습니다(현재 데이터는 그대로입니다): {str(getattr(e, 'orig', e)).splitlines()[0][:200]}")
    from app.core.database import SessionLocal
    with SessionLocal() as s2:
        have = set(s2.execute(select(BackupLog.id)).scalars())
        for row in keep_logs:
            if row["id"] not in have:
                s2.add(BackupLog(**row))
        s2.add(BackupLog(kind="RESTORE", status="SUCCESS", started_at=started, finished_at=datetime.now(timezone.utc),
                         file_name=(file.filename or "")[:200], size_bytes=len(blob), encrypted=True,
                         message=f"웹에서 되돌림 — {who} · 백업 시각 {meta.get('exported_at', '')[:19]}"))
        s2.commit()
    return {"ok": True, "counts": r["counts"], "files": r["files"], "exported_at": meta.get("exported_at"),
            "skipped_tables": r["skipped_tables"], "dropped_columns": r["dropped_columns"]}


@router.get("/security-status")
def security_status(current_user: User = Depends(ADMIN)):
    """운영 전환 전 점검표 — 현재 설정이 운영 기준을 통과하는지(비밀값 자체는 보여주지 않는다)."""
    return {"app_env": settings.APP_ENV, "production_ready": not production_problems(), "problems": production_problems(),
            "login_lock": {"max_failures": settings.LOGIN_MAX_FAILURES, "lock_minutes": settings.LOGIN_LOCK_MINUTES},
            "token_expire_minutes": settings.ACCESS_TOKEN_EXPIRE_MINUTES,
            "event_approval_required": settings.EVENT_APPROVAL_REQUIRED,
            "data_encryption_key_set": bool(settings.DATA_ENCRYPTION_KEY), "backup_encrypt": settings.BACKUP_ENCRYPT,
            "checked_at": datetime.now(timezone.utc)}
