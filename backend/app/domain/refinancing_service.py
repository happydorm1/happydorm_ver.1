"""
대환대출 처리 — 2단계 확정사항 + 첨부6(일부대환)·첨부7(전액대환) 실사례 분석 결과.

[전액대환] 같은 원장을 끊지 않고 이어간다(신규 차입금 ID 발급 없음).
  - 대환일 시점까지의 경과이자를 구금리로 정산하고 '원금상환 0원' 동기화 행을 넣는다(첨부7 확인)
  - 이후 회차는 기존 스케줄(회차·원금)을 100% 승계하되 재원(Loan.fund_id)과 금리만 교체
  - 현재는 원금균등(단리) 원장만 지원. 원리금균등(PPMT) 원장의 재원변경은 업무기준 확인 필요 → 거부

[일부대환] 담당자가 전환금액·잔류금액·재합류일을 직접 입력한다(확정). 첨부6에서 확인된 구조:
  - 대환일 이후 ~ 재합류일 이전 구간의 매 상환일에 대해 두 개의 트랙을 만든다
      · 전환분: 기존 스케줄의 정기상환 원금을 그대로 승계해 상환(신규 재원 금리, 단리)
      · 잔류분: 원금상환 없이 이자만 부과(기존 금리)
  - 재합류일부터는 기존 스케줄 행을 그대로 이어간다(잔류분 잔액 = 기존 스케줄 잔액)
  - 검증: 전환금액 + 잔류금액 = 대환 시점 잔액,  전환금액 = 구간 내 정기상환 원금 합계
    (두 조건이 맞지 않으면 임의로 보정하지 않고 422로 거부 — 업무 확정 전 추정 금지)
"""
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.rate_service import get_applicable_rate
from app.domain.schedule_service import ScheduleGenerationError, _months_per_period
from app.domain.version_utils import copy_row, get_current_version, start_next_version
from app.engine.schedule_engine import calc_simple_interest, days_between, split_at_year_end
from app.models.event import LoanEvent, LoanEventType, Refinancing
from app.models.loan import InterestRateHistory, Loan
from app.models.reference import Code, Fund, InterestMethod
from app.models.schedule import RepaymentScheduleRow, RowType, ScheduleVersionStatus


class RefinancingError(Exception):
    pass


def _is_equal_principal(db: Session, loan: Loan) -> bool:
    fund = db.get(Fund, loan.fund_id)
    method_code = db.get(Code, loan.repay_method_code_id)
    return fund.interest_method == InterestMethod.SIMPLE or bool(method_code and method_code.code == "EQUAL_PRINCIPAL")


def _row(version_id, seq, src_due, start, end, days, inst, bb, principal, interest, ba, rate, rtype, remark, created_by):
    return RepaymentScheduleRow(
        version_id=version_id, seq_no=seq, due_date=src_due, interest_calc_start=start, interest_calc_end=end,
        days=days, installment_no=inst, balance_before=bb, principal_amount=principal, interest_amount=interest,
        unpaid_interest_amount=0, balance_after=ba, applied_rate=rate, row_type=rtype, remark=remark,
        created_by=created_by,
    )


def apply_refinancing(
    db: Session,
    loan_id: uuid.UUID,
    *,
    refi_type: str,
    event_date: date,
    new_fund_id: uuid.UUID,
    new_annual_rate,
    rate_table_name: str | None,
    converted_amount: int | None,
    remaining_amount: int | None,
    rejoin_date: date | None,
    reason: str | None,
    created_by: uuid.UUID | None,
) -> LoanEvent:
    loan = db.get(Loan, loan_id)
    if loan is None or loan.is_deleted:
        raise RefinancingError("차입금을 찾을 수 없습니다.")
    new_fund = db.get(Fund, new_fund_id)
    if new_fund is None or not new_fund.is_active:
        raise RefinancingError("신규 재원을 찾을 수 없습니다.")
    if refi_type not in ("FULL", "PARTIAL"):
        raise RefinancingError("대환 유형은 FULL 또는 PARTIAL이어야 합니다.")
    if not _is_equal_principal(db, loan):
        raise RefinancingError(
            "현재 대환 처리는 원금균등(단리) 원장만 지원합니다. 원리금균등(PPMT) 원장의 대환은 업무기준 확인 후 지원 예정입니다."
        )

    current = get_current_version(db, loan_id)
    old_rows = sorted(current.rows, key=lambda r: r.seq_no)
    if any(r.row_type == RowType.DEFERRAL_RECOVERY for r in old_rows):
        raise RefinancingError("상환유예 회수가 진행 중인 차입금은 대환 재계산을 지원하지 않습니다.")
    try:
        months = _months_per_period(db, loan.repay_cycle_code_id)
    except ScheduleGenerationError as e:
        raise RefinancingError(str(e))

    if refi_type == "FULL":
        return _apply_full(db, loan, current, old_rows, months, event_date, new_fund, new_annual_rate,
                           rate_table_name, reason, created_by)
    return _apply_partial(db, loan, current, old_rows, event_date, new_fund, new_annual_rate,
                          converted_amount, remaining_amount, rejoin_date, reason, created_by)


