"""
상환유예(Deferral) 처리.
2단계에서 첨부3 실데이터로 역산·검증한 알고리즘을 그대로 구현한다:

1) 접수시점과 일치(이후 가장 가까운)하는 정규회차 1건을 동결(원금/원금+이자/이자 중 선택)
2) 정규 스케줄의 다른 회차는 전혀 영향받지 않음(금액 불변)
3) 동결일 + 1년부터, 입력된 주기(분기/반기)·횟수로 분할 회수 — 정규 회차 금액에 "가산"
4) 회수 중인 잔여 동결원금에 매 회차 별도 단리이자 부과
5) 마지막 회수회차에서 원단위 자동보정
6) 기존 스케줄은 삭제하지 않고 새 버전(V+1)으로 저장
"""
import uuid
from datetime import date

from dateutil.relativedelta import relativedelta
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.domain.rate_service import get_applicable_rate
from app.engine.schedule_engine import add_months_eom, calc_simple_interest, days_between, excel_round
from app.models.event import Deferral, DeferralRecoveryRow, DeferTarget, LoanEvent, LoanEventType
from app.models.loan import Loan
from app.models.schedule import RepaymentScheduleRow, RepaymentScheduleVersion, RowType, ScheduleVersionStatus


class DeferralError(Exception):
    pass


def _get_current_version_with_rows(db: Session, loan_id: uuid.UUID) -> RepaymentScheduleVersion:
    version = db.execute(
        select(RepaymentScheduleVersion)
        .options(selectinload(RepaymentScheduleVersion.rows))
        .where(RepaymentScheduleVersion.loan_id == loan_id, RepaymentScheduleVersion.is_current.is_(True))
    ).scalar_one_or_none()
    if version is None:
        raise DeferralError("원리금 테이블이 없습니다. 먼저 스케줄을 생성하세요.")
    return version


def _split_evenly_with_last_correction(total: int, count: int) -> list[int]:
    """ROUND(총액/횟수)를 반복하고, 마지막 회차에서 누적오차를 자동보정(1단계에서 확정한 원칙)."""
    base = excel_round(total / count)
    amounts = [base] * count
    amounts[-1] = total - base * (count - 1)
    return amounts


