"""
알림 생성 배치(§31~33).

상환일정 알림: 납부기한 D-15/10/5/3/1 — '영업일' 기준(달력일 아님). 대상: SPC 담당자 + 재단 담당자.
  · 납부기한 = 상환예정일(휴일이면 다음 영업일 — 확정 업무기준).
  · 이미 납부 완료된 상환일에는 보내지 않는다.
  · 배치가 하루 건너뛰어도 알림이 사라지지 않도록, 이미 도래한 기준 중 '가장 임박한 하나'만 보낸다
    (밀린 D-15, D-10을 한꺼번에 보내지 않는다).
금리변동 알림: 변동금리 차입금의 현재 금리 구간 종료일(=변경예정일) D-30 이내. 대상: 재단 자금 담당자.
  · D-30은 영업일 기준(확정 업무기준): 변경예정일 이전 30번째 영업일부터 알린다.

배치는 여러 번 실행해도 안전하다(중복 생성 없음). 외부 스케줄러(cron 등)가 하루 한 번 호출한다.
"""
import uuid
from collections import defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.calendar_service import load_holidays
from app.domain.rate_service import rate_history
from app.domain.payment_service import PaymentError, payment_summary
from app.domain.schedule_amounts import scheduled_by_due_date
from app.domain.version_utils import NoScheduleError, get_current_version
from app.engine.business_day import (
    REPAYMENT_THRESHOLDS, count_business_days, current_threshold, subtract_business_days,
)
from app.models.loan import InterestRateHistory, Loan, LoanStatus, RateType
from app.models.notification import Notification
from app.models.organization import Dormitory, User, UserRole
from app.models.reference import Fund

RATE_CHANGE_NOTICE_DAYS = 30


def _active_users(db: Session, *conds):
    return db.execute(select(User).where(User.is_deleted.is_(False), User.is_active.is_(True), *conds)).scalars().all()


def repayment_recipients(db: Session, dorm: Dormitory) -> list[User]:
    """§32: SPC 담당자 + 재단 담당자(기숙사에 지정된 담당자, 없으면 재단 자금담당자 전원)."""
    users = {u.id: u for u in _active_users(db, User.role == UserRole.SPC_MANAGER, User.spc_id == dorm.spc_id)}
    foundation = []
    if dorm.manager_user_id:
        foundation = _active_users(db, User.id == dorm.manager_user_id)
    if not foundation:
        foundation = _active_users(db, User.role == UserRole.FOUNDATION_FUND_MANAGER)
    users.update({u.id: u for u in foundation})
    return list(users.values())


def rate_change_recipients(db: Session, dorm: Dormitory) -> list[User]:
    """§33: 재단 자금 담당자."""
    users = {u.id: u for u in _active_users(db, User.role == UserRole.FOUNDATION_FUND_MANAGER)}
    if dorm.manager_user_id:
        users.update({u.id: u for u in _active_users(db, User.id == dorm.manager_user_id)})
    return list(users.values())


def _loan_targets(db: Session, dormitory_id: uuid.UUID | None):
    stmt = (
        select(Loan, Dormitory, Fund)
        .join(Dormitory, Dormitory.id == Loan.dormitory_id)
        .join(Fund, Fund.id == Loan.fund_id)
        .where(Loan.is_deleted.is_(False), Loan.status == LoanStatus.ACTIVE, Dormitory.is_deleted.is_(False))
    )
    if dormitory_id:
        stmt = stmt.where(Dormitory.id == dormitory_id)
    return db.execute(stmt.order_by(Loan.loan_code)).all()


def _existing_keys(db: Session, loan_id: uuid.UUID) -> set[tuple]:
    return {
        (n.recipient_user_id, n.notification_type, n.reference_date)
        for n in db.execute(select(Notification).where(Notification.loan_id == loan_id)).scalars()
    }


def upcoming_rate_change(db: Session, loan: Loan, dorm: Dormitory, fund: Fund, version, amounts, as_of: date,
                         holidays: set[date] | None = None) -> dict | None:
    """영업일 기준 D-30 이내 금리변동 예정이 있으면 알림/대시보드에 쓸 내용(payload/title/body)을 만든다. 없으면 None."""
    if holidays is None:
        holidays = load_holidays(db)
    if loan.rate_type != RateType.VARIABLE:
        return None
    hist = rate_history(db, loan.id)
    current = next((h for h in reversed(hist) if h.effective_start_date <= as_of), None)
    if not current or not current.effective_end_date:
        return None
    change_date = current.effective_end_date
    days_left = (change_date - as_of).days                      # 달력일(참고용)
    if change_date < as_of or subtract_business_days(change_date, RATE_CHANGE_NOTICE_DAYS, holidays) > as_of:
        return None
    business_days_left = count_business_days(as_of, change_date, holidays)

    nxt = next((h for h in hist if h.effective_start_date == change_date), None)
    related = next((r for r in sorted(version.rows, key=lambda r: r.seq_no)
                    if r.interest_calc_end >= change_date and r.row_type.value != "YEAR_END_SPLIT_MEMO"), None)
    rel_due = related.due_date if related else None
    rel_total = amounts[rel_due]["total"] if rel_due in amounts else None
    check = ("변경 금리 고시 확인 후 금리이력 등록 및 금리변동 이벤트 적용 필요" if nxt is None else
             "등록된 변경금리 확인 후 금리변동 이벤트를 적용해 원리금 테이블을 재계산해야 함")
    payload = {
        "dormitory_name": dorm.dormitory_name, "loan_id": str(loan.id), "loan_code": loan.loan_code, "fund": fund.fund_name,
        "current_rate": float(current.annual_rate), "change_date": change_date.isoformat(),
        "new_rate": float(nxt.annual_rate) if nxt else None, "days_left": days_left,
        "business_days_left": business_days_left,
        "related_due_date": rel_due.isoformat() if rel_due else None,
        "related_installment_no": related.installment_no if related else None,
        "related_total": rel_total, "check_items": check,
    }
    new_txt = f"{float(nxt.annual_rate)}%" if nxt else "미정"
    body = (f"{dorm.dormitory_name} / {loan.loan_code} ({fund.fund_name}) — 현재금리 {float(current.annual_rate)}% → "
            f"변경예정금리 {new_txt}, 변경예정일 {change_date}"
            + (f", 관련 상환일 {rel_due} 원리금 {rel_total:,}원" if rel_due and rel_total else "")
            + f". 확인 필요: {check}")
    return {"payload": payload, "title": f"[금리변동 D-{business_days_left}] {dorm.dormitory_name} {change_date}", "body": body}


