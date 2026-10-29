"""Phase 19: 기존 차입금 일괄 이관 API — 시스템관리자만."""
import uuid
from datetime import date, datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import require_roles
from app.domain import migration_service as svc
from app.domain.deletion_service import DeletionError
from app.models.organization import MigrationBatch, User, UserRole

router = APIRouter(prefix="/admin/migration", tags=["기존 차입금 이관"])
ADMIN = require_roles(UserRole.SYSTEM_ADMIN)
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_BYTES = 10 * 1024 * 1024


def _xlsx(content: bytes, name: str) -> Response:
    return Response(content=content, media_type=XLSX,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


class BatchRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    file_name: str
    cutoff_date: date
    mark_paid: bool
    summary: dict
    loan_ids: list
    dormitory_ids: list
    created_at: datetime
    created_by: uuid.UUID | None
    cancelled_at: datetime | None
    cancel_reason: str | None
    created_by_name: str | None = None


class ReasonIn(BaseModel):
    reason: str = Field(..., min_length=1, max_length=300)


@router.get("/template")
def template(db: Session = Depends(get_db), user: User = Depends(ADMIN)):
    return _xlsx(svc.build_template(db), "기존차입금_이관양식.xlsx")


async def _go(file: UploadFile, cutoff_date: date, mark_paid: bool, db: Session, user: User, apply: bool) -> dict:
    content = await file.read()
    if len(content) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="파일이 너무 큽니다(10MB 이하).")
    return svc.run_migration(db, content, file.filename or "이관.xlsx", cutoff=cutoff_date, mark_paid=mark_paid,
                             user=user, apply=apply)


@router.post("/validate")
async def validate(file: UploadFile = File(...), cutoff_date: date = Form(...), mark_paid: bool = Form(True),
                   db: Session = Depends(get_db), user: User = Depends(ADMIN)):
    """검사: 실제 등록·원리금 생성·납부기록까지 돌려 본 뒤 모두 되돌린다(DB에 남는 것 없음)."""
    return await _go(file, cutoff_date, mark_paid, db, user, apply=False)


@router.post("/apply")
async def apply(file: UploadFile = File(...), cutoff_date: date = Form(...), mark_paid: bool = Form(True),
                db: Session = Depends(get_db), user: User = Depends(ADMIN)):
    """이관 실행: 오류가 하나라도 있으면 아무것도 반영하지 않는다."""
    return await _go(file, cutoff_date, mark_paid, db, user, apply=True)


@router.get("/batches", response_model=list[BatchRead])
def batches(db: Session = Depends(get_db), user: User = Depends(ADMIN)):
    rows = list(db.execute(select(MigrationBatch).where(MigrationBatch.is_deleted.is_(False))
                           .order_by(MigrationBatch.created_at.desc()).limit(200)).scalars())
    ids = {r.created_by for r in rows if r.created_by}
    names = {u.id: u.name for u in db.execute(select(User).where(User.id.in_(ids))).scalars()} if ids else {}
    out = []
    for r in rows:
        o = BatchRead.model_validate(r)
        o.created_by_name = names.get(r.created_by)
        out.append(o)
    return out


def _batch(db: Session, batch_id: uuid.UUID) -> MigrationBatch:
    b = db.get(MigrationBatch, batch_id)
    if b is None or b.is_deleted:
        raise HTTPException(status_code=404, detail="이관 기록을 찾을 수 없습니다.")
    return b


@router.get("/batches/{batch_id}/report")
def report(batch_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(ADMIN)):
    b = _batch(db, batch_id)
    return _xlsx(svc.build_report(db, b), f"이관대조표_{b.cutoff_date}.xlsx")


@router.post("/batches/{batch_id}/cancel")
def cancel(batch_id: uuid.UUID, body: ReasonIn, db: Session = Depends(get_db), user: User = Depends(ADMIN)):
    b = _batch(db, batch_id)
    try:
        return svc.cancel_batch(db, b, body.reason, user)
    except DeletionError as e:
        db.rollback()
        raise HTTPException(status_code=e.status, detail=str(e))