def apply_deferral(
    db: Session,
    loan_id: uuid.UUID,
    *,
    event_date: date,
    defer_target: DeferTarget,
    recovery_cycle_months: int,
    recovery_count: int,
    reason: str | None,
    created_by: uuid.UUID | None,
) -> LoanEvent:
    loan = db.get(Loan, loan_id)
    if loan is None or loan.is_deleted:
        raise DeferralError("차입금을 찾을 수 없습니다.")

    current_version = _get_current_version_with_rows(db, loan_id)
    old_rows = sorted(current_version.rows, key=lambda r: r.seq_no)

    # 1) 동결 대상 회차 탐색: 접수시점 이후 가장 가까운 '실지급' 회차(메모행 제외)
    real_rows = [r for r in old_rows if r.row_type != RowType.YEAR_END_SPLIT_MEMO]
    first_real = next((r for r in real_rows if r.due_date >= event_date), None)
    if first_real is None:
        raise DeferralError("접수시점 이후 동결할 회차를 찾을 수 없습니다.")

    # 동결 단위 = 그 상환예정일의 '실지급 행 전체'. 단리(원금균등) 원장은 연도경계(12/31)가 걸리면 같은 상환일에
    # 이자 전용 행과 원금 포함 행이 모두 실지급이므로, 첫 행만 잡으면 원금이 동결되지 않는다.
    frozen_group = [r for r in real_rows if r.due_date == first_real.due_date]
    frozen_row = max(frozen_group, key=lambda r: r.seq_no)  # 원금이 실리는 마지막 행을 대표로 기록

    freeze_principal = defer_target in (DeferTarget.PRINCIPAL, DeferTarget.PRINCIPAL_INTEREST)
    freeze_interest = defer_target in (DeferTarget.INTEREST, DeferTarget.PRINCIPAL_INTEREST)
    frozen_principal_total = sum(r.principal_amount for r in frozen_group) if freeze_principal else 0
    frozen_interest_total = sum(r.interest_amount for r in frozen_group) if freeze_interest else 0

    if frozen_principal_total == 0 and frozen_interest_total == 0:
        raise DeferralError("동결 대상 금액이 0원입니다 — 해당 회차에 상환할 금액이 없습니다.")

    # 2) 새 버전 생성(기존 버전은 보존 — 26/43번 원칙)
    next_version_no = current_version.version_no + 1
    new_version = RepaymentScheduleVersion(
        loan_id=loan_id,
        version_no=next_version_no,
        generated_reason="상환유예",
        previous_version_id=current_version.id,
        is_current=True,
        status=ScheduleVersionStatus.NORMAL,
        created_by=created_by,
    )
    db.add(new_version)
    current_version.is_current = False
    db.flush()

    old_to_new: dict[uuid.UUID, RepaymentScheduleRow] = {}
    for old in old_rows:
        new_row = RepaymentScheduleRow(
            version_id=new_version.id,
            seq_no=old.seq_no,
            due_date=old.due_date,
            interest_calc_start=old.interest_calc_start,
            interest_calc_end=old.interest_calc_end,
            days=old.days,
            installment_no=old.installment_no,
            balance_before=old.balance_before,
            principal_amount=old.principal_amount,
            interest_amount=old.interest_amount,
            unpaid_interest_amount=old.unpaid_interest_amount,
            balance_after=old.balance_after,
            applied_rate=old.applied_rate,
            row_type=old.row_type,
            memo_interest=old.memo_interest,
            remark=old.remark,
            created_by=created_by,
        )
        db.add(new_row)
        old_to_new[old.id] = new_row
    db.flush()

    new_frozen_row = old_to_new[frozen_row.id]
    for g in frozen_group:
        gr = old_to_new[g.id]
        if frozen_principal_total:
            gr.principal_amount = 0
        if frozen_interest_total:
            gr.interest_amount = 0
        gr.remark = ((gr.remark + " / ") if gr.remark else "") + "상환유예(동결)"

    # 3) 이벤트/유예 레코드
    event = LoanEvent(
        loan_id=loan_id,
        event_type=LoanEventType.DEFERRAL,
        event_date=event_date,
        resulting_version_id=new_version.id,
        reason=reason,
        created_by=created_by,
    )
    db.add(event)
    db.flush()

    recovery_start_date = frozen_row.due_date + relativedelta(years=1)
    deferral = Deferral(
        loan_event_id=event.id,
        frozen_schedule_row_id=new_frozen_row.id,
        defer_target=defer_target,
        frozen_principal_total=frozen_principal_total,
        frozen_interest_total=frozen_interest_total,
        recovery_cycle_months=recovery_cycle_months,
        recovery_count=recovery_count,
        recovery_start_date=recovery_start_date,
        created_by=created_by,
    )
    db.add(deferral)
    db.flush()

    # 4) 회수 스케줄 생성 — 계산된 회수일이 실제 존재하는 정규회차와 일치해야 함
    principal_splits = _split_evenly_with_last_correction(frozen_principal_total, recovery_count) if frozen_principal_total else [0] * recovery_count
    interest_splits = _split_evenly_with_last_correction(frozen_interest_total, recovery_count) if frozen_interest_total else [0] * recovery_count

    rows_by_due_date: dict[date, list[RepaymentScheduleRow]] = {}
    for r in old_to_new.values():
        rows_by_due_date.setdefault(r.due_date, []).append(r)
    all_due_dates_sorted = sorted(rows_by_due_date.keys())

    def _previous_due_date(before: date) -> date:
        earlier = [d for d in all_due_dates_sorted if d < before]
        return max(earlier) if earlier else frozen_row.due_date

    due_date = recovery_start_date
    # 1단계(첨부3)에서 확인된 규칙: 회수가 시작되면 '동결원금에 대한 이자'의 일수 기준은
    # 각 회차 고유의(기존 반기 간격이 아닌) 회수체인 상의 직전 회수일로부터 계산한다 —
    # 기존에 존재하던 정규회차와 날짜가 겹치더라도 동일하게 적용된다.
    chain_prev_due = _previous_due_date(recovery_start_date)
    newly_inserted_rows: list[RepaymentScheduleRow] = []

    for k in range(1, recovery_count + 1):
        chain_days = days_between(chain_prev_due, due_date)
        candidates = [r for r in rows_by_due_date.get(due_date, []) if r.row_type != RowType.YEAR_END_SPLIT_MEMO]

        if candidates:
            target_row = max(candidates, key=lambda r: r.seq_no)
        else:
            # 정규 상환주기와 맞지 않는 회수일 — 유예 회수 전용 행을 새로 삽입(실데이터 확인된 패턴)
            rate = float(get_applicable_rate(db, loan_id, due_date))
            target_row = RepaymentScheduleRow(
                version_id=new_version.id,
                seq_no=-1,  # 아래에서 전체 재정렬
                due_date=due_date,
                interest_calc_start=chain_prev_due,
                interest_calc_end=due_date,
                days=chain_days,
                installment_no=None,
                balance_before=0,
                principal_amount=0,
                interest_amount=0,
                unpaid_interest_amount=0,
                balance_after=0,
                applied_rate=rate,
                row_type=RowType.DEFERRAL_RECOVERY,
                created_by=created_by,
            )
            db.add(target_row)
            db.flush()
            rows_by_due_date.setdefault(due_date, []).append(target_row)
            all_due_dates_sorted = sorted(rows_by_due_date.keys())
            newly_inserted_rows.append(target_row)

        # 1단계(첨부3)에서 실데이터로 확인된 규칙: 누적 잔여 동결원금이 아니라
        # '해당 회차에 회수되는 원금(Z)' 자체에 대해, 회수체인 상의 일수만큼만 단리로 계산한다.
        additional_interest = calc_simple_interest(
            principal_splits[k - 1], chain_days, float(target_row.applied_rate), loan.interest_rounding
        )

        recovery_row = DeferralRecoveryRow(
            deferral_id=deferral.id,
            schedule_row_id=target_row.id,
            installment_seq=k,
            recovery_principal=principal_splits[k - 1],
            recovery_interest=interest_splits[k - 1],
            additional_interest_on_frozen_principal=additional_interest,
            created_by=created_by,
        )
        db.add(recovery_row)

        target_row.unpaid_interest_amount += interest_splits[k - 1] + additional_interest
        target_row.remark = ((target_row.remark + " / ") if target_row.remark else "") + f"상환유예 회수({k}/{recovery_count})"

        chain_prev_due = due_date
        due_date = add_months_eom(due_date, recovery_cycle_months)

    # 새로 삽입된 회수전용 행을 포함해 due_date(및 동일일자 내 기존순서) 기준으로 seq_no 재정렬
    all_rows_ordered = sorted(
        list(old_to_new.values()) + newly_inserted_rows,
        key=lambda r: (r.due_date, 0 if r in old_to_new.values() else 1, r.seq_no if r in old_to_new.values() else 0),
    )
    for idx, r in enumerate(all_rows_ordered, start=1):
        r.seq_no = idx

    db.flush()

    # 5) 잔액 재계산: 정규 원금 + 해당 회차 회수원금을 함께 차감해야 최종 잔액이 0에 수렴
    recovery_principal_by_row: dict[uuid.UUID, int] = {}
    for rr in deferral.recovery_rows:
        recovery_principal_by_row[rr.schedule_row_id] = recovery_principal_by_row.get(rr.schedule_row_id, 0) + rr.recovery_principal

    running_balance = old_rows[0].balance_before
    for new_row in all_rows_ordered:
        is_memo = new_row.row_type == RowType.YEAR_END_SPLIT_MEMO
        total_principal_this_row = new_row.principal_amount + recovery_principal_by_row.get(new_row.id, 0)
        new_row.balance_before = running_balance
        if not is_memo:
            running_balance -= total_principal_this_row
        new_row.balance_after = running_balance

    # 6) 검증
    total_regular_principal = sum(r.principal_amount for r in old_to_new.values())
    total_recovery_principal = sum(rr.recovery_principal for rr in deferral.recovery_rows)
    errors = []
    if total_regular_principal + total_recovery_principal != loan.assigned_amount:
        errors.append(
            f"총 원금(정규 {total_regular_principal:,} + 유예회수 {total_recovery_principal:,})이 "
            f"배정금액({loan.assigned_amount:,})과 일치하지 않습니다."
        )
    if running_balance != 0:
        errors.append(f"최종 잔여원금이 0원이 아닙니다({running_balance:,}).")

    new_version.status = ScheduleVersionStatus.NORMAL if not errors else ScheduleVersionStatus.NEEDS_REVIEW
    new_version.validation_errors = errors or None

    db.commit()
    db.refresh(event)
    return event