# ------------------------------------------------------------------ 전액대환
def _apply_full(db, loan, current, old_rows, months, event_date, new_fund, new_rate, rate_table_name, reason, created_by):
    loan_id = loan.id
    prev_rows = [r for r in old_rows if r.due_date <= event_date]
    next_rows = [r for r in old_rows if r.due_date > event_date]
    if not next_rows:
        raise RefinancingError("대환일 이후 남은 상환회차가 없습니다.")

    prev_due = max((r.due_date for r in prev_rows), default=loan.assigned_date)
    balance = prev_rows[-1].balance_after if prev_rows else loan.assigned_amount
    completed = max((r.installment_no or 0) for r in prev_rows) if prev_rows else 0
    next_due = min(r.due_date for r in next_rows)

    # 구금리로 경과이자를 먼저 계산(신금리 이력 등록 전)
    stub_specs = []
    if event_date > prev_due:
        for s, e in split_at_year_end(prev_due, event_date):
            stub_specs.append((s, e, days_between(s, e), float(get_applicable_rate(db, loan_id, e))))

    # 금리이력: 직전 open 구간 종료 + 신금리 구간 추가(7번 요구사항)
    open_rate = db.execute(
        select(InterestRateHistory)
        .where(InterestRateHistory.loan_id == loan_id, InterestRateHistory.effective_end_date.is_(None),
               InterestRateHistory.is_deleted.is_(False))
        .order_by(InterestRateHistory.effective_start_date.desc())
    ).scalars().first()
    if open_rate is not None:
        open_rate.effective_end_date = event_date
    db.add(InterestRateHistory(loan_id=loan_id, effective_start_date=event_date, effective_end_date=None,
                               annual_rate=new_rate, rate_table_name=rate_table_name,
                               rate_basis=f"대환대출(재원변경: {new_fund.fund_name})", created_by=created_by))
    old_fund_id = loan.fund_id
    loan.fund_id = new_fund.id
    db.flush()

    def rate_lookup(as_of_end_date: date) -> float:
        return float(get_applicable_rate(db, loan_id, as_of_end_date))

    new_version = start_next_version(db, current, "대환대출(전액)", created_by)
    seq = 0
    for old in prev_rows:
        seq += 1
        db.add(copy_row(old, new_version.id, seq, created_by))

    for idx, (s, e, days, rate) in enumerate(stub_specs):
        seq += 1
        last = idx == len(stub_specs) - 1
        db.add(_row(new_version.id, seq, event_date, s, e, days, None, balance, 0,
                    calc_simple_interest(balance, days, rate, loan.interest_rounding), balance, rate, RowType.NORMAL,
                    f"대환대출(재원변경) {new_fund.fund_name} — 원금상환 0원(청구주기 동기화)" if last else None,
                    created_by))

    # 대환일 이후 회차: 기존 스케줄의 회차·원금을 그대로 승계하고 이자만 신금리로 재계산
    # (첨부7: 불균등 단계 원금도 그대로 이어짐). 대환일이 기간 중간이면 해당 회차 이자는 대환일부터 새로 센다.
    tail_rows = [r for r in next_rows if r.interest_calc_end > event_date]
    tail_principal = 0
    last_balance_after = balance
    for i, old in enumerate(tail_rows):
        start = max(old.interest_calc_start, event_date)
        days = days_between(start, old.interest_calc_end)
        rate = rate_lookup(old.interest_calc_end)
        interest = calc_simple_interest(old.balance_before, days, rate, loan.interest_rounding)
        remark = old.remark
        if i == 0:
            remark = ((remark + " / ") if remark else "") + f"대환대출 신금리 {float(new_rate)}% 적용({new_fund.fund_name})"
        seq += 1
        db.add(_row(new_version.id, seq, old.due_date, start, old.interest_calc_end, days, old.installment_no,
                    old.balance_before, old.principal_amount, interest, old.balance_after, rate,
                    old.row_type, remark, created_by))
        tail_principal += old.principal_amount
        last_balance_after = old.balance_after

    errors = []
    if sum(r.principal_amount for r in prev_rows) + tail_principal != loan.assigned_amount:
        errors.append("총 원금상환액이 배정금액과 일치하지 않습니다.")
    if last_balance_after != 0:
        errors.append("최종 잔여원금이 0원이 아닙니다.")
    new_version.status = ScheduleVersionStatus.NORMAL if not errors else ScheduleVersionStatus.NEEDS_REVIEW
    new_version.validation_errors = errors or None

    event = LoanEvent(loan_id=loan_id, event_type=LoanEventType.REFINANCING, event_date=event_date,
                      resulting_version_id=new_version.id, reason=reason, created_by=created_by)
    db.add(event)
    db.flush()
    db.add(Refinancing(loan_event_id=event.id, refi_type="FULL", new_fund_id=new_fund.id,
                       converted_amount=balance, remaining_amount=None, rejoin_date=None,
                       new_annual_rate=new_rate, old_fund_id=old_fund_id, created_by=created_by))
    db.commit()
    db.refresh(event)
    return event


