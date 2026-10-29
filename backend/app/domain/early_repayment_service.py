"""
조기상환(일부/전액) 처리 — 2단계에서 확정한 규칙:
- 일부 조기상환: 상환기간(남은 회차수)은 유지하고 정기상환액을 새 잔여원금 기준으로 재계산
    · 원금균등: 새 잔여원금 ÷ 남은 회차수 (마지막 회차 자동보정)
    · 원리금균등(PPMT/IPMT): 남은 회차수 유지, 새 잔여원금을 pv로 회차 index 1부터 재계산
- 전액 조기상환: 예정 상환회차는 모두 종료 처리
- 조기상환일까지의 경과이자는 직전 상환일~조기상환일 일수만큼 단리로 정산(연도경계는 분리)
- 수수료는 건별 입력값(기본 0) — 일정표에는 반영하지 않고 이벤트 레코드/비고에만 기록
- 기존 버전은 보존, 새 버전(V+1) 생성

이 서비스의 기준(조기상환일 직전 상환일부터 조기상환일까지 경과이자 정산 등)은 업무 확정 전의
기본 구현이므로, 실제 사례 자료를 받으면 대조검증하여 보정한다.
"""
import uuid
from datetime import date

from sqlalchemy.orm import Session

from app.domain.rate_service import get_applicable_rate
from app.domain.schedule_service import ScheduleGenerationError, _months_per_period
from app.domain.version_utils import copy_row, get_current_version, start_next_version
from app.engine import schedule_engine as engine
from app.engine.schedule_engine import calc_simple_interest, days_between, split_at_year_end
from app.models.event import EarlyRepayment, LoanEvent, LoanEventType
from app.models.loan import Loan
from app.models.reference import Code, Fund, InterestMethod
from app.models.schedule import RepaymentScheduleRow, RowType, ScheduleVersionStatus


class EarlyRepaymentError(Exception):
    pass


