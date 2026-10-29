"""
이벤트 취소 — 문서 §43: 잘못 등록된 이벤트도 삭제하지 않고 취소이력으로 남긴다.

규칙
- '가장 최근에 적용된(APPLIED) 이벤트'만 취소할 수 있다. 중간 이벤트를 빼면 뒤의 이벤트 결과가
  모두 달라지므로 임의로 재계산하지 않고, 최신 이벤트부터 순서대로 취소하도록 요구한다.
- 취소하면 해당 이벤트 직전 버전의 내용을 복원한 새 버전(V+1)을 만든다(기존 버전은 모두 보존).
- 금리변동·전액대환이 바꿨던 금리이력/재원도 함께 되돌린다(금리이력은 삭제 대신 취소 표시).
"""
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.audit import record_audit
from app.domain.schedule_amounts import get_version_with_rows
from app.domain.version_utils import copy_row, get_current_version, start_next_version
from app.models.event import LoanEvent, LoanEventStatus, LoanEventType, Refinancing
from app.models.loan import InterestRateHistory, Loan
from app.models.schedule import RepaymentScheduleVersion

EVENT_LABEL = {
    LoanEventType.DEFERRAL: "상환유예",
    LoanEventType.ACCELERATION: "원금가속상환",
    LoanEventType.EARLY_REPAYMENT: "조기상환",
    LoanEventType.REFINANCING: "대환대출",
    LoanEventType.RATE_CHANGE: "금리변동",
    LoanEventType.DELINQUENCY: "연체처리",
}


class EventCancelError(Exception):
    pass


def cancel_event(db: Session, loan_id: uuid.UUID, event_id: uuid.UUID, *, reason: str, user_id: uuid.UUID | None) -> RepaymentScheduleVersion:
    event = db.get(LoanEvent, event_id)
    if event is None or event.loan_id != loan_id or event.is_deleted:
        raise EventCancelError("이벤트를 찾을 수 없습니다.")
    if event.status == LoanEventStatus.CANCELLED:
        raise EventCancelError("이미 취소된 이벤트입니다.")
    if event.resulting_version_id is None:
        raise EventCancelError("원리금 테이블을 만들지 않은 이벤트는 취소 대상이 아닙니다.")

    latest = db.execute(
        select(LoanEvent)
        .where(LoanEvent.loan_id == loan_id, LoanEvent.status == LoanEventStatus.APPLIED, LoanEvent.is_deleted.is_(False))
        .order_by(LoanEvent.created_at.desc(), LoanEvent.id.desc())
    ).scalars().first()
    if latest is None or latest.id != event.id:
        raise EventCancelError(
            "가장 최근에 적용된 이벤트만 취소할 수 있습니다. 이후에 적용된 이벤트를 먼저 취소하세요."
        )

    refi = None
    if event.event_type == LoanEventType.REFINANCING:
        refi = db.execute(select(Refinancing).where(Refinancing.loan_event_id == event.id)).scalar_one_or_none()
        if refi is None:
            raise EventCancelError("대환 상세 정보를 찾을 수 없습니다.")
        if refi.refi_type == "FULL" and refi.old_fund_id is None:
            raise EventCancelError("이전 재원 정보가 없는 전액대환은 자동 취소할 수 없습니다. 관리자에게 문의하세요.")

    result_version = db.get(RepaymentScheduleVersion, event.resulting_version_id)
    if result_version is None or result_version.previous_version_id is None:
        raise EventCancelError("복원할 이전 버전을 찾을 수 없습니다.")
    prev = db.get(RepaymentScheduleVersion, result_version.previous_version_id)
    base_version = get_version_with_rows(db, loan_id, prev.version_no)

    loan = db.get(Loan, loan_id)
    current = get_current_version(db, loan_id)

    # 금리이력/재원 되돌리기 (금리변동, 전액대환)
    reverts_rate = event.event_type == LoanEventType.RATE_CHANGE or (refi is not None and refi.refi_type == "FULL")
    if reverts_rate:
        added = db.execute(
            select(InterestRateHistory)
            .where(InterestRateHistory.loan_id == loan_id, InterestRateHistory.is_deleted.is_(False),
                   InterestRateHistory.effective_start_date == event.event_date)
            .order_by(InterestRateHistory.created_at.desc())
        ).scalars().first()
        if added is not None:
            added.is_deleted = True
            record_audit(db, table_name="interest_rate_history", record_id=added.id, loan_id=loan_id, action="CANCEL",
                         user_id=user_id, field="annual_rate", before=added.annual_rate, after=None,
                         reason=f"{EVENT_LABEL[event.event_type]} 취소: {reason}")
        previous = db.execute(
            select(InterestRateHistory)
            .where(InterestRateHistory.loan_id == loan_id, InterestRateHistory.is_deleted.is_(False),
                   InterestRateHistory.effective_end_date == event.event_date)
        ).scalars().first()
        if previous is not None:
            record_audit(db, table_name="interest_rate_history", record_id=previous.id, loan_id=loan_id, action="UPDATE",
                         user_id=user_id, field="effective_end_date", before=previous.effective_end_date, after=None,
                         reason=f"{EVENT_LABEL[event.event_type]} 취소로 금리 구간 복원")
            previous.effective_end_date = None
    if refi is not None and refi.refi_type == "FULL":
        record_audit(db, table_name="loan", record_id=loan.id, loan_id=loan_id, action="UPDATE", user_id=user_id,
                     field="fund_id", before=loan.fund_id, after=refi.old_fund_id, reason=f"전액대환 취소: {reason}")
        loan.fund_id = refi.old_fund_id

    label = EVENT_LABEL.get(event.event_type, event.event_type.value)
    new_version = start_next_version(db, current, f"{label} 취소", user_id)
    new_version.status = base_version.status
    new_version.validation_errors = base_version.validation_errors
    for seq, row in enumerate(sorted(base_version.rows, key=lambda r: r.seq_no), start=1):
        db.add(copy_row(row, new_version.id, seq, user_id))

    event.status = LoanEventStatus.CANCELLED
    record_audit(db, table_name="loan_event", record_id=event.id, loan_id=loan_id, action="CANCEL", user_id=user_id,
                 field="status", before=LoanEventStatus.APPLIED.value, after=LoanEventStatus.CANCELLED.value, reason=reason)
    db.commit()
    db.refresh(new_version)
    return new_version
