"""Phase 9: 버전·변경이력 관리 — 이벤트 이력, 이벤트 취소, 버전 비교, 변경이력(AuditLog) 조회."""
import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.v1.access import get_accessible_loan
from app.core.database import get_db
from app.core.security import get_current_user, require_roles
from app.domain.event_cancel_service import EventCancelError, cancel_event
from app.domain.schedule_amounts import VersionNotFoundError, compare_versions
from app.models.audit import AuditLog
from app.models.event import Acceleration, Deferral, EarlyRepayment, LoanEvent, LoanEventType, Refinancing
from app.models.organization import User, UserRole
from app.models.schedule import RepaymentScheduleVersion
from app.schemas.schedule import RepaymentScheduleVersionRead

router = APIRouter(prefix="/loans/{loan_id}", tags=["버전·변경이력"])

WRITE_ROLES = (UserRole.SYSTEM_ADMIN, UserRole.FOUNDATION_FUND_MANAGER)
HISTORY_ROLES = (UserRole.SYSTEM_ADMIN, UserRole.FOUNDATION_FUND_MANAGER, UserRole.FOUNDATION_APPROVER)


class LoanEventSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    event_type: LoanEventType
    event_date: date
    status: str
    reason: str | None
    created_at: datetime
    created_by: uuid.UUID | None
    resulting_version_no: int | None = None
    detail: dict | None = None
    created_by_name: str | None = None


class CancelRequest(BaseModel):
    reason: str = Field(..., min_length=2, max_length=500, description="취소 사유(필수 — 변경이력에 기록)")


class AuditLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    table_name: str
    record_id: uuid.UUID
    action: str
    changed_field: str | None
    before_value: str | None
    after_value: str | None
    changed_by: uuid.UUID | None
    changed_at: datetime
    change_reason: str | None
    changed_by_name: str | None = None


def _user_names(db: Session, ids) -> dict:
    ids = {i for i in ids if i}
    return {u.id: u.name for u in db.execute(select(User).where(User.id.in_(ids))).scalars()} if ids else {}


def _detail(db: Session, ev: LoanEvent) -> dict | None:
    if ev.event_type == LoanEventType.DEFERRAL:
        d = db.execute(select(Deferral).where(Deferral.loan_event_id == ev.id)).scalar_one_or_none()
        return None if d is None else {
            "defer_target": d.defer_target.value, "frozen_principal_total": d.frozen_principal_total,
            "frozen_interest_total": d.frozen_interest_total, "recovery_cycle_months": d.recovery_cycle_months,
            "recovery_count": d.recovery_count, "recovery_start_date": d.recovery_start_date.isoformat()}
    if ev.event_type == LoanEventType.ACCELERATION:
        a = db.execute(select(Acceleration).where(Acceleration.loan_event_id == ev.id)).scalar_one_or_none()
        return None if a is None else {
            "acceleration_multiplier": float(a.acceleration_multiplier), "start_installment_no": a.start_installment_no,
            "start_due_date": a.start_due_date.isoformat(), "closing_installment_no": a.closing_installment_no}
    if ev.event_type == LoanEventType.EARLY_REPAYMENT:
        e = db.execute(select(EarlyRepayment).where(EarlyRepayment.loan_event_id == ev.id)).scalar_one_or_none()
        return None if e is None else {
            "repay_type": e.repay_type, "repay_amount": e.repay_amount, "fee_amount": e.fee_amount,
            "balance_before": e.balance_before, "balance_after": e.balance_after}
    if ev.event_type == LoanEventType.REFINANCING:
        r = db.execute(select(Refinancing).where(Refinancing.loan_event_id == ev.id)).scalar_one_or_none()
        return None if r is None else {
            "refi_type": r.refi_type, "converted_amount": r.converted_amount, "remaining_amount": r.remaining_amount,
            "rejoin_date": r.rejoin_date.isoformat() if r.rejoin_date else None, "new_annual_rate": float(r.new_annual_rate)}
    return None


@router.get("/events", response_model=list[LoanEventSummary])
def list_events(loan_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """이 차입금에 발생한 모든 이벤트(취소 포함) 타임라인 — 26번 '적용 이벤트' 추적."""
    get_accessible_loan(db, loan_id, current_user)
    events = db.execute(
        select(LoanEvent).where(LoanEvent.loan_id == loan_id).order_by(LoanEvent.created_at, LoanEvent.id)
    ).scalars().all()
    version_nos = {
        v.id: v.version_no
        for v in db.execute(select(RepaymentScheduleVersion).where(RepaymentScheduleVersion.loan_id == loan_id)).scalars()
    }
    names = _user_names(db, [e.created_by for e in events])
    out = []
    for ev in events:
        item = LoanEventSummary.model_validate(ev)
        item.created_by_name = names.get(ev.created_by)
        item.status = ev.status.value
        item.resulting_version_no = version_nos.get(ev.resulting_version_id)
        item.detail = _detail(db, ev)
        out.append(item)
    return out


@router.post("/events/{event_id}/cancel", response_model=RepaymentScheduleVersionRead)
def cancel_loan_event(
    loan_id: uuid.UUID, event_id: uuid.UUID, payload: CancelRequest,
    db: Session = Depends(get_db), current_user: User = Depends(require_roles(*WRITE_ROLES)),
):
    """이벤트 취소(삭제 아님). 가장 최근 적용 이벤트만 가능하며, 직전 버전을 복원한 새 버전을 만든다."""
    get_accessible_loan(db, loan_id, current_user)
    try:
        version = cancel_event(db, loan_id, event_id, reason=payload.reason, user_id=current_user.id)
    except EventCancelError as e:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(e))
    return db.execute(
        select(RepaymentScheduleVersion).options(selectinload(RepaymentScheduleVersion.rows))
        .where(RepaymentScheduleVersion.id == version.id)
    ).scalar_one()