def apply_early_repayment(
    db: Session,
    loan_id: uuid.UUID,
    *,
    event_date: date,
    repay_type: str,  # PARTIAL / FULL
    repay_amount: int | None,
    fee_amount: int,
    reason: str | None,
    created_by: uuid.UUID | None,
) -> LoanEvent:
    loan = db.get(Loan, loan_id)
    if loan is None or loan.is_deleted:
        raise EarlyRepaymentError("차입금을 찾을 수 없습니다.")
    if repay_type not in ("PARTIAL", "FULL"):
        raise EarlyRepaymentError("조기상환 유형은 PARTIAL 또는 FULL이어야 합니다.")

    current = get_current_version(db, loan_id)
    old_rows = sorted(current.rows, key=lambda r: r.seq_no)
    if any(r.row_type == RowType.DEFERRAL_RECOVERY for r in old_rows):
        raise EarlyRepaymentError("상환유예 회수가 진행 중인 차입금은 조기상환 재계산을 지원하지 않습니다.")

    try:
        months = _months_per_period(db, loan.repay_cycle_code_id)
    except ScheduleGenerationError as e:
        raise EarlyRepaymentError(str(e))

    # 1) 조기상환 시점의 직전 정산 상태
    prev_rows = [r for r in old_rows if r.due_date <= event_date]
    next_rows = [r for r in old_rows if r.due_date > event_date]
    if not next_rows:
        raise EarlyRepaymentError("조기상환일 이후 남은 상환회차가 없습니다.")

    if prev_rows:
        prev_due = max(r.due_date for r in prev_rows)
        balance = prev_rows[-1].balance_after
        completed_installments = max((r.installment_no or 0) for r in prev_rows)
    else:
        prev_due = loan.assigned_date
        balance = loan.assigned_amount
        completed_installments = 0

    if balance <= 0:
        raise EarlyRepaymentError("이미 상환이 완료된 차입금입니다.")

    if repay_type == "FULL":
        repay_amount = balance
    else:
        if repay_amount is None or repay_amount <= 0:
            raise EarlyRepaymentError("일부 조기상환은 상환금액이 필요합니다.")
        if repay_amount >= balance:
            raise EarlyRepaymentError("상환금액이 잔여원금 이상입니다. 전액 조기상환(FULL)으로 처리하세요.")

    new_balance = balance - repay_amount
    next_due = min(r.due_date for r in next_rows)
    remaining_installments = len({r.due_date for r in next_rows if r.installment_no is not None})

    fund = db.get(Fund, loan.fund_id)
    method_code = db.get(Code, loan.repay_method_code_id)
    is_equal_principal = fund.interest_method == InterestMethod.SIMPLE or (
        method_code and method_code.code == "EQUAL_PRINCIPAL"
    )

    def rate_lookup(as_of_end_date: date) -> float:
        return float(get_applicable_rate(db, loan_id, as_of_end_date))

    new_version = start_next_version(db, current, "조기상환" if repay_type == "PARTIAL" else "전액조기상환", created_by)

    seq = 0
    for old in prev_rows:
        seq += 1
        db.add(copy_row(old, new_version.id, seq, created_by))

    # 2) 조기상환일까지 경과이자 정산행(연도경계가 있으면 분리) + 조기상환 원금
    stub_periods = split_at_year_end(prev_due, event_date) if event_date > prev_due else [(event_date, event_date)]
    for idx, (s, e) in enumerate(stub_periods):
        is_last = idx == len(stub_periods) - 1
        days = days_between(s, e)
        rate = rate_lookup(e if e > s else s)
        interest = calc_simple_interest(balance, days, rate, loan.interest_rounding)
        seq += 1
        remark = None
        if is_last:
            fee_text = f" (수수료 {fee_amount:,}원)" if fee_amount else ""
            remark = ("전액 조기상환" if repay_type == "FULL" else "일부 조기상환") + fee_text
        db.add(
            RepaymentScheduleRow(
                version_id=new_version.id, seq_no=seq, due_date=event_date,
                interest_calc_start=s, interest_calc_end=e, days=days, installment_no=None,
                balance_before=balance, principal_amount=repay_amount if is_last else 0,
                interest_amount=interest, unpaid_interest_amount=0,
                balance_after=new_balance if is_last else balance,
                applied_rate=rate, row_type=RowType.NORMAL, remark=remark, created_by=created_by,
            )
        )

    # 3) 일부 조기상환: 남은 회차수 유지, 정기상환액 재계산
    tail_principal_total = 0
    final_balance = new_balance
    if repay_type == "PARTIAL":
        if is_equal_principal:
            tail = engine.generate_equal_principal_schedule(
                balance_start=new_balance, assigned_amount=new_balance, first_due_date=next_due,
                total_installments=remaining_installments, months_per_period=months,
                rate_lookup=rate_lookup, period_start_date=event_date, rounding=loan.interest_rounding,
            )
        else:
            tail = engine.generate_equal_payment_schedule(
                balance_start=new_balance, first_due_date=next_due,
                total_installments=remaining_installments, months_per_period=months,
                rate_lookup=rate_lookup, period_start_date=event_date,
            )
        first = True
        for r in tail:
            seq += 1
            installment_no = (r.installment_no + completed_installments) if r.installment_no else None
            remark = r.remark
            if first:
                remark = ((remark + " / ") if remark else "") + "정기상환액 재계산(조기상환 반영)"
                first = False
            db.add(
                RepaymentScheduleRow(
                    version_id=new_version.id, seq_no=seq, due_date=r.due_date,
                    interest_calc_start=r.interest_calc_start, interest_calc_end=r.interest_calc_end,
                    days=r.days, installment_no=installment_no, balance_before=r.balance_before,
                    principal_amount=r.principal_amount, interest_amount=r.interest_amount,
                    unpaid_interest_amount=0, balance_after=r.balance_after, applied_rate=r.applied_rate,
                    row_type=RowType(r.row_type), memo_interest=r.memo_interest, remark=remark,
                    created_by=created_by,
                )
            )
            tail_principal_total += r.principal_amount
        final_balance = tail[-1].balance_after if tail else new_balance

    # 4) 검증
    prev_principal = sum(r.principal_amount for r in prev_rows)
    errors = []
    if prev_principal + repay_amount + tail_principal_total != loan.assigned_amount:
        errors.append("총 원금상환액이 배정금액과 일치하지 않습니다.")
    if final_balance != 0 and repay_type == "PARTIAL":
        errors.append(f"최종 잔여원금이 0원이 아닙니다({final_balance:,}).")
    new_version.status = ScheduleVersionStatus.NORMAL if not errors else ScheduleVersionStatus.NEEDS_REVIEW
    new_version.validation_errors = errors or None

    event = LoanEvent(
        loan_id=loan_id, event_type=LoanEventType.EARLY_REPAYMENT, event_date=event_date,
        resulting_version_id=new_version.id, reason=reason, created_by=created_by,
    )
    db.add(event)
    db.flush()
    db.add(
        EarlyRepayment(
            loan_event_id=event.id, repay_type=repay_type, repay_amount=repay_amount,
            fee_amount=fee_amount, balance_before=balance, balance_after=new_balance,
            applied_after_installment_no=completed_installments or None, created_by=created_by,
        )
    )
    db.commit()
    db.refresh(event)
    return event
