"""
금리변동(Phase 7) 처리.
공공자금관리기금처럼 변동금리 적용 중 금리가 바뀌는 경우를 다룬다.
- 1단계에서 실데이터로 검증한 규칙: 새 금리는 이자계산종료일이 적용시작일 이후인
  회차부터 전체 적용(일할안분 없음)
- 과거(이미 지난) 회차는 절대 건드리지 않고, 영향받는 회차부터만 재계산
- 기존 버전은 보존, 새 버전(V+1) 생성

현재는 원금균등(단리) 방식만 지원한다 — 문서상 주택도시기금(PPMT/IPMT)은 고정금리이므로
금리변동 이벤트 대상이 아니다(§9).
"""
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.domain.rate_service import get_applicable_rate
from app.engine import schedule_engine as engine
from app.models.event import LoanEvent, LoanEventType
from app.models.loan import InterestRateHistory, Loan
from app.models.reference import Code, Fund, InterestMethod
from app.models.schedule import (
    RepaymentScheduleRow,
    RepaymentScheduleVersion,
    RowType,
    ScheduleVersionStatus,
)


class RateChangeError(Exception):
    pass


def apply_rate_change(
    db: Session,
    loan_id: uuid.UUID,
    *,
    effective_start_date: date,
    annual_rate,
    rate_table_name: str | None,
    rate_basis: str | None,
    created_by: uuid.UUID | None,
) -> RepaymentScheduleVersion:
    loan = db.get(Loan, loan_id)
    if loan is None or loan.is_deleted:
        raise RateChangeError("차입금을 찾을 수 없습니다.")

    fund = db.get(Fund, loan.fund_id)
    method_code = db.get(Code, loan.repay_method_code_id)
    is_equal_principal = fund.interest_method == InterestMethod.SIMPLE or (
        method_code and method_code.code == "EQUAL_PRINCIPAL"
    )
    if not is_equal_principal:
        raise RateChangeError(
            "현재 금리변동 재계산은 원금균등(단리) 방식만 지원합니다. "
            "이 차입금은 원리금균등(PPMT/IPMT) 방식입니다."
        )

    current_version = db.execute(
        select(RepaymentScheduleVersion)
        .options(selectinload(RepaymentScheduleVersion.rows))
        .where(RepaymentScheduleVersion.loan_id == loan_id, RepaymentScheduleVersion.is_current.is_(True))
    ).scalar_one_or_none()
    if current_version is None:
        raise RateChangeError("원리금 테이블이 없습니다. 먼저 스케줄을 생성하세요.")

    old_rows = sorted(current_version.rows, key=lambda r: r.seq_no)

    # 0) 담당자가 같은 적용시작일의 금리를 '금리이력'으로 먼저 등록해 둔 경우(알림 안내 순서) — 중복 이력이 생기지 않도록
    #    그 이력은 이 이벤트가 만드는 이력으로 대체한다(물리삭제 대신 사용중지).
    for same in db.execute(
        select(InterestRateHistory).where(InterestRateHistory.loan_id == loan_id, InterestRateHistory.is_deleted.is_(False),
                                          InterestRateHistory.effective_start_date == effective_start_date)
    ).scalars():
        same.is_deleted = True
    db.flush()

    # 1) 금리이력 갱신: 직전 open 구간을 닫고 새 구간 추가(7번 요구사항 — 이력관리)
    open_rate = db.execute(
        select(InterestRateHistory)
        .where(InterestRateHistory.loan_id == loan_id, InterestRateHistory.effective_end_date.is_(None),
               InterestRateHistory.is_deleted.is_(False))
        .order_by(InterestRateHistory.effective_start_date.desc())
    ).scalars().first()
    if open_rate is not None:
        open_rate.effective_end_date = effective_start_date

    db.add(
        InterestRateHistory(
            loan_id=loan_id,
            effective_start_date=effective_start_date,
            effective_end_date=None,
            annual_rate=annual_rate,
            rate_basis=rate_basis,
            rate_table_name=rate_table_name,
            created_by=created_by,
        )
    )
    db.flush()

    def rate_lookup(as_of_end_date: date) -> float:
        return float(get_applicable_rate(db, loan_id, as_of_end_date))

    # 2) 영향받는 첫 회차(=이자계산종료일이 적용시작일 이후인 첫 회차) 탐색
    cutover_idx = next(
        (i for i, r in enumerate(old_rows) if r.interest_calc_end >= effective_start_date), None
    )
    if cutover_idx is None:
        raise RateChangeError("적용시작일이 기존 스케줄 범위를 벗어납니다(이미 상환이 종료된 차입금일 수 있습니다).")

    unaffected_rows = old_rows[:cutover_idx]
    cutover_row = old_rows[cutover_idx]

    months_per_period = 12 // _periods_per_year(db, loan.repay_cycle_code_id)
    total_installments = round(loan.repay_period_months / months_per_period)

    new_rows_data: list[engine.ScheduleRow] = []
    if cutover_row.installment_no is None:
        # 아직 거치기간 — 커트오버 지점부터 거치기간 잔여분 + 원금상환 전체를 재생성
        new_rows_data += engine.generate_grace_period_rows(
            assigned_amount=loan.assigned_amount,
            start_date=cutover_row.interest_calc_start,
            grace_end_date=loan.grace_end_date,
            months_per_period=months_per_period,
            rate_lookup=rate_lookup,
            rounding=loan.interest_rounding,
        )
        new_rows_data += engine.generate_equal_principal_schedule(
            balance_start=loan.assigned_amount,
            assigned_amount=loan.assigned_amount,
            first_due_date=loan.principal_repay_start_date,
            total_installments=total_installments,
            months_per_period=months_per_period,
            rate_lookup=rate_lookup,
            period_start_date=loan.grace_end_date,
            rounding=loan.interest_rounding,
        )
    else:
        # 상환 진행 중 — 커트오버 회차부터 잔여 회차를 재생성(원금은 원래 금액 유지)
        new_rows_data += engine.generate_equal_principal_schedule(
            balance_start=cutover_row.balance_before,
            assigned_amount=loan.assigned_amount,
            first_due_date=cutover_row.due_date,
            total_installments=total_installments,
            months_per_period=months_per_period,
            rate_lookup=rate_lookup,
            period_start_date=cutover_row.interest_calc_start,
            start_installment_no=cutover_row.installment_no - 1,
            rounding=loan.interest_rounding,
        )

    # 3) 새 버전 생성: 과거 불변 구간 + 재계산된 구간
    next_version_no = current_version.version_no + 1
    new_version = RepaymentScheduleVersion(
        loan_id=loan_id,
        version_no=next_version_no,
        generated_reason="금리변동",
        previous_version_id=current_version.id,
        is_current=True,
        status=ScheduleVersionStatus.NORMAL,
        created_by=created_by,
    )
    db.add(new_version)
    current_version.is_current = False
    db.flush()

    for seq, old in enumerate(unaffected_rows, start=1):
        db.add(
            RepaymentScheduleRow(
                version_id=new_version.id, seq_no=seq, due_date=old.due_date,
                interest_calc_start=old.interest_calc_start, interest_calc_end=old.interest_calc_end,
                days=old.days, installment_no=old.installment_no, balance_before=old.balance_before,
                principal_amount=old.principal_amount, interest_amount=old.interest_amount,
                unpaid_interest_amount=old.unpaid_interest_amount, balance_after=old.balance_after,
                applied_rate=old.applied_rate, row_type=old.row_type, memo_interest=old.memo_interest,
                remark=old.remark, created_by=created_by,
            )
        )

    seq = len(unaffected_rows)
    for r in new_rows_data:
        seq += 1
        remark = r.remark
        if r is new_rows_data[0]:
            remark = ((remark + " / ") if remark else "") + "금리변동"
        db.add(
            RepaymentScheduleRow(
                version_id=new_version.id, seq_no=seq, due_date=r.due_date,
                interest_calc_start=r.interest_calc_start, interest_calc_end=r.interest_calc_end,
                days=r.days, installment_no=r.installment_no, balance_before=r.balance_before,
                principal_amount=r.principal_amount, interest_amount=r.interest_amount,
                unpaid_interest_amount=0, balance_after=r.balance_after, applied_rate=r.applied_rate,
                row_type=RowType(r.row_type), memo_interest=r.memo_interest, remark=remark,
                created_by=created_by,
            )
        )

    # 4) 검증
    total_principal = sum(r.principal_amount for r in unaffected_rows) + sum(
        r.principal_amount for r in new_rows_data
    )
    errors = []
    if total_principal != loan.assigned_amount:
        errors.append(f"총 원금상환액({total_principal:,})이 배정금액({loan.assigned_amount:,})과 일치하지 않습니다.")
    final_balance = new_rows_data[-1].balance_after if new_rows_data else unaffected_rows[-1].balance_after
    if final_balance != 0:
        errors.append(f"최종 잔여원금이 0원이 아닙니다({final_balance:,}).")

    new_version.status = ScheduleVersionStatus.NORMAL if not errors else ScheduleVersionStatus.NEEDS_REVIEW
    new_version.validation_errors = errors or None

    db.add(LoanEvent(loan_id=loan_id, event_type=LoanEventType.RATE_CHANGE, event_date=effective_start_date,
                     resulting_version_id=new_version.id,
                     reason=f"금리변동 {float(annual_rate)}%" + (f" ({rate_basis})" if rate_basis else ""),
                     created_by=created_by))
    db.commit()
    db.refresh(new_version)
    return new_version


def _periods_per_year(db: Session, repay_cycle_code_id: uuid.UUID) -> int:
    code = db.get(Code, repay_cycle_code_id)
    if code is None or not code.extra_value:
        raise RateChangeError("상환주기 코드 정보를 찾을 수 없습니다.")
    return code.extra_value["periods_per_year"]