# ------------------------------------------------------------------ 일부대환
def _apply_partial(db, loan, current, old_rows, event_date, new_fund, new_rate,
                   converted_amount, remaining_amount, rejoin_date, reason, created_by):
    loan_id = loan.id
    if converted_amount is None or remaining_amount is None or rejoin_date is None:
        raise RefinancingError("일부대환은 전환금액·잔류금액·재합류일을 모두 입력해야 합니다.")
    if converted_amount <= 0 or remaining_amount <= 0:
        raise RefinancingError("전환금액과 잔류금액은 0보다 커야 합니다.")
    if rejoin_date <= event_date:
        raise RefinancingError("재합류일은 대환일보다 늦어야 합니다.")

    window = [r for r in old_rows if r.interest_calc_end > event_date and r.due_date < rejoin_date]
    if not window:
        raise RefinancingError("대환일~재합류일 사이에 해당하는 상환회차가 없습니다.")
    first_idx = old_rows.index(window[0])
    before = old_rows[:first_idx]
    after = [r for r in old_rows if r.due_date >= rejoin_date]
    if len(before) + len(window) + len(after) != len(old_rows):
        raise RefinancingError("대환 구간 계산 중 회차 구성이 일치하지 않습니다. 재합류일을 확인하세요.")
    if any(r.row_type == RowType.DEFERRAL_RECOVERY for r in window):
        raise RefinancingError("구간 내 상환유예 회수 행이 있어 처리할 수 없습니다.")

    balance_at_start = window[0].balance_before
    if converted_amount + remaining_amount != balance_at_start:
        raise RefinancingError(
            f"전환금액({converted_amount:,}) + 잔류금액({remaining_amount:,})이 대환 시점 잔액({balance_at_start:,})과 다릅니다."
        )
    scheduled_principal = sum(r.principal_amount for r in window if r.installment_no is not None)
    if scheduled_principal != converted_amount:
        raise RefinancingError(
            f"전환금액({converted_amount:,})이 대환일~재합류일 구간의 정기상환 원금 합계({scheduled_principal:,})와 다릅니다. "
            "전환분이 구간 내 정기상환 원금을 모두 승계하는 구조여야 하므로, 전환금액 또는 재합류일을 확인하세요."
        )

    new_version = start_next_version(db, current, "대환대출(일부)", created_by)
    seq = 0
    for old in before:
        seq += 1
        db.add(copy_row(old, new_version.id, seq, created_by))

    cb, rb = converted_amount, remaining_amount
    new_rate_f = float(new_rate)
    for i, old in enumerate(window):
        old_rate = float(old.applied_rate)
        # 전환분 트랙: 기존 스케줄 원금 승계(신규 금리)
        principal = old.principal_amount if old.installment_no is not None else 0
        c_interest = calc_simple_interest(cb, old.days, new_rate_f, loan.interest_rounding)
        mark = f"일부대환 전환분({new_fund.fund_name}, {new_rate_f}%)" + (" — 일부 대환 시행" if i == 0 else "")
        seq += 1
        db.add(_row(new_version.id, seq, old.due_date, old.interest_calc_start, old.interest_calc_end, old.days,
                    old.installment_no, cb, principal, c_interest, cb - principal, new_rate_f,
                    RowType.YEAR_END_SPLIT_REAL if old.row_type == RowType.YEAR_END_SPLIT_MEMO else old.row_type,
                    mark, created_by))
        cb -= principal
        # 잔류분 트랙: 원금상환 없이 이자만(구금리)
        r_interest = calc_simple_interest(rb, old.days, old_rate, loan.interest_rounding)
        seq += 1
        db.add(_row(new_version.id, seq, old.due_date, old.interest_calc_start, old.interest_calc_end, old.days,
                    None, rb, 0, r_interest, rb, old_rate, RowType.YEAR_END_SPLIT_REAL,
                    "일부대환 잔류분(이자만 부과)", created_by))

    if cb != 0:
        raise RefinancingError("전환분 잔액이 구간 종료 시점에 0원이 되지 않습니다.")

    for old in after:
        seq += 1
        db.add(copy_row(old, new_version.id, seq, created_by))

    errors = []
    total_principal = sum(r.principal_amount for r in before) + converted_amount + sum(r.principal_amount for r in after)
    if total_principal != loan.assigned_amount:
        errors.append("총 원금상환액이 배정금액과 일치하지 않습니다.")
    if after and after[-1].balance_after != 0:
        errors.append("최종 잔여원금이 0원이 아닙니다.")
    new_version.status = ScheduleVersionStatus.NORMAL if not errors else ScheduleVersionStatus.NEEDS_REVIEW
    new_version.validation_errors = errors or None

    event = LoanEvent(loan_id=loan_id, event_type=LoanEventType.REFINANCING, event_date=event_date,
                      resulting_version_id=new_version.id, reason=reason, created_by=created_by)
    db.add(event)
    db.flush()
    db.add(Refinancing(loan_event_id=event.id, refi_type="PARTIAL", new_fund_id=new_fund.id,
                       converted_amount=converted_amount, remaining_amount=remaining_amount,
                       rejoin_date=rejoin_date, new_annual_rate=new_rate, created_by=created_by))
    db.commit()
    db.refresh(event)
    return event
