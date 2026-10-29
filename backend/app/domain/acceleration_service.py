"""
원금가속상환 처리. 첨부4(실사례 테이블)·첨부5(업무절차)에서 확정한 규칙:

1) 시작시점: 접수일 이후 첫 정규 상환예정일(기금별 실제 상환월에 맞춰 자동 매칭)
2) 시작시점부터 매 회차 원금 = ROUND(기존 스케줄 원금 × 가속배율)   (배율은 입력값, 기본 1.5)
3) 시작시점부터 이자는 재원과 무관하게 단리(ROUND(잔여원금×금리×일수/365))로 전환 —
   연도경계 분리행은 공자기금 방식대로 양쪽 모두 실지급
4) 잔여원금이 가속상환액 이하가 되는 회차는 원금 = 잔여원금 전액으로 마감하고
   이후 회차는 삭제(종료 처리)
5) 기존 버전은 보존하고 새 버전(V+1)을 생성
"""
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.engine.schedule_engine import calc_simple_interest, excel_round
from app.domain.version_utils import copy_row, get_current_version, start_next_version
from app.models.event import Acceleration, LoanEvent, LoanEventType
from app.models.loan import Loan
from app.models.schedule import RepaymentScheduleRow, RowType, ScheduleVersionStatus


class AccelerationError(Exception):
    pass


def apply_acceleration(
    db: Session,
    loan_id: uuid.UUID,
    *,
    event_date: date,
    multiplier: Decimal,
    reason: str | None,
    created_by: uuid.UUID | None,
) -> LoanEvent:
    loan = db.get(Loan, loan_id)
    if loan is None or loan.is_deleted:
        raise AccelerationError("차입금을 찾을 수 없습니다.")
    if multiplier <= 0:
        raise AccelerationError("가속배율은 0보다 커야 합니다.")

    current = get_current_version(db, loan_id)
    old_rows = sorted(current.rows, key=lambda r: r.seq_no)

    # 1) 시작 회차: 접수일 이후 첫 '실지급 상환회차'(원금상환 회차번호가 있는 행)
    start_real = next(
        (r for r in old_rows if r.installment_no is not None and r.due_date >= event_date), None
    )
    if start_real is None:
        raise AccelerationError("접수일 이후 적용할 상환회차가 없습니다.")

    first_idx = next(i for i, r in enumerate(old_rows) if r.due_date == start_real.due_date)
    unaffected = old_rows[:first_idx]
    affected = old_rows[first_idx:]

    if any(r.row_type == RowType.DEFERRAL_RECOVERY for r in affected):
        raise AccelerationError("상환유예 회수가 진행 중인 구간에는 원금가속상환을 적용할 수 없습니다.")

    real_rows_affected = [r for r in affected if r.installment_no is not None]
    last_original_installment_no = real_rows_affected[-1].installment_no

    new_version = start_next_version(db, current, "원금가속상환", created_by)

    seq = 0
    for old in unaffected:
        seq += 1
        db.add(copy_row(old, new_version.id, seq, created_by))

    balance = affected[0].balance_before
    closing_installment_no: int | None = None
    new_total_principal = sum(r.principal_amount for r in unaffected)
    first_new_row = True
    closed = False

    for old in affected:
        if closed:
            break

        has_memo_partner = old.row_type == RowType.YEAR_END_SPLIT_MEMO
        interest = calc_simple_interest(balance, old.days, float(old.applied_rate), loan.interest_rounding)

        if old.installment_no is None:
            # 연도경계 앞쪽 분리행 — 가속상환 이후에는 단리방식이라 실지급 행이 된다
            row_type = RowType.YEAR_END_SPLIT_REAL if has_memo_partner else old.row_type
            principal = 0
            remark = old.remark
            balance_after = balance
        else:
            accelerated = excel_round(old.principal_amount * float(multiplier))
            is_split_second = old.row_type in (RowType.YEAR_END_SPLIT_REAL, RowType.CLOSING) and any(
                o.due_date == old.due_date and o.installment_no is None for o in affected
            )
            if balance <= accelerated or old.installment_no == last_original_installment_no:
                principal = balance
                row_type = RowType.CLOSING
                closed = True
                closing_installment_no = old.installment_no
                remark = "원금가속상환 마감(잔액 전액)"
            else:
                principal = accelerated
                row_type = RowType.YEAR_END_SPLIT_REAL if is_split_second else RowType.NORMAL
                remark = old.remark
            balance_after = balance - principal

        if first_new_row:
            remark = ((remark + " / ") if remark else "") + f"원금가속상환 시행(x{multiplier})"
            first_new_row = False

        seq += 1
        db.add(
            RepaymentScheduleRow(
                version_id=new_version.id,
                seq_no=seq,
                due_date=old.due_date,
                interest_calc_start=old.interest_calc_start,
                interest_calc_end=old.interest_calc_end,
                days=old.days,
                installment_no=old.installment_no,
                balance_before=balance,
                principal_amount=principal,
                interest_amount=interest,
                unpaid_interest_amount=0,
                balance_after=balance_after,
                applied_rate=old.applied_rate,
                row_type=row_type,
                memo_interest=None,
                remark=remark,
                created_by=created_by,
            )
        )
        new_total_principal += principal
        balance = balance_after

    errors = []
    if new_total_principal != loan.assigned_amount:
        errors.append(f"총 원금({new_total_principal:,})이 배정금액({loan.assigned_amount:,})과 일치하지 않습니다.")
    if balance != 0:
        errors.append(f"최종 잔여원금이 0원이 아닙니다({balance:,}).")
    new_version.status = ScheduleVersionStatus.NORMAL if not errors else ScheduleVersionStatus.NEEDS_REVIEW
    new_version.validation_errors = errors or None

    event = LoanEvent(
        loan_id=loan_id,
        event_type=LoanEventType.ACCELERATION,
        event_date=event_date,
        resulting_version_id=new_version.id,
        reason=reason,
        created_by=created_by,
    )
    db.add(event)
    db.flush()
    db.add(
        Acceleration(
            loan_event_id=event.id,
            start_installment_no=start_real.installment_no,
            start_due_date=start_real.due_date,
            acceleration_multiplier=multiplier,
            closing_installment_no=closing_installment_no,
            original_total_installments=last_original_installment_no,
            created_by=created_by,
        )
    )
    db.commit()
    db.refresh(event)
    return event
