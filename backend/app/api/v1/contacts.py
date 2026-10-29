"""Phase 18: 기숙사별 공문 수신처·연락처 관리. 삭제 대신 사용중지(감사로그 남김)."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user, require_roles
from app.domain.audit import record_audit
from app.models.organization import Dormitory, DormitoryContact, User, UserRole

router = APIRouter(tags=["수신처·연락처"])
WRITE_ROLES = (UserRole.SYSTEM_ADMIN, UserRole.FOUNDATION_FUND_MANAGER)
FIELDS = ("name", "organization", "position", "email", "phone", "is_primary", "note")


class ContactIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)
    organization: str | None = Field(default=None, max_length=100)
    position: str | None = Field(default=None, max_length=100)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=30)
    is_primary: bool = False
    note: str | None = Field(default=None, max_length=300)


class ContactRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    dormitory_id: uuid.UUID
    name: str
    organization: str | None
    position: str | None
    email: str | None
    phone: str | None
    is_primary: bool
    note: str | None
    is_active: bool


class DeactivateIn(BaseModel):
    reason: str = Field(..., min_length=1, max_length=300)


def _dormitory(db: Session, dormitory_id: uuid.UUID, user: User) -> Dormitory:
    d = db.get(Dormitory, dormitory_id)
    if d is None or d.is_deleted:
        raise HTTPException(status_code=404, detail="기숙사를 찾을 수 없습니다.")
    if user.role == UserRole.SPC_MANAGER and d.spc_id != user.spc_id:
        raise HTTPException(status_code=403, detail="해당 기숙사에 대한 접근 권한이 없습니다.")
    return d


def _contact(db: Session, contact_id: uuid.UUID) -> DormitoryContact:
    c = db.get(DormitoryContact, contact_id)
    if c is None or c.is_deleted:
        raise HTTPException(status_code=404, detail="연락처를 찾을 수 없습니다.")
    return c


def _only_one_primary(db: Session, c: DormitoryContact) -> None:
    if c.is_primary:
        for o in db.execute(select(DormitoryContact).where(DormitoryContact.dormitory_id == c.dormitory_id,
                                                           DormitoryContact.id != c.id,
                                                           DormitoryContact.is_primary.is_(True))).scalars():
            o.is_primary = False


def active_contacts(db: Session, dormitory_id: uuid.UUID) -> list[DormitoryContact]:
    return list(db.execute(select(DormitoryContact).where(
        DormitoryContact.dormitory_id == dormitory_id, DormitoryContact.is_active.is_(True),
        DormitoryContact.is_deleted.is_(False)).order_by(DormitoryContact.is_primary.desc(), DormitoryContact.created_at)).scalars())


@router.get("/dormitories/{dormitory_id}/contacts", response_model=list[ContactRead])
def list_contacts(dormitory_id: uuid.UUID, include_inactive: bool = False, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    _dormitory(db, dormitory_id, current_user)
    q = select(DormitoryContact).where(DormitoryContact.dormitory_id == dormitory_id, DormitoryContact.is_deleted.is_(False))
    if not include_inactive:
        q = q.where(DormitoryContact.is_active.is_(True))
    return db.execute(q.order_by(DormitoryContact.is_active.desc(), DormitoryContact.is_primary.desc(),
                                 DormitoryContact.created_at)).scalars().all()


@router.post("/dormitories/{dormitory_id}/contacts", response_model=ContactRead, status_code=201)
def create_contact(dormitory_id: uuid.UUID, payload: ContactIn, db: Session = Depends(get_db),
                   current_user: User = Depends(require_roles(*WRITE_ROLES))):
    _dormitory(db, dormitory_id, current_user)
    c = DormitoryContact(dormitory_id=dormitory_id, created_by=current_user.id, **payload.model_dump())
    db.add(c)
    db.flush()
    _only_one_primary(db, c)
    record_audit(db, table_name="dormitory_contact", record_id=c.id, loan_id=None, action="CREATE",
                 user_id=current_user.id, field="name", after=f"{c.name} <{c.email or '-'}>")
    db.commit()
    db.refresh(c)
    return c


@router.put("/contacts/{contact_id}", response_model=ContactRead)
def update_contact(contact_id: uuid.UUID, payload: ContactIn, db: Session = Depends(get_db),
                   current_user: User = Depends(require_roles(*WRITE_ROLES))):
    c = _contact(db, contact_id)
    for f in FIELDS:
        new = getattr(payload, f)
        old = getattr(c, f)
        if new != old:
            record_audit(db, table_name="dormitory_contact", record_id=c.id, loan_id=None, action="UPDATE",
                         user_id=current_user.id, field=f, before=old, after=new)
            setattr(c, f, new)
    c.updated_by = current_user.id
    _only_one_primary(db, c)
    db.commit()
    db.refresh(c)
    return c


@router.post("/contacts/{contact_id}/deactivate", response_model=ContactRead)
def deactivate_contact(contact_id: uuid.UUID, payload: DeactivateIn, db: Session = Depends(get_db),
                       current_user: User = Depends(require_roles(*WRITE_ROLES))):
    c = _contact(db, contact_id)
    if c.is_active:
        c.is_active = False
        c.is_primary = False
        c.updated_by = current_user.id
        record_audit(db, table_name="dormitory_contact", record_id=c.id, loan_id=None, action="CANCEL",
                     user_id=current_user.id, field="is_active", before=True, after=False, reason=payload.reason)
        db.commit()
        db.refresh(c)
    return c
