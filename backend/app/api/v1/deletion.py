"""Phase 18c: 관리자 삭제(숨김)·복구 API — 시스템관리자만. 사유 필수, 모든 삭제·복구는 기록·변경이력에 남는다."""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import require_roles
from app.domain import deletion_service as svc
from app.models.loan import Loan
from app.models.organization import DeletionRecord, Dormitory, SPC, User, UserRole

router = APIRouter(prefix="/admin", tags=["관리자 삭제·복구"])
ADMIN = require_roles(UserRole.SYSTEM_ADMIN)


class ReasonIn(BaseModel):
    reason: str = Field(..., min_length=1, max_length=300)


class DeletionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    target_type: str
    target_id: uuid.UUID
    label: str
    reason: str
    detail: dict
    created_at: datetime
    created_by: uuid.UUID | None
    restored_at: datetime | None
    restored_by: uuid.UUID | None
    deleted_by_name: str | None = None
    restored_by_name: str | None = None


def _out(db: Session, recs: list[DeletionRecord]) -> list[DeletionRead]:
    ids = {x for r in recs for x in (r.created_by, r.restored_by) if x}
    names = {u.id: u.name for u in db.execute(select(User).where(User.id.in_(ids))).scalars()} if ids else {}
    out = []
    for r in recs:
        o = DeletionRead.model_validate(r)
        o.deleted_by_name, o.restored_by_name = names.get(r.created_by), names.get(r.restored_by)
        out.append(o)
    return out


def _run(db: Session, fn, *args) -> DeletionRead:
    try:
        rec = fn(db, *args)
    except svc.DeletionError as e:
        db.rollback()
        raise HTTPException(status_code=e.status, detail=str(e))
    return _out(db, [rec])[0]


@router.post("/delete/dormitories/{dormitory_id}", response_model=DeletionRead)
def delete_dormitory(dormitory_id: uuid.UUID, body: ReasonIn, db: Session = Depends(get_db), user: User = Depends(ADMIN)):
    return _run(db, svc.delete_dormitory, dormitory_id, body.reason, user)


@router.post("/delete/loans/{loan_id}", response_model=DeletionRead)
def delete_loan(loan_id: uuid.UUID, body: ReasonIn, db: Session = Depends(get_db), user: User = Depends(ADMIN)):
    return _run(db, svc.delete_loan, loan_id, body.reason, user)


@router.post("/delete/loans/{loan_id}/schedule", response_model=DeletionRead)
def delete_schedule(loan_id: uuid.UUID, body: ReasonIn, db: Session = Depends(get_db), user: User = Depends(ADMIN)):
    return _run(db, svc.delete_schedule, loan_id, body.reason, user)


@router.post("/delete/events/{event_id}", response_model=DeletionRead)
def delete_event(event_id: uuid.UUID, body: ReasonIn, db: Session = Depends(get_db), user: User = Depends(ADMIN)):
    return _run(db, svc.delete_event, event_id, body.reason, user)


@router.post("/delete/schedule-versions/{version_id}", response_model=DeletionRead)
def delete_version(version_id: uuid.UUID, body: ReasonIn, db: Session = Depends(get_db), user: User = Depends(ADMIN)):
    return _run(db, svc.delete_version, version_id, body.reason, user)


@router.post("/delete/notices/{notice_id}", response_model=DeletionRead)
def delete_notice(notice_id: uuid.UUID, body: ReasonIn, db: Session = Depends(get_db), user: User = Depends(ADMIN)):
    return _run(db, svc.delete_notice, notice_id, body.reason, user)


@router.get("/deletions", response_model=list[DeletionRead])
def list_deletions(include_restored: bool = False, db: Session = Depends(get_db), user: User = Depends(ADMIN)):
    q = select(DeletionRecord).where(DeletionRecord.is_deleted.is_(False))
    if not include_restored:
        q = q.where(DeletionRecord.restored_at.is_(None))
    return _out(db, list(db.execute(q.order_by(DeletionRecord.created_at.desc()).limit(500)).scalars()))


@router.post("/deletions/{record_id}/restore", response_model=DeletionRead)
def restore(record_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(ADMIN)):
    return _run(db, svc.restore, record_id, user)


@router.get("/dormitories")
def dormitory_overview(db: Session = Depends(get_db), user: User = Depends(ADMIN)):
    """삭제 관리 화면용: 기숙사별 차입금 수(삭제 안 된 것)."""
    counts = dict(db.execute(select(Loan.dormitory_id, func.count()).where(Loan.is_deleted.is_(False))
                             .group_by(Loan.dormitory_id)).all())
    rows = db.execute(select(Dormitory, SPC).join(SPC, SPC.id == Dormitory.spc_id)
                      .where(Dormitory.is_deleted.is_(False)).order_by(Dormitory.dormitory_name)).all()
    return [{"id": d.id, "dormitory_name": d.dormitory_name, "school_name": d.school_name, "spc_name": s.spc_name,
             "loan_count": counts.get(d.id, 0), "created_at": d.created_at} for d, s in rows]