@router.get("/schedule/compare")
def compare_schedule_versions(
    loan_id: uuid.UUID, from_version: int, to_version: int,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    """§37-9 '버전간 계산차이 확인': 상환예정일별로 달라진 원금·이자·합계와 총계 차이."""
    get_accessible_loan(db, loan_id, current_user)
    try:
        return compare_versions(db, loan_id, from_version, to_version)
    except VersionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/audit-logs", response_model=list[AuditLogRead])
def list_audit_logs(
    loan_id: uuid.UUID, table_name: str | None = None, limit: int = 200,
    db: Session = Depends(get_db), current_user: User = Depends(require_roles(*HISTORY_ROLES)),
):
    """§42 변경이력: 누가·언제·무엇을·변경 전/후·사유."""
    get_accessible_loan(db, loan_id, current_user)
    stmt = select(AuditLog).where(AuditLog.loan_id == loan_id)
    if table_name:
        stmt = stmt.where(AuditLog.table_name == table_name)
    stmt = stmt.order_by(AuditLog.changed_at.desc()).limit(min(max(limit, 1), 1000))
    rows = db.execute(stmt).scalars().all()
    names = _user_names(db, [r.changed_by for r in rows])
    out = []
    for r in rows:
        item = AuditLogRead.model_validate(r)
        item.changed_by_name = names.get(r.changed_by)
        out.append(item)
    return out


# ---------- 전체 이벤트 이력(이벤트 관리 화면) ----------
from app.domain.loan_filters import LoanFilters, filtered_loans  # noqa: E402

events_router = APIRouter(prefix="/events", tags=["버전·변경이력"])


class EventListItem(LoanEventSummary):
    loan_id: uuid.UUID
    loan_code: str | None = None
    dormitory_name: str | None = None
    cancellable: bool = False      # 그 차입금의 가장 최근 적용 이벤트만 취소 가능(지난 이벤트는 뒤에서부터)


@events_router.get("", response_model=list[EventListItem])
def list_all_events(event_type: LoanEventType | None = None, status: str | None = None, loan_id: uuid.UUID | None = None,
                    date_from: date | None = None, date_to: date | None = None, limit: int = 200,
                    db: Session = Depends(get_db), current_user: User = Depends(require_roles(*HISTORY_ROLES))):
    """모든 차입금의 중도변수 이벤트(최근 등록 순). 권한 범위 안의 차입금만."""
    rows = filtered_loans(db, current_user, LoanFilters())
    loans = {loan.id: (loan, dorm) for loan, dorm, _ in rows}
    if loan_id:
        loans = {k: v for k, v in loans.items() if k == loan_id}
    if not loans:
        return []
    q = select(LoanEvent).where(LoanEvent.loan_id.in_(list(loans)))
    if event_type:
        q = q.where(LoanEvent.event_type == event_type)
    if status:
        q = q.where(LoanEvent.status == status)
    if date_from:
        q = q.where(LoanEvent.event_date >= date_from)
    if date_to:
        q = q.where(LoanEvent.event_date <= date_to)
    events = db.execute(q.order_by(LoanEvent.created_at.desc()).limit(min(max(limit, 1), 1000))).scalars().all()
    latest = {}
    for ev in db.execute(select(LoanEvent).where(LoanEvent.loan_id.in_({e.loan_id for e in events}),
                                                 LoanEvent.status == "APPLIED").order_by(LoanEvent.created_at)).scalars():
        latest[ev.loan_id] = ev.id
    vnos = {v.id: v.version_no for v in db.execute(select(RepaymentScheduleVersion).where(
        RepaymentScheduleVersion.loan_id.in_({e.loan_id for e in events}))).scalars()}
    names = _user_names(db, [e.created_by for e in events])
    out = []
    for ev in events:
        loan, dorm = loans[ev.loan_id]
        item = EventListItem.model_validate({**LoanEventSummary.model_validate(ev).model_dump(), "loan_id": ev.loan_id})
        item.status = ev.status.value
        item.loan_code, item.dormitory_name = loan.loan_code, dorm.dormitory_name
        item.created_by_name = names.get(ev.created_by)
        item.resulting_version_no = vnos.get(ev.resulting_version_id)
        item.detail = _detail(db, ev)
        item.cancellable = latest.get(ev.loan_id) == ev.id
        out.append(item)
    return out
