"""
Phase 10: 실제 납부관리 (§27, §35 미납, §34 미납건수).

예정(원리금 테이블 현재 버전)과 실제(ActualPayment)를 상환예정일 단위로 비교한다.
- 한 상환예정일에 여러 번 나눠 납부 가능 → 누적 합계로 상태 판단
- 상태: PAID(일치) / OVERPAID(초과) / PARTIAL(일부납부, 부족) / UNPAID(미납) / PENDING(아직 납부기한 전)
- 납부기한(상환예정일, 휴일이면 다음 영업일 — 확정 업무기준)이 지났는데(as_of > 납부기한) 부족액이 남으면 is_overdue=True
- 잘못 입력한 납부는 삭제하지 않고 취소(43번). 취소된 납부는 합계에서 제외
- 연체이자는 고정 공식이 없어(확정사항) 여기서 자동 계산하지 않는다 — 미납 사실과 부족액만 제공
"""
import uuid
from collections import defaultdict
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.audit import record_audit
from app.domain.calendar_service import load_holidays
from app.domain.schedule_amounts import scheduled_by_due_date
from app.domain.version_utils import NoScheduleError, get_current_version
from app.engine.business_day import effective_due_date
from app.models.loan import Loan
from app.models.payment import ActualPayment, PaymentStatus


class PaymentError(Exception):
    pass


def record_payment(
    db: Session, loan: Loan, *, due_date: date, actual_paid_date: date, actual_principal: int,
    actual_interest: int, note: str | None, user_id: uuid.UUID | None,
) -> ActualPayment:
    if actual_principal < 0 or actual_interest < 0:
        raise PaymentError("납부 원금·이자는 음수일 수 없습니다.")
    if actual_principal + actual_interest <= 0:
        raise PaymentError("납부금액이 0원입니다. 미납은 입력하지 않으면 자동으로 미납 처리됩니다.")
    if actual_paid_date > date.today():
        raise PaymentError("미래 날짜의 납부는 입력할 수 없습니다.")
    try:
        version = get_current_version(db, loan.id)
    except NoScheduleError as e:
        raise PaymentError(str(e))
    scheduled = scheduled_by_due_date(db, version).get(due_date)
    if scheduled is None or scheduled["total"] <= 0:
        raise PaymentError(f"{due_date}에는 현재 원리금 테이블상 예정된 상환액이 없습니다.")

    payment = ActualPayment(
        loan_id=loan.id, due_date=due_date, actual_paid_date=actual_paid_date,
        actual_principal=actual_principal, actual_interest=actual_interest, remark=note, created_by=user_id,
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)
    return payment


def cancel_payment(db: Session, loan_id: uuid.UUID, payment_id: uuid.UUID, *, reason: str, user_id: uuid.UUID | None) -> ActualPayment:
    p = db.get(ActualPayment, payment_id)
    if p is None or p.loan_id != loan_id:
        raise PaymentError("납부 내역을 찾을 수 없습니다.")
    if p.status == PaymentStatus.CANCELLED:
        raise PaymentError("이미 취소된 납부입니다.")
    record_audit(db, table_name="actual_payment", record_id=p.id, loan_id=loan_id, action="CANCEL", user_id=user_id,
                 field="status", before=PaymentStatus.ACTIVE.value, after=PaymentStatus.CANCELLED.value, reason=reason)
    p.status = PaymentStatus.CANCELLED
    p.cancel_reason = reason
    p.cancelled_at = datetime.now(timezone.utc)
    p.cancelled_by = user_id
    db.commit()
    db.refresh(p)
    return p


def payment_summary(db: Session, loan: Loan, as_of: date, holidays: set[date] | None = None) -> dict:
    if holidays is None:
        holidays = load_holidays(db)
    try:
        version = get_current_version(db, loan.id)
    except NoScheduleError as e:
        raise PaymentError(str(e))
    scheduled = {d: m for d, m in scheduled_by_due_date(db, version).items() if m["total"] > 0}

    paid: dict[date, dict] = defaultdict(lambda: {"principal": 0, "interest": 0, "count": 0, "last_paid": None})
    cache = db.info.get("read_cache")
    payments = cache[("pay", loan.id)] if cache is not None and ("pay", loan.id) in cache else db.execute(
        select(ActualPayment).where(ActualPayment.loan_id == loan.id, ActualPayment.status == PaymentStatus.ACTIVE)).scalars().all()
    for p in payments:
        slot = paid[p.due_date]
        slot["principal"] += p.actual_principal
        slot["interest"] += p.actual_interest
        slot["count"] += 1
        slot["last_paid"] = p.actual_paid_date if slot["last_paid"] is None else max(slot["last_paid"], p.actual_paid_date)

    rows = []
    for d in sorted(scheduled):
        s = scheduled[d]
        pd_ = paid.get(d)
        paid_total = (pd_["principal"] + pd_["interest"]) if pd_ else 0
        diff = paid_total - s["total"]
        deadline = effective_due_date(d, holidays)   # 휴일 상환일은 다음 영업일이 납부기한(확정 업무기준)
        overdue_base = deadline < as_of
        if paid_total == 0:
            status = "UNPAID" if overdue_base else "PENDING"
        elif diff == 0:
            status = "PAID"
        elif diff > 0:
            status = "OVERPAID"
        else:
            status = "PARTIAL"
        outstanding = max(s["total"] - paid_total, 0)
        last_paid = pd_["last_paid"] if pd_ else None
        rows.append({
            "due_date": d, "payment_deadline": deadline,
            "scheduled_principal": s["principal"], "scheduled_interest": s["interest"], "scheduled_total": s["total"],
            "paid_principal": pd_["principal"] if pd_ else 0, "paid_interest": pd_["interest"] if pd_ else 0,
            "paid_total": paid_total, "difference": diff, "outstanding": outstanding,
            "status": status, "is_overdue": overdue_base and outstanding > 0,
            "payment_count": pd_["count"] if pd_ else 0, "last_paid_date": last_paid,
            "days_after_due": max((last_paid - deadline).days, 0) if last_paid else None,
        })

    unmatched = [
        {"due_date": d, "paid_total": v["principal"] + v["interest"]}
        for d, v in sorted(paid.items()) if d not in scheduled
    ]
    due_rows = [r for r in rows if r["due_date"] <= as_of]
    sched_due = sum(r["scheduled_total"] for r in due_rows)
    paid_due = sum(min(r["paid_total"], r["scheduled_total"]) for r in due_rows)
    overdue = [r for r in rows if r["is_overdue"]]
    return {
        "loan_id": loan.id, "loan_code": loan.loan_code, "as_of": as_of,
        "totals": {
            "scheduled_to_date": sched_due, "paid_to_date": paid_due,
            "collection_rate": round(paid_due / sched_due, 4) if sched_due else None,
            "overdue_count": len(overdue), "overdue_amount": sum(r["outstanding"] for r in overdue),
        },
        "rows": rows,
        "unmatched_payments": unmatched,  # 현재 버전에 없는 상환일의 납부(이벤트 취소 등으로 일정이 바뀐 경우)
    }
