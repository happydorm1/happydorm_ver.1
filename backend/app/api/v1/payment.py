import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.access import get_accessible_loan
from app.core.database import get_db
from app.core.security import get_current_user, require_roles
from app.domain.payment_service import PaymentError, cancel_payment, payment_summary, record_payment
from app.models.loan import Loan
from app.models.organization import Dormitory, User, UserRole
from app.models.payment import ActualPayment, PaymentStatus

router = APIRouter(tags=["실제납부"])

WRITE_ROLES = (UserRole.SYSTEM_ADMIN, UserRole.FOUNDATION_FUND_MANAGER)


class PaymentCreate(BaseModel):
    due_date: date = Field(..., description="납부 대상 상환예정일(원리금 테이블의 상환예정일)")
    actual_paid_date: date
    actual_principal: int = Field(default=0, ge=0)
    actual_interest: int = Field(default=0, ge=0)
    note: str | None = Field(default=None, max_length=300)


class PaymentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    loan_id: uuid.UUID
    due_date: date
    actual_paid_date: date
    actual_principal: int
    actual_interest: int
    actual_total: int
    status: str
    remark: str | None
    cancel_reason: str | None
    created_by: uuid.UUID | None
    created_at: datetime


class PaymentCancel(BaseModel):
    reason: str = Field(..., min_length=2, max_length=500)


def _read(p: ActualPayment) -> PaymentRead:
    out = PaymentRead.model_validate(p)
    out.status = p.status.value
    return out


@router.post("/loans/{loan_id}/payments", response_model=PaymentRead, status_code=201)
def create_payment(
    loan_id: uuid.UUID, payload: PaymentCreate,
    db: Session = Depends(get_db), current_user: User = Depends(require_roles(*WRITE_ROLES)),
):
    loan = get_accessible_loan(db, loan_id, current_user)
    try:
        p = record_payment(db, loan, due_date=payload.due_date, actual_paid_date=payload.actual_paid_date,
                           actual_principal=payload.actual_principal, actual_interest=payload.actual_interest,
                           note=payload.note, user_id=current_user.id)
    except PaymentError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return _read(p)


@router.get("/loans/{loan_id}/payments", response_model=list[PaymentRead])
def list_payments(loan_id: uuid.UUID, include_cancelled: bool = False, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    get_accessible_loan(db, loan_id, current_user)
    stmt = select(ActualPayment).where(ActualPayment.loan_id == loan_id)
    if not include_cancelled:
        stmt = stmt.where(ActualPayment.status == PaymentStatus.ACTIVE)
    return [_read(p) for p in db.execute(stmt.order_by(ActualPayment.due_date, ActualPayment.created_at)).scalars()]


@router.post("/loans/{loan_id}/payments/{payment_id}/cancel", response_model=PaymentRead)
def cancel_loan_payment(
    loan_id: uuid.UUID, payment_id: uuid.UUID, payload: PaymentCancel,
    db: Session = Depends(get_db), current_user: User = Depends(require_roles(*WRITE_ROLES)),
):
    get_accessible_loan(db, loan_id, current_user)
    try:
        p = cancel_payment(db, loan_id, payment_id, reason=payload.reason, user_id=current_user.id)
    except PaymentError as e:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(e))
    return _read(p)


@router.get("/loans/{loan_id}/payments/summary")
def get_payment_summary(loan_id: uuid.UUID, as_of: date | None = None, db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_user)):
    """예정액 vs 실제 납부액 비교(상환예정일별), 미납·일부납부·초과납부 상태와 회수율."""
    loan = get_accessible_loan(db, loan_id, current_user)
    try:
        return payment_summary(db, loan, as_of or date.today())
    except PaymentError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/payments/overdue")
def list_overdue(as_of: date | None = None, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """전체 차입금의 미납/일부납부(납부기한 경과) 목록 — 대시보드 '미납건수' 및 '미납' 알림용(§34~35)."""
    as_of = as_of or date.today()
    stmt = select(Loan, Dormitory).join(Dormitory, Dormitory.id == Loan.dormitory_id).where(Loan.is_deleted.is_(False))
    if current_user.role == UserRole.SPC_MANAGER:
        if current_user.spc_id is None:
            return {"as_of": as_of, "overdue_count": 0, "overdue_amount": 0, "items": []}
        stmt = stmt.where(Dormitory.spc_id == current_user.spc_id)

    items = []
    for loan, dorm in db.execute(stmt).all():
        try:
            summary = payment_summary(db, loan, as_of)
        except PaymentError:
            continue  # 원리금 테이블이 아직 없는 차입금
        for r in summary["rows"]:
            if r["is_overdue"]:
                items.append({"loan_id": loan.id, "loan_code": loan.loan_code, "dormitory_name": dorm.dormitory_name,
                              "due_date": r["due_date"], "scheduled_total": r["scheduled_total"],
                              "paid_total": r["paid_total"], "outstanding": r["outstanding"], "status": r["status"]})
    items.sort(key=lambda x: (x["due_date"], x["loan_code"]))
    return {"as_of": as_of, "overdue_count": len(items), "overdue_amount": sum(i["outstanding"] for i in items), "items": items}
