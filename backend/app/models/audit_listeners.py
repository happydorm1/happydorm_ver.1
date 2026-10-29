"""
등록(CREATE) 이력 자동 기록. 모델 insert 시점에 AuditLog를 같은 트랜잭션에 남기므로
개별 API에서 기록을 빠뜨릴 수 없다. (수정/취소는 각 서비스에서 record_audit으로 명시 기록)
"""
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import event, insert

from app.models.audit import AuditLog
from app.models.event import LoanEvent
from app.models.loan import InterestRateHistory, Loan
from app.models.payment import ActualPayment


def _write(connection, *, table, record_id, loan_id, by, summary):
    connection.execute(
        insert(AuditLog.__table__).values(
            id=uuid.uuid4(), table_name=table, record_id=record_id, loan_id=loan_id, action="CREATE",
            after_value=json.dumps(summary, ensure_ascii=False, default=str), changed_by=by,
            changed_at=datetime.now(timezone.utc),
        )
    )


@event.listens_for(Loan, "after_insert")
def _loan_created(mapper, connection, t):
    _write(connection, table="loan", record_id=t.id, loan_id=t.id, by=t.created_by,
           summary={"loan_code": t.loan_code, "assigned_amount": t.assigned_amount,
                    "repay_period_months": t.repay_period_months})


@event.listens_for(InterestRateHistory, "after_insert")
def _rate_created(mapper, connection, t):
    _write(connection, table="interest_rate_history", record_id=t.id, loan_id=t.loan_id, by=t.created_by,
           summary={"effective_start_date": t.effective_start_date, "annual_rate": t.annual_rate})


@event.listens_for(LoanEvent, "after_insert")
def _event_created(mapper, connection, t):
    _write(connection, table="loan_event", record_id=t.id, loan_id=t.loan_id, by=t.created_by,
           summary={"event_type": t.event_type, "event_date": t.event_date, "reason": t.reason})


@event.listens_for(ActualPayment, "after_insert")
def _payment_created(mapper, connection, t):
    _write(connection, table="actual_payment", record_id=t.id, loan_id=t.loan_id, by=t.created_by,
           summary={"due_date": t.due_date, "actual_paid_date": t.actual_paid_date,
                    "actual_principal": t.actual_principal, "actual_interest": t.actual_interest})
