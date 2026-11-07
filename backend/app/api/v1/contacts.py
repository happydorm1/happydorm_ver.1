"""Phase 18: 기숙사별 공문 수신처·연락처 관리. 삭제 대신 사용중지(감사로그 남김)."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user, require_roles
from app.core.crypto import decrypt_str, encrypt_str, mask_account
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
    """(Phase 23 이후 사용 안 함) 기본 수신처는 여러 명 지정 가능 — 메일 작성 때 받는 사람을 체크해 고른다."""
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
    # 기본 수신처는 여러 명 가능(Phase 23) — 다른 사람 표시를 해제하지 않는다
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
    # 기본 수신처는 여러 명 가능(Phase 23) — 다른 사람 표시를 해제하지 않는다
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



# ---------- Phase 21: 기숙사별 입금계좌 ----------
class AccountIn(BaseModel):
    bank_account: str | None = Field(default=None, max_length=120)   # 비우면 공통 계좌(공문 양식) 사용
    account_holder: str | None = Field(default=None, max_length=100)


@router.get("/dormitories/{dormitory_id}/account")
def get_account(dormitory_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    d = _dormitory(db, dormitory_id, user)
    full = user.role in WRITE_ROLES
    acc = (decrypt_str(d.bank_account) if full else mask_account(d.bank_account)) if d.bank_account else None
    return {"dormitory_id": d.id, "bank_account": acc, "account_holder": d.account_holder,
            "uses_common": not (d.bank_account and d.account_holder)}


@router.put("/dormitories/{dormitory_id}/account")
def put_account(dormitory_id: uuid.UUID, body: AccountIn, db: Session = Depends(get_db),
                user: User = Depends(require_roles(*WRITE_ROLES))):
    d = _dormitory(db, dormitory_id, user)
    acc = (body.bank_account or "").strip() or None
    holder = (body.account_holder or "").strip() or None
    if bool(acc) != bool(holder):
        raise HTTPException(status_code=422, detail="계좌번호와 예금주를 함께 입력하세요(둘 다 비우면 공통 계좌를 씁니다).")
    before = mask_account(d.bank_account) if d.bank_account else None
    d.bank_account, d.account_holder = (encrypt_str(acc) if acc else None), holder
    d.updated_by = user.id
    record_audit(db, table_name="dormitory", record_id=d.id, loan_id=None, action="UPDATE", user_id=user.id,
                 field="bank_account", before=before, after=mask_account(d.bank_account) if acc else None,
                 reason="기숙사 입금계좌 변경")
    db.commit()
    return get_account(dormitory_id, db, user)
