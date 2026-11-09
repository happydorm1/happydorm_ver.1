"""Phase 23: 입금 확인·승인 API — 조회는 재단 직원, 처리는 재단 자금담당자·시스템관리자(SPC 담당자 제외)."""
import uuid
from datetime import date, datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import require_roles
from app.domain import deposit_service as svc
from app.models.document import GeneratedDocument
from app.models.organization import Dormitory, User, UserRole
from app.models.payment import BankDeposit, DepositStatus

router = APIRouter(prefix="/deposits", tags=["입금 확인·승인"])
READ = require_roles(UserRole.SYSTEM_ADMIN, UserRole.FOUNDATION_FUND_MANAGER, UserRole.FOUNDATION_APPROVER, UserRole.VIEWER)
WRITE = require_roles(UserRole.SYSTEM_ADMIN, UserRole.FOUNDATION_FUND_MANAGER)
MAX_BYTES = 10 * 1024 * 1024


class DepositIn(BaseModel):
    deposit_date: date
    depositor: str = Field(..., min_length=1, max_length=100)
    amount: int = Field(..., gt=0)
    bank_memo: str | None = Field(default=None, max_length=200)


class NoteIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class ReasonIn(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


class AssignIn(BaseModel):
    notice_id: uuid.UUID | None = None


class IdentIn(BaseModel):
    virtual_account: str | None = Field(default=None, max_length=40)
    customer_key: str | None = Field(default=None, max_length=50)
    payer_names: list[str] = Field(default_factory=list, max_length=10)


def _notice_dict(db: Session, doc: GeneratedDocument | None) -> dict | None:
    if doc is None:
        return None
    dorm = db.get(Dormitory, doc.dormitory_id)
    t = doc.generated_data["totals"]
    return {"id": str(doc.id), "title": doc.title, "dormitory_id": str(doc.dormitory_id),
            "dormitory_name": dorm.dormitory_name if dorm else "", "due_date": doc.due_date.isoformat(),
            "payment_deadline": doc.payment_deadline.isoformat(), "total": t["total"], "principal": t["principal"],
            "interest": t["interest"], "is_deleted": doc.is_deleted,
            # 수신 여부는 표시만 — 대사·자동 승인 조건이 아니다(메일·출력본으로 받고 시스템 열람 없이 입금하는 경우가 있어서)
            "received_at": doc.received_at.isoformat() if doc.received_at else None}


def _out(db: Session, d: BankDeposit) -> dict:
    who = db.get(User, d.decided_by) if d.decided_by else None
    dorm = db.get(Dormitory, d.dormitory_id) if d.dormitory_id else None
    group = svc._members(db, d) if d.group_id else []
    return {
        "customer_name": d.customer_name, "customer_key": d.customer_key, "virtual_account": svc.mask_account(d.virtual_account),
        "dormitory": {"id": str(dorm.id), "name": dorm.dormitory_name} if dorm else None,
        "group": {"id": str(d.group_id), "count": len(group), "total": sum(m.amount for m in group),
                  "amounts": [m.amount for m in group]} if d.group_id else None,
        "id": str(d.id), "deposit_date": d.deposit_date.isoformat(), "deposit_time": d.deposit_time, "depositor": d.depositor, "amount": d.amount,
        "bank_memo": d.bank_memo, "source": d.source, "file_name": d.file_name, "status": d.status.value,
        "hold_reasons": [{"code": c, "label": svc.REASONS.get(c, c)} for c in (d.hold_reasons or [])],
        "auto_approved": d.auto_approved, "manual_done": d.manual_done,
        "decided_by_name": "시스템(자동)" if d.auto_approved else (who.name if who else None),
        "decided_at": d.decided_at.isoformat() if d.decided_at else None, "decision_note": d.decision_note,
        "payment_count": len(d.payment_ids or []), "created_at": d.created_at.isoformat(),
        "notice": _notice_dict(db, db.get(GeneratedDocument, d.notice_id) if d.notice_id else None),
    }


def _wrap(fn, *a, **k):
    try:
        return fn(*a, **k)
    except svc.DepositError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("")
def list_deposits(status: DepositStatus | None = None, date_from: date | None = None, date_to: date | None = None,
                  db: Session = Depends(get_db), user: User = Depends(READ)):
    q = select(BankDeposit).where(BankDeposit.is_deleted.is_(False))
    if status:
        q = q.where(BankDeposit.status == status)
    if date_from:
        q = q.where(BankDeposit.deposit_date >= date_from)
    if date_to:
        q = q.where(BankDeposit.deposit_date <= date_to)
    items = db.execute(q.order_by(BankDeposit.deposit_date.desc(), BankDeposit.created_at.desc()).limit(500)).scalars().all()
    counts = dict(db.execute(select(BankDeposit.status, func.count()).where(BankDeposit.is_deleted.is_(False))
                             .group_by(BankDeposit.status)).all())
    return {"items": [_out(db, d) for d in items], "counts": {s.value: counts.get(s, 0) for s in DepositStatus},
            "auto_approve_limit": svc.AUTO_APPROVE_LIMIT}


@router.get("/dorm-idents")
def dorm_idents(db: Session = Depends(get_db), user: User = Depends(READ)):
    """기숙사별 입금 식별정보 — 은행 가상계좌(CMS)의 가상계좌번호·고객KEY·입금인명."""
    from app.core.crypto import decrypt_str
    from app.models.organization import SPC
    dorms = db.execute(select(Dormitory).where(Dormitory.is_deleted.is_(False)).order_by(Dormitory.dormitory_name)).scalars().all()
    out = []
    for d in dorms:
        spc = db.get(SPC, d.spc_id) if d.spc_id else None
        out.append({"id": str(d.id), "dormitory_name": d.dormitory_name, "school_name": d.school_name,
                    "spc_name": spc.spc_name if spc else None,
                    "virtual_account": decrypt_str(d.bank_virtual_account) if user.role in (UserRole.SYSTEM_ADMIN, UserRole.FOUNDATION_FUND_MANAGER) else svc.mask_account(d.bank_virtual_account),
                    "virtual_account_masked": svc.mask_account(d.bank_virtual_account),
                    "customer_key": d.bank_customer_key, "payer_names": d.bank_payer_names or []})
    return out


@router.put("/dorm-idents/{dormitory_id}")
def put_dorm_ident(dormitory_id: uuid.UUID, payload: IdentIn, db: Session = Depends(get_db), user: User = Depends(WRITE)):
    from app.core.crypto import encrypt_str
    from app.domain.audit import record_audit
    d = db.get(Dormitory, dormitory_id)
    if d is None or d.is_deleted:
        raise HTTPException(status_code=404, detail="기숙사를 찾을 수 없습니다.")
    va = (payload.virtual_account or "").strip()
    ck = (payload.customer_key or "").strip()
    names = [n.strip() for n in payload.payer_names if n and n.strip()]
    # 다른 기숙사와 겹치면 어느 기숙사 입금인지 알 수 없으므로 막는다
    from app.core.crypto import decrypt_str
    for o in db.execute(select(Dormitory).where(Dormitory.is_deleted.is_(False), Dormitory.id != d.id)).scalars():
        if va and o.bank_virtual_account and svc._acct(decrypt_str(o.bank_virtual_account)) == svc._acct(va):
            raise HTTPException(status_code=422, detail=f"가상계좌번호가 '{o.dormitory_name}'에 이미 등록돼 있습니다.")
        if ck and o.bank_customer_key == ck:
            raise HTTPException(status_code=422, detail=f"고객KEY가 '{o.dormitory_name}'에 이미 등록돼 있습니다.")
    d.bank_virtual_account = encrypt_str(va) if va else None
    d.bank_customer_key = ck or None
    d.bank_payer_names = names or None
    record_audit(db, table_name="dormitory", record_id=d.id, loan_id=None, action="UPDATE", user_id=user.id,
                 field="bank_ident", after=f"고객KEY {ck or '-'} · 입금인명 {', '.join(names) or '-'} · 가상계좌 {'등록' if va else '-'}")
    db.commit()
    return {"ok": True}


@router.get("/template")
def template(user: User = Depends(READ)):
    return Response(content=svc.build_template(),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote('입금내역_양식.xlsx')}"})


@router.post("/upload")
async def upload(file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(WRITE)):
    content = await file.read()
    if len(content) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="파일이 너무 큽니다(10MB 이하).")
    name = file.filename or "입금내역.xlsx"
    rows, errors = _wrap(svc.read_rows, content, name)
    res = svc.add_deposits(db, rows, source="UPLOAD", file_name=name, user_id=user.id)
    res["errors"] = errors + res["errors"]
    res["read"] = len(rows)
    return res


