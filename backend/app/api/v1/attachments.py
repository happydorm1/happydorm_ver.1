"""Phase 17: 증빙자료 첨부와 상환일 메모 API."""
import uuid
from datetime import date, datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.access import get_accessible_loan
from app.core.config import settings
from app.core.database import get_db
from app.core.security import ensure_can_download, get_current_user, require_roles
from app.domain import attachment_service as svc
from app.domain.audit import record_audit
from app.models.attachment import OWNER_TYPES, Attachment, ScheduleNote
from app.models.event import EventRequest, LoanEvent
from app.models.organization import User, UserRole
from app.models.payment import ActualPayment
from app.models.schedule import RepaymentScheduleRow, RepaymentScheduleVersion

router = APIRouter(tags=["증빙·메모"])
WRITE_ROLES = (UserRole.SYSTEM_ADMIN, UserRole.FOUNDATION_FUND_MANAGER)
OWNER_LABEL = {"LOAN": "차입금", "LOAN_EVENT": "이벤트", "EVENT_REQUEST": "기안", "PAYMENT": "납부"}


class AttachmentRead(BaseModel):
    id: uuid.UUID
    loan_id: uuid.UUID
    owner_type: str
    owner_label: str
    owner_id: uuid.UUID
    file_name: str
    content_type: str
    size_bytes: int
    sha256: str
    description: str | None
    created_at: datetime
    created_by: uuid.UUID | None
    created_by_name: str | None = None
    active: bool
    deactivated_at: datetime | None
    deactivate_reason: str | None


def _names(db: Session, ids) -> dict:
    ids = {i for i in ids if i}
    return {u.id: u.name for u in db.execute(select(User).where(User.id.in_(ids))).scalars()} if ids else {}


def _out(a: Attachment, names: dict) -> AttachmentRead:
    return AttachmentRead(id=a.id, loan_id=a.loan_id, owner_type=a.owner_type, owner_label=OWNER_LABEL.get(a.owner_type, a.owner_type),
                          owner_id=a.owner_id, file_name=a.file_name, content_type=a.content_type, size_bytes=a.size_bytes,
                          sha256=a.sha256, description=a.description, created_at=a.created_at, created_by=a.created_by,
                          created_by_name=names.get(a.created_by), active=a.deactivated_at is None,
                          deactivated_at=a.deactivated_at, deactivate_reason=a.deactivate_reason)


def _check_owner(db: Session, loan_id: uuid.UUID, owner_type: str, owner_id: uuid.UUID) -> None:
    model = {"LOAN_EVENT": LoanEvent, "EVENT_REQUEST": EventRequest, "PAYMENT": ActualPayment}.get(owner_type)
    if owner_type == "LOAN":
        if owner_id != loan_id:
            raise HTTPException(status_code=422, detail="차입금 첨부의 대상은 그 차입금이어야 합니다.")
        return
    obj = db.get(model, owner_id) if model else None
    if obj is None or obj.loan_id != loan_id:
        raise HTTPException(status_code=422, detail=f"{OWNER_LABEL.get(owner_type, owner_type)} 대상을 이 차입금에서 찾을 수 없습니다.")


@router.get("/attachments/settings")
def attachment_settings(current_user: User = Depends(get_current_user)):
    """화면이 올리기 전에 형식·용량을 안내하기 위한 값."""
    return {"max_mb": settings.ATTACHMENT_MAX_MB, "extensions": sorted(svc.allowed_extensions())}


@router.post("/loans/{loan_id}/attachments", response_model=AttachmentRead, status_code=201)
async def upload(loan_id: uuid.UUID, file: UploadFile = File(...), owner_type: str = Form("LOAN"),
                 owner_id: uuid.UUID | None = Form(None), description: str | None = Form(None),
                 db: Session = Depends(get_db), current_user: User = Depends(require_roles(*WRITE_ROLES))):
    get_accessible_loan(db, loan_id, current_user)
    if owner_type not in OWNER_TYPES:
        raise HTTPException(status_code=422, detail="owner_type은 " + ", ".join(OWNER_TYPES) + " 중 하나입니다.")
    oid = owner_id or loan_id
    _check_owner(db, loan_id, owner_type, oid)
    data = await file.read(settings.ATTACHMENT_MAX_MB * 1024 * 1024 + 1)
    try:
        a = svc.save(db, loan_id=loan_id, owner_type=owner_type, owner_id=oid, file_name=file.filename or "file",
                     data=data, description=description, user_id=current_user.id)
    except svc.AttachmentError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return _out(a, _names(db, [a.created_by]))