def scan_notifications(db: Session, as_of: date, *, dormitory_id: uuid.UUID | None = None) -> dict:
    holidays = load_holidays(db)
    created = {"repayment": 0, "rate_change": 0}
    loans_scanned = 0

    for loan, dorm, fund in _loan_targets(db, dormitory_id):
        try:
            version = get_current_version(db, loan.id)
        except NoScheduleError:
            continue
        loans_scanned += 1
        existing = _existing_keys(db, loan.id)
        amounts = scheduled_by_due_date(db, version)
        last_balance: dict[date, int] = {}
        for r in sorted(version.rows, key=lambda r: r.seq_no):
            last_balance[r.due_date] = r.balance_after

        # ---- 상환일정 알림 ----
        try:
            summary = payment_summary(db, loan, as_of, holidays)
        except PaymentError:
            summary = None
        if summary:
            recipients = repayment_recipients(db, dorm)
            for row in summary["rows"]:
                due = row["due_date"]
                deadline = row["payment_deadline"]          # 휴일이면 다음 영업일
                if deadline < as_of or row["status"] in ("PAID", "OVERPAID"):
                    continue
                n = current_threshold(deadline, as_of, holidays)
                if n is None:
                    continue
                # 더 임박한(또는 같은) 기준의 알림이 이미 나간 상환일이면 건너뛴다
                sent_ns = [
                    int(t.split("_D")[1]) for (_, t, ref) in existing if ref == due and t.startswith("REPAYMENT_D")
                ]
                if any(s <= n for s in sent_ns):
                    continue
                m = amounts[due]
                remaining = last_balance[due]
                outstanding = row["outstanding"]
                payload = {
                    "dormitory_name": dorm.dormitory_name, "loan_code": loan.loan_code, "fund": fund.fund_name,
                    "due_date": due.isoformat(), "payment_deadline": deadline.isoformat(), "principal": m["principal"], "interest": m["interest"],
                    "total": m["total"], "remaining_principal": remaining, "outstanding": outstanding,
                    "business_day_threshold": n,
                    "trigger_date_of_threshold": subtract_business_days(deadline, n, holidays).isoformat(),
                }
                title = f"[D-{n}] {dorm.dormitory_name} 상환일 {due}"
                body = (f"{dorm.dormitory_name} / {loan.loan_code} ({fund.fund_name}) 납부일 {due} — "
                        f"원금 {m['principal']:,}원, 이자 {m['interest']:,}원, 원리금 {m['total']:,}원, "
                        f"상환 후 잔여원금 {remaining:,}원 (영업일 기준 D-{n})")
                if deadline != due:
                    body += f" / 상환일이 휴일이라 납부기한은 {deadline}"
                if outstanding != m["total"]:
                    body += f" / 일부 납부됨, 미납 잔액 {outstanding:,}원"
                for u in recipients:
                    key = (u.id, f"REPAYMENT_D{n}", due)
                    if key in existing:
                        continue
                    db.add(Notification(recipient_user_id=u.id, loan_id=loan.id, dormitory_id=dorm.id,
                                        notification_type=f"REPAYMENT_D{n}", reference_date=due, trigger_date=as_of,
                                        title=title, body=body, payload=payload))
                    existing.add(key)
                    created["repayment"] += 1

        # ---- 금리변동 알림 ----
        info = upcoming_rate_change(db, loan, dorm, fund, version, amounts, as_of, holidays)
        if info:
            for u in rate_change_recipients(db, dorm):
                key = (u.id, "RATE_CHANGE_D30", date.fromisoformat(info["payload"]["change_date"]))
                if key in existing:
                    continue
                db.add(Notification(recipient_user_id=u.id, loan_id=loan.id, dormitory_id=dorm.id,
                                    notification_type="RATE_CHANGE_D30", reference_date=key[2], trigger_date=as_of,
                                    title=info["title"], body=info["body"], payload=info["payload"]))
                existing.add(key)
                created["rate_change"] += 1

    db.commit()
    return {"as_of": as_of, "loans_scanned": loans_scanned, "created": created,
            "thresholds": list(REPAYMENT_THRESHOLDS)}