@router.post("", status_code=201)
def create(payload: DepositIn, db: Session = Depends(get_db), user: User = Depends(WRITE)):
    res = svc.add_deposits(db, [payload.model_dump()], source="MANUAL", file_name=None, user_id=user.id)
    if res["errors"]:
        raise HTTPException(status_code=422, detail=res["errors"][0]["message"])
    if res["duplicates"]:
        raise HTTPException(status_code=409, detail="같은 입금(입금일·입금자·금액·적요)이 이미 등록되어 있습니다.")
    return res


@router.get("/{deposit_id}/notice-options")
def notice_options(deposit_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(WRITE)):
    return [_notice_dict(db, d) for d in _wrap(svc.notice_options, db, deposit_id)]


@router.post("/{deposit_id}/assign")
def assign(deposit_id: uuid.UUID, payload: AssignIn, db: Session = Depends(get_db), user: User = Depends(WRITE)):
    return _out(db, _wrap(svc.assign, db, deposit_id, payload.notice_id, user.id))


@router.post("/{deposit_id}/approve")
def approve(deposit_id: uuid.UUID, payload: NoteIn, db: Session = Depends(get_db), user: User = Depends(WRITE)):
    return _out(db, _wrap(svc.approve, db, deposit_id, user.id, payload.note))


@router.post("/{deposit_id}/manual-complete")
def manual_complete(deposit_id: uuid.UUID, payload: ReasonIn, db: Session = Depends(get_db), user: User = Depends(WRITE)):
    return _out(db, _wrap(svc.complete_manual, db, deposit_id, user.id, payload.reason))


@router.post("/{deposit_id}/exclude")
def exclude(deposit_id: uuid.UUID, payload: ReasonIn, db: Session = Depends(get_db), user: User = Depends(WRITE)):
    return _out(db, _wrap(svc.exclude, db, deposit_id, user.id, payload.reason))


@router.post("/{deposit_id}/revert")
def revert(deposit_id: uuid.UUID, payload: ReasonIn, db: Session = Depends(get_db), user: User = Depends(WRITE)):
    return _out(db, _wrap(svc.revert, db, deposit_id, user.id, payload.reason))