@router.get("/loans/{loan_id}/attachments", response_model=list[AttachmentRead])
def list_attachments(loan_id: uuid.UUID, owner_type: str | None = None, owner_id: uuid.UUID | None = None,
                     include_inactive: bool = False, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    get_accessible_loan(db, loan_id, current_user)
    q = select(Attachment).where(Attachment.loan_id == loan_id, Attachment.is_deleted.is_(False))
    if owner_type:
        q = q.where(Attachment.owner_type == owner_type)
    if owner_id:
        q = q.where(Attachment.owner_id == owner_id)
    if not include_inactive:
        q = q.where(Attachment.deactivated_at.is_(None))
    rows = db.execute(q.order_by(Attachment.created_at.desc())).scalars().all()
    names = _names(db, [r.created_by for r in rows])
    return [_out(r, names) for r in rows]


def _get(db: Session, attachment_id: uuid.UUID, user: User) -> Attachment:
    a = db.get(Attachment, attachment_id)
    if a is None or a.is_deleted:
        raise HTTPException(status_code=404, detail="첨부를 찾을 수 없습니다.")
    get_accessible_loan(db, a.loan_id, user)
    return a


@router.get("/attachments/{attachment_id}/download")
def download(attachment_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """내려받기(접근기록에 DOWNLOAD로 남는다). 사용중지된 첨부도 기록 확인을 위해 내려받을 수 있다."""
    ensure_can_download(current_user)
    a = _get(db, attachment_id, current_user)
    try:
        data = svc.read(a)
    except svc.AttachmentError as e:
        raise HTTPException(status_code=410, detail=str(e))
    return Response(content=data, media_type=a.content_type,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(a.file_name)}",
                             "X-Content-SHA256": a.sha256})


class Deactivate(BaseModel):
    reason: str = Field(..., min_length=2, max_length=300)


@router.post("/attachments/{attachment_id}/deactivate", response_model=AttachmentRead)
def deactivate(attachment_id: uuid.UUID, body: Deactivate, db: Session = Depends(get_db),
               current_user: User = Depends(require_roles(*WRITE_ROLES))):
    a = _get(db, attachment_id, current_user)
    try:
        a = svc.deactivate(db, a, body.reason, current_user.id)
    except svc.AttachmentError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return _out(a, _names(db, [a.created_by]))


# ---------- 상환일 메모(사용자 비고, §20) ----------
class NoteIn(BaseModel):
    due_date: date
    note: str = Field(..., min_length=1, max_length=1000)


class NoteRead(BaseModel):
    id: uuid.UUID
    loan_id: uuid.UUID
    due_date: date
    note: str
    written_on_version_no: int | None
    created_at: datetime
    created_by_name: str | None
    active: bool


def _note_out(n: ScheduleNote, names: dict) -> NoteRead:
    return NoteRead(id=n.id, loan_id=n.loan_id, due_date=n.due_date, note=n.note, written_on_version_no=n.written_on_version_no,
                    created_at=n.created_at, created_by_name=names.get(n.created_by), active=n.deactivated_at is None)


@router.get("/loans/{loan_id}/schedule-notes", response_model=list[NoteRead])
def list_notes(loan_id: uuid.UUID, include_inactive: bool = False, db: Session = Depends(get_db),
               current_user: User = Depends(get_current_user)):
    get_accessible_loan(db, loan_id, current_user)
    q = select(ScheduleNote).where(ScheduleNote.loan_id == loan_id, ScheduleNote.is_deleted.is_(False))
    if not include_inactive:
        q = q.where(ScheduleNote.deactivated_at.is_(None))
    rows = db.execute(q.order_by(ScheduleNote.due_date, ScheduleNote.created_at)).scalars().all()
    names = _names(db, [r.created_by for r in rows])
    return [_note_out(r, names) for r in rows]


@router.post("/loans/{loan_id}/schedule-notes", response_model=NoteRead, status_code=201)
def add_note(loan_id: uuid.UUID, body: NoteIn, db: Session = Depends(get_db),
             current_user: User = Depends(require_roles(*WRITE_ROLES))):
    get_accessible_loan(db, loan_id, current_user)
    exists = db.execute(select(RepaymentScheduleRow.id).join(RepaymentScheduleVersion, RepaymentScheduleVersion.id == RepaymentScheduleRow.version_id)
                        .where(RepaymentScheduleVersion.loan_id == loan_id, RepaymentScheduleRow.due_date == body.due_date)).first()
    if not exists:
        raise HTTPException(status_code=422, detail=f"{body.due_date}은(는) 이 차입금의 원리금 테이블에 없는 상환일입니다.")
    cur = db.execute(select(RepaymentScheduleVersion.version_no).where(RepaymentScheduleVersion.loan_id == loan_id,
                                                                       RepaymentScheduleVersion.is_current.is_(True))).scalar()
    n = ScheduleNote(loan_id=loan_id, due_date=body.due_date, note=body.note.strip(), written_on_version_no=cur, created_by=current_user.id)
    db.add(n)
    db.flush()
    record_audit(db, table_name="schedule_note", record_id=n.id, loan_id=loan_id, action="CREATE", user_id=current_user.id,
                 field=str(body.due_date), after=n.note)
    db.commit()
    db.refresh(n)
    return _note_out(n, _names(db, [n.created_by]))


@router.post("/schedule-notes/{note_id}/deactivate", response_model=NoteRead)
def deactivate_note(note_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(require_roles(*WRITE_ROLES))):
    from datetime import timezone
    n = db.get(ScheduleNote, note_id)
    if n is None or n.is_deleted:
        raise HTTPException(status_code=404, detail="메모를 찾을 수 없습니다.")
    get_accessible_loan(db, n.loan_id, current_user)
    if n.deactivated_at is None:
        n.deactivated_at, n.deactivated_by = datetime.now(timezone.utc), current_user.id
        record_audit(db, table_name="schedule_note", record_id=n.id, loan_id=n.loan_id, action="CANCEL", user_id=current_user.id,
                     field=str(n.due_date), before=n.note, after="사용중지")
        db.commit()
        db.refresh(n)
    return _note_out(n, _names(db, [n.created_by]))

