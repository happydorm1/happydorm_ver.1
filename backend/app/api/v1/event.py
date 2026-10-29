import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.v1.event_requests import submit_request
from app.core.config import settings
from app.core.database import get_db
from app.core.security import require_roles
from app.domain.acceleration_service import AccelerationError, apply_acceleration
from app.domain.deferral_service import DeferralError, apply_deferral
from app.domain.early_repayment_service import EarlyRepaymentError, apply_early_repayment
from app.domain.rate_change_service import RateChangeError, apply_rate_change
from app.domain.refinancing_service import RefinancingError, apply_refinancing
from app.models.event import Deferral, LoanEvent
from app.models.organization import User, UserRole
from app.models.schedule import RepaymentScheduleVersion
from app.schemas.event import (
    AccelerationCreate,
    DeferralApplyResult,
    DeferralCreate,
    EarlyRepaymentCreate,
    RateChangeCreate,
    RefinancingCreate,
)
from app.schemas.schedule import RepaymentScheduleVersionRead

router = APIRouter(prefix="/loans/{loan_id}/events", tags=["이벤트(상환유예 등)"])

WRITE_ROLES = (UserRole.SYSTEM_ADMIN, UserRole.FOUNDATION_FUND_MANAGER)


@router.post("/deferral", response_model=DeferralApplyResult, status_code=201)
def create_deferral(
    loan_id: uuid.UUID,
    payload: DeferralCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*WRITE_ROLES)),
):
    if settings.EVENT_APPROVAL_REQUIRED:   # 등록·승인 분리 모드: 기안만 만들고 202 반환
        return submit_request(db, loan_id, "DEFERRAL", payload, current_user)
    try:
        event = apply_deferral(
            db,
            loan_id,
            event_date=payload.event_date,
            defer_target=payload.defer_target,
            recovery_cycle_months=payload.recovery_cycle_months,
            recovery_count=payload.recovery_count,
            reason=payload.reason,
            created_by=current_user.id,
        )
    except DeferralError as e:
        raise HTTPException(status_code=422, detail=str(e))

    event_full = db.execute(
        select(LoanEvent)
        .options(selectinload(LoanEvent.deferral).selectinload(Deferral.recovery_rows))
        .where(LoanEvent.id == event.id)
    ).scalar_one()

    version = db.execute(
        select(RepaymentScheduleVersion)
        .options(selectinload(RepaymentScheduleVersion.rows))
        .where(RepaymentScheduleVersion.id == event.resulting_version_id)
    ).scalar_one()

    return DeferralApplyResult(event=event_full, resulting_schedule=version)


@router.post("/rate-change", response_model=RepaymentScheduleVersionRead, status_code=201)
def create_rate_change(
    loan_id: uuid.UUID,
    payload: RateChangeCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*WRITE_ROLES)),
):
    """
    Phase 7: 금리변동. 영향받는 회차부터만 재계산하고 새 버전을 생성한다.
    (현재는 원금균등/단리 방식만 지원 — 주택도시기금 PPMT/IPMT는 고정금리라 대상 아님)
    """
    if settings.EVENT_APPROVAL_REQUIRED:   # 등록·승인 분리 모드: 기안만 만들고 202 반환
        return submit_request(db, loan_id, "RATE_CHANGE", payload, current_user)
    try:
        version = apply_rate_change(
            db,
            loan_id,
            effective_start_date=payload.effective_start_date,
            annual_rate=payload.annual_rate,
            rate_table_name=payload.rate_table_name,
            rate_basis=payload.rate_basis,
            created_by=current_user.id,
        )
    except RateChangeError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return db.execute(
        select(RepaymentScheduleVersion)
        .options(selectinload(RepaymentScheduleVersion.rows))
        .where(RepaymentScheduleVersion.id == version.id)
    ).scalar_one()


@router.post("/acceleration", response_model=RepaymentScheduleVersionRead, status_code=201)
def create_acceleration(
    loan_id: uuid.UUID,
    payload: AccelerationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*WRITE_ROLES)),
):
    """원금가속상환: 접수일 이후 첫 정규회차부터 원금 x 가속배율(기본 1.5), 이자는 단리로 전환."""
    if settings.EVENT_APPROVAL_REQUIRED:   # 등록·승인 분리 모드: 기안만 만들고 202 반환
        return submit_request(db, loan_id, "ACCELERATION", payload, current_user)
    try:
        event = apply_acceleration(
            db, loan_id,
            event_date=payload.event_date,
            multiplier=payload.acceleration_multiplier,
            reason=payload.reason,
            created_by=current_user.id,
        )
    except AccelerationError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return db.execute(
        select(RepaymentScheduleVersion)
        .options(selectinload(RepaymentScheduleVersion.rows))
        .where(RepaymentScheduleVersion.id == event.resulting_version_id)
    ).scalar_one()


@router.post("/early-repayment", response_model=RepaymentScheduleVersionRead, status_code=201)
def create_early_repayment(
    loan_id: uuid.UUID,
    payload: EarlyRepaymentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*WRITE_ROLES)),
):
    """조기상환(일부/전액): 일부는 상환기간 유지·정기상환액 재계산, 전액은 이후 회차 종료."""
    if settings.EVENT_APPROVAL_REQUIRED:   # 등록·승인 분리 모드: 기안만 만들고 202 반환
        return submit_request(db, loan_id, "EARLY_REPAYMENT", payload, current_user)
    try:
        event = apply_early_repayment(
            db, loan_id,
            event_date=payload.event_date,
            repay_type=payload.repay_type,
            repay_amount=payload.repay_amount,
            fee_amount=payload.fee_amount,
            reason=payload.reason,
            created_by=current_user.id,
        )
    except EarlyRepaymentError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return db.execute(
        select(RepaymentScheduleVersion)
        .options(selectinload(RepaymentScheduleVersion.rows))
        .where(RepaymentScheduleVersion.id == event.resulting_version_id)
    ).scalar_one()


@router.post("/refinancing", response_model=RepaymentScheduleVersionRead, status_code=201)
def create_refinancing(
    loan_id: uuid.UUID,
    payload: RefinancingCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*WRITE_ROLES)),
):
    """대환대출. FULL: 같은 원장 승계(재원·금리 교체). PARTIAL: 전환/잔류금액·재합류일은 담당자 입력."""
    if settings.EVENT_APPROVAL_REQUIRED:   # 등록·승인 분리 모드: 기안만 만들고 202 반환
        return submit_request(db, loan_id, "REFINANCING", payload, current_user)
    try:
        event = apply_refinancing(
            db, loan_id,
            refi_type=payload.refi_type,
            event_date=payload.event_date,
            new_fund_id=payload.new_fund_id,
            new_annual_rate=payload.new_annual_rate,
            rate_table_name=payload.rate_table_name,
            converted_amount=payload.converted_amount,
            remaining_amount=payload.remaining_amount,
            rejoin_date=payload.rejoin_date,
            reason=payload.reason,
            created_by=current_user.id,
        )
    except RefinancingError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return db.execute(
        select(RepaymentScheduleVersion)
        .options(selectinload(RepaymentScheduleVersion.rows))
        .where(RepaymentScheduleVersion.id == event.resulting_version_id)
    ).scalar_one()
