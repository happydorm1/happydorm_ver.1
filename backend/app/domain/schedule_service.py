"""
원리금 테이블 생성 서비스.
engine/schedule_engine.py(순수 계산)과 DB(Loan/InterestRateHistory/Code)를 연결한다.
"""
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.rate_service import get_applicable_rate
from app.engine import schedule_engine as engine
from app.models.loan import Loan
from app.models.reference import Code, Fund, InterestMethod
from app.models.schedule import (
    RepaymentScheduleRow,
    RepaymentScheduleVersion,
    RowType,
    ScheduleVersionStatus,
)


class ScheduleGenerationError(Exception):
    pass


def _months_per_period(db: Session, repay_cycle_code_id: uuid.UUID) -> int:
    code = db.get(Code, repay_cycle_code_id)
    if code is None or not code.extra_value or "periods_per_year" not in code.extra_value:
        raise ScheduleGenerationError("상환주기 코드에 periods_per_year 정보가 없습니다.")
    return 12 // code.extra_value["periods_per_year"]


def generate_initial_schedule(
    db: Session, loan_id: uuid.UUID, *, generated_by: uuid.UUID | None = None
) -> RepaymentScheduleVersion:
    """최초 상환계획(V1) 생성. 이미 버전이 있으면 에러 — 재계산은 이벤트(Phase 6~8)를 통해서만."""
    loan = db.get(Loan, loan_id)
    if loan is None or loan.is_deleted:
        raise ScheduleGenerationError("차입금을 찾을 수 없습니다.")

    existing = db.execute(
        select(RepaymentScheduleVersion).where(RepaymentScheduleVersion.loan_id == loan_id)
    ).first()
    if existing:
        raise ScheduleGenerationError("이미 생성된 원리금 테이블이 있습니다.")

    fund = db.get(Fund, loan.fund_id)
    method_code = db.get(Code, loan.repay_method_code_id)
    months_per_period = _months_per_period(db, loan.repay_cycle_code_id)

    def rate_lookup(as_of_end_date: date) -> float:
        return float(get_applicable_rate(db, loan_id, as_of_end_date))

    rows: list[engine.ScheduleRow] = []

    # 거치기간
    if loan.grace_start_date and loan.grace_end_date:
        grace_rows = engine.generate_grace_period_rows(
            assigned_amount=loan.assigned_amount,
            start_date=loan.grace_start_date,
            grace_end_date=loan.grace_end_date,
            months_per_period=months_per_period,
            rate_lookup=rate_lookup,
            rounding=loan.interest_rounding,
        )
        rows += grace_rows
        # 상환기간은 '마지막 거치 이자 납입일'부터 이어진다. 거치종료일이 납입일 사이(예: 12/31)에 있어도
        # 그 사이 기간이 빠지지 않게 한다(검증된 원본 사례는 거치종료일=마지막 납입일이라 결과 동일).
        period_start_date = grace_rows[-1].due_date if grace_rows else loan.grace_start_date
    else:
        period_start_date = loan.assigned_date

    total_installments = round(loan.repay_period_months / months_per_period)

    if fund.interest_method == InterestMethod.SIMPLE or (method_code and method_code.code == "EQUAL_PRINCIPAL"):
        rows += engine.generate_equal_principal_schedule(
            balance_start=loan.assigned_amount,
            assigned_amount=loan.assigned_amount,
            first_due_date=loan.principal_repay_start_date,
            total_installments=total_installments,
            months_per_period=months_per_period,
            rate_lookup=rate_lookup,
            period_start_date=period_start_date,
            rounding=loan.interest_rounding,
        )
    else:
        rows += engine.generate_equal_payment_schedule(
            balance_start=loan.assigned_amount,
            first_due_date=loan.principal_repay_start_date,
            total_installments=total_installments,
            months_per_period=months_per_period,
            rate_lookup=rate_lookup,
            period_start_date=period_start_date,
        )

    validation = engine.validate_schedule(rows, loan.assigned_amount)

    version = RepaymentScheduleVersion(
        loan_id=loan_id,
        version_no=1,
        generated_reason="최초생성",
        is_current=True,
        status=ScheduleVersionStatus.NORMAL if validation.is_valid else ScheduleVersionStatus.NEEDS_REVIEW,
        validation_errors=validation.errors or None,
        created_by=generated_by,
    )
    db.add(version)
    db.flush()

    for seq, r in enumerate(rows, start=1):
        db.add(
            RepaymentScheduleRow(
                version_id=version.id,
                seq_no=seq,
                due_date=r.due_date,
                interest_calc_start=r.interest_calc_start,
                interest_calc_end=r.interest_calc_end,
                days=r.days,
                installment_no=r.installment_no,
                balance_before=r.balance_before,
                principal_amount=r.principal_amount,
                interest_amount=r.interest_amount,
                unpaid_interest_amount=0,
                balance_after=r.balance_after,
                applied_rate=r.applied_rate,
                row_type=RowType(r.row_type),
                memo_interest=r.memo_interest,
                remark=r.remark,
                created_by=generated_by,
            )
        )

    db.commit()
    db.refresh(version)
    return version
