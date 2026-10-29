import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.database import get_db
from app.api.v1.access import get_accessible_loan
from app.core.security import get_current_user, require_roles
from app.domain.audit import record_audit
from app.models.schedule import RepaymentScheduleVersion
from app.domain.rate_service import RateNotRegisteredError, get_applicable_rate
from app.models.loan import InterestRateHistory, Loan
from app.models.organization import Dormitory, User, UserRole
from app.schemas.loan import (
    InterestRateHistoryCreate,
    InterestRateHistoryRead,
    LoanCreate,
    LoanRead,
    LoanUpdate,
)

router = APIRouter(prefix="/loans", tags=["차입금"])

WRITE_ROLES = (UserRole.SYSTEM_ADMIN, UserRole.FOUNDATION_FUND_MANAGER)
CONTRACT_FIELDS = {"assigned_amount", "grace_start_date", "grace_end_date", "principal_repay_start_date", "repay_period_months"}


def _loan_query():
    return select(Loan).options(selectinload(Loan.rate_history))


@router.get("", response_model=list[LoanRead])
def list_loans(
    dormitory_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    stmt = _loan_query().where(Loan.is_deleted.is_(False))

    if current_user.role == UserRole.SPC_MANAGER:
        if current_user.spc_id is None:
            return []
        stmt = stmt.join(Dormitory, Dormitory.id == Loan.dormitory_id).where(
            Dormitory.spc_id == current_user.spc_id
        )

    if dormitory_id:
        stmt = stmt.where(Loan.dormitory_id == dormitory_id)

    return db.execute(stmt).scalars().unique().all()


@router.post("", response_model=LoanRead, status_code=201)
def create_loan(
    payload: LoanCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*WRITE_ROLES)),
):
    dormitory = db.get(Dormitory, payload.dormitory_id)
    if dormitory is None or dormitory.is_deleted:
        raise HTTPException(status_code=404, detail="기숙사를 찾을 수 없습니다.")

    existing = db.execute(select(Loan).where(Loan.loan_code == payload.loan_code)).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="이미 등록된 차입금 코드입니다.")

    data = payload.model_dump(exclude={"initial_rate"})
    loan = Loan(**data, created_by=current_user.id)
    db.add(loan)
    db.flush()  # loan.id 확보

    rate = InterestRateHistory(
        loan_id=loan.id,
        **payload.initial_rate.model_dump(),
        created_by=current_user.id,
    )
    db.add(rate)
    db.commit()
    db.refresh(loan)
    return db.execute(_loan_query().where(Loan.id == loan.id)).scalar_one()


@router.get("/{loan_id}", response_model=LoanRead)
def get_loan(
    loan_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    loan = db.execute(_loan_query().where(Loan.id == loan_id)).scalar_one_or_none()
    if loan is None or loan.is_deleted:
        raise HTTPException(status_code=404, detail="차입금을 찾을 수 없습니다.")

    if current_user.role == UserRole.SPC_MANAGER:
        dormitory = db.get(Dormitory, loan.dormitory_id)
        if dormitory is None or dormitory.spc_id != current_user.spc_id:
            raise HTTPException(status_code=403, detail="해당 차입금에 대한 접근 권한이 없습니다.")

    return loan


@router.put("/{loan_id}", response_model=LoanRead)
def update_loan(
    loan_id: uuid.UUID,
    payload: LoanUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*WRITE_ROLES)),
):
    loan = db.get(Loan, loan_id)
    if loan is None or loan.is_deleted:
        raise HTTPException(status_code=404, detail="차입금을 찾을 수 없습니다.")

    changes = payload.model_dump(exclude_unset=True, exclude={"change_reason"})
    contract_fields = set(changes) & CONTRACT_FIELDS
    if contract_fields:
        # §50: 기존 데이터를 임의로 덮어쓰지 않는다 — 원리금 테이블이 이미 있으면 계약조건은 이벤트로만 변경
        has_schedule = db.execute(
            select(RepaymentScheduleVersion.id).where(RepaymentScheduleVersion.loan_id == loan_id)
        ).first()
        if has_schedule:
            raise HTTPException(
                status_code=422,
                detail=f"원리금 테이블이 이미 생성된 차입금의 계약조건({', '.join(sorted(contract_fields))})은 "
                       "직접 수정할 수 없습니다. 조기상환·유예·금리변동 등 이벤트로 처리하세요.",
            )

    for field, value in changes.items():
        before = getattr(loan, field)
        if before == value:
            continue
        record_audit(db, table_name="loan", record_id=loan.id, loan_id=loan.id, action="UPDATE",
                     user_id=current_user.id, field=field, before=before, after=value, reason=payload.change_reason)
        setattr(loan, field, value)
    loan.updated_by = current_user.id
    db.commit()
    return db.execute(_loan_query().where(Loan.id == loan_id)).scalar_one()


@router.post("/{loan_id}/rate-history", response_model=InterestRateHistoryRead, status_code=201)
def add_rate_history(
    loan_id: uuid.UUID,
    payload: InterestRateHistoryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*WRITE_ROLES)),
):
    """
    7번 요구사항: 금리는 '현재금리 1개'가 아니라 이력으로 관리.
    (실제 금리변동 이벤트에 따른 버전 생성은 Phase 7에서 LOAN_EVENT와 함께 구현 예정 —
    여기서는 기준정보로서의 이력 추가만 제공)
    """
    loan = db.get(Loan, loan_id)
    if loan is None or loan.is_deleted:
        raise HTTPException(status_code=404, detail="차입금을 찾을 수 없습니다.")

    rate = InterestRateHistory(loan_id=loan_id, **payload.model_dump(), created_by=current_user.id)
    db.add(rate)
    db.commit()
    db.refresh(rate)
    return rate


@router.get("/{loan_id}/applicable-rate")
def get_rate_as_of(
    loan_id: uuid.UUID,
    as_of_end_date: date,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    1단계에서 실데이터로 검증한 규칙 그대로: 회차의 이자계산종료일 기준 적용금리 조회.
    Phase 3 계산엔진이 회차별로 호출하게 될 핵심 조회 API.
    """
    get_accessible_loan(db, loan_id, current_user)
    try:
        rate = get_applicable_rate(db, loan_id, as_of_end_date)
    except RateNotRegisteredError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"loan_id": loan_id, "as_of_end_date": as_of_end_date, "annual_rate": rate}
