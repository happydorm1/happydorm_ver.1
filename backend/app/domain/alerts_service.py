"""
대시보드용 업무 알림 요약(§34~35) — 저장하지 않고 조회 시점에 계산한다.
상환 예정(D-15/10/5/3/1) · 금리변동 예정(D-30) · 미납 · 확인 필요(계산 오류, 잔여원금 불일치, 금리 미등록, 공휴일 미등록,
중도변수 미처리 — 판정 기준은 midterm_check 참고, 로그인 시 팝업으로도 띄운다).
"""
import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.calendar_service import load_holidays, years_without_holidays
from app.domain.midterm_check import find_midterm_pending
from app.domain.loan_filters import LoanFilters, filtered_loans
from app.domain.notification_service import upcoming_rate_change
from app.domain.rate_service import rate_history
from app.domain.payment_service import PaymentError, payment_summary
from app.domain.schedule_amounts import scheduled_by_due_date
from app.domain.version_utils import NoScheduleError, get_current_version, read_cache
from app.engine.business_day import current_threshold, subtract_business_days
from app.models.event import EventRequest, EventRequestStatus, LoanEvent, LoanEventStatus, Refinancing

from app.models.organization import User
from app.models.schedule import ScheduleVersionStatus


def build_alert_summary(db: Session, user: User, as_of: date, *, dormitory_id: uuid.UUID | None = None,
                        filters: LoanFilters | None = None) -> dict:
    f = filters or LoanFilters(dormitory_id=dormitory_id)
    rows = filtered_loans(db, user, f.active_only())   # 업무 알림은 진행 중(ACTIVE) 차입금만
    with read_cache(db, [loan.id for loan, _, _ in rows]):
        return _build(db, rows, as_of)


def _build(db: Session, rows, as_of: date) -> dict:
    ids = [loan.id for loan, _, _ in rows]
    partial_refi_ids, pending_by_loan = set(), {}
    if ids:
        partial_refi_ids = set(db.execute(
            select(LoanEvent.loan_id).join(Refinancing, Refinancing.loan_event_id == LoanEvent.id)
            .where(LoanEvent.loan_id.in_(ids), LoanEvent.status == LoanEventStatus.APPLIED, Refinancing.refi_type == "PARTIAL")).scalars())
        for r in db.execute(select(EventRequest).where(EventRequest.loan_id.in_(ids), EventRequest.status == EventRequestStatus.REQUESTED)).scalars():
            pending_by_loan.setdefault(r.loan_id, []).append(r)

    holidays = load_holidays(db)
    horizon = as_of + timedelta(days=365)
    upcoming, rate_changes, overdue = [], [], []
    calc_errors, balance_mismatch, rate_missing, midterm = [], [], [], []
    due_years: set[int] = set()

    for loan, dorm, fund in rows:
        base = {"loan_id": loan.id, "loan_code": loan.loan_code, "dormitory_name": dorm.dormitory_name, "fund": fund.fund_name}
        hist = rate_history(db, loan.id)
        if not any(h.effective_start_date <= loan.assigned_date for h in hist):
            rate_missing.append({**base, "issue": "배정일 기준 적용 가능한 금리 이력이 없습니다."})

        try:
            version = get_current_version(db, loan.id)
        except NoScheduleError:
            continue
        amounts = scheduled_by_due_date(db, version)

        if version.status == ScheduleVersionStatus.NEEDS_REVIEW:
            calc_errors.append({**base, "version_no": version.version_no, "errors": version.validation_errors})
        else:
            total_principal = sum(m["principal"] for m in amounts.values())
            final_balance = max(version.rows, key=lambda r: r.seq_no).balance_after if version.rows else None
            if total_principal != loan.assigned_amount or final_balance != 0:
                balance_mismatch.append({**base, "version_no": version.version_no, "total_principal": total_principal,
                                         "assigned_amount": loan.assigned_amount, "final_balance": final_balance})

        for d, m in amounts.items():
            if as_of <= d <= horizon and m["total"] > 0:
                due_years.add(d.year)

        try:
            summary = payment_summary(db, loan, as_of, holidays)
        except PaymentError:
            summary = None
        if summary:
            for row in summary["rows"]:
                if row["is_overdue"]:
                    overdue.append({**base, "due_date": row["due_date"], "scheduled_total": row["scheduled_total"],
                                    "paid_total": row["paid_total"], "outstanding": row["outstanding"], "status": row["status"]})
                elif (row["payment_deadline"] >= as_of and row["status"] not in ("PAID", "OVERPAID")
                      and (row["payment_deadline"] - as_of).days <= 45):   # D-15 영업일은 달력으로 45일을 넘지 않음(계산 생략)
                    n = current_threshold(row["payment_deadline"], as_of, holidays)
                    if n is not None:
                        upcoming.append({**base, "due_date": row["due_date"], "threshold": f"D-{n}", "threshold_n": n,
                                         "payment_deadline": row["payment_deadline"],
                                         "alert_date": subtract_business_days(row["payment_deadline"], n, holidays),
                                         "principal": row["scheduled_principal"], "interest": row["scheduled_interest"],
                                         "total": row["scheduled_total"], "outstanding": row["outstanding"]})

        partial_refi = loan.id in partial_refi_ids
        pending = pending_by_loan.get(loan.id, [])
        midterm.extend(find_midterm_pending(loan, version, hist, summary, as_of, base, blended_rates=partial_refi,
                                            pending_requests=pending))

        info = upcoming_rate_change(db, loan, dorm, fund, version, amounts, as_of, holidays)
        if info:
            rate_changes.append({**base, **{k: v for k, v in info["payload"].items() if k not in base and k != "dormitory_name"}})

    upcoming.sort(key=lambda x: (x["due_date"], x["loan_code"]))
    rate_changes.sort(key=lambda x: (x["change_date"], x["loan_code"]))
    overdue.sort(key=lambda x: (x["due_date"], x["loan_code"]))
    missing_years = years_without_holidays(db, due_years)

    return {
        "as_of": as_of,
        "repayment_upcoming": {"count": len(upcoming), "items": upcoming},
        "rate_changes_upcoming": {"count": len(rate_changes), "items": rate_changes},
        "overdue": {"count": len(overdue), "amount": sum(i["outstanding"] for i in overdue), "items": overdue},
        "needs_attention": {
            "calculation_errors": calc_errors,
            "balance_mismatches": balance_mismatch,
            "rate_not_registered": rate_missing,
            "holidays_not_registered_years": missing_years,
            "midterm_pending": midterm,
            "not_covered": [],
            "count": len(calc_errors) + len(balance_mismatch) + len(rate_missing) + len(midterm) + (1 if missing_years else 0),
        },
    }
