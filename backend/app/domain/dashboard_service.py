"""
대시보드(§34~36) — 저장하지 않고 조회 시점에 원리금 테이블(현재 버전)과 실제 납부에서 계산한다.

KPI 정의(§34)
- 전체 기숙사 수 / 차입금 건수 / 총 차입금(배정금액 합)
- 총 잔여원금: 배정금액 − 기준일까지 '예정된' 원금상환액(현재 버전, 유예 회수원금 포함). 실제 납부 여부와 무관한 계획 기준.
  실제로 덜 낸 금액은 '미납'으로 따로 보여준다.
- 금월/익월 상환예정액: 상환예정일이 기준일의 이번 달/다음 달인 예정 원리금 합계
- 금년 상환예정액·이자예정액: 기준일이 속한 해(1/1~12/31) 전체의 예정 원리금·이자 합계(이미 지난 달 포함) — 화면 표시는 Phase 20에서 뺌(API는 유지)
- 분기별 상환계획액 / 상환액(Phase 20b): 기준일이 속한 해 1~12월을 1~4분기로.
  계획액 = 원리금 테이블(현재 버전) 예정액, 상환액 = 실제 납부(취소 제외). 둘 다 '상환예정일(회차)'이 속한 분기에 넣어
  같은 회차끼리 비교한다(1분기 회차를 4월에 늦게 내도 1분기 상환액). 아직 시작하지 않은 분기는 상환액 없음(None).
  달성률·미상환은 '기준일까지 상환예정일이 지난 회차의 계획액(planned_due_total)' 대비로 본다.
- 안내공문 발급·수신 현황(Phase 20): notice_status 참고
- 금리변동 예정건수 / 미납건수: 업무 알림(§35)과 같은 기준(영업일 D-30, 납부기한 경과 미납)
- (선택) 기간 상환예정액: 검색조건 '기간'이 주어지면 그 구간의 예정 원리금 합계
"""
from collections import defaultdict
from datetime import date

from dateutil.relativedelta import relativedelta
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.alerts_service import build_alert_summary
from app.domain.calendar_service import load_holidays
from app.domain.notice_status import build_notice_status
from app.domain.loan_filters import MIDTERM_EVENT_TYPES, LoanFilters, filtered_loans
from app.domain.rate_service import rate_history, rate_on
from app.domain.schedule_amounts import scheduled_by_due_date
from app.domain.version_utils import NoScheduleError, get_current_version, read_cache
from app.engine.business_day import effective_due_date
from app.models.event import EarlyRepayment, LoanEvent, LoanEventStatus, LoanEventType
from app.models.loan import InterestRateHistory, LoanStatus

MIDTERM_LABEL = {LoanEventType.DEFERRAL: "상환유예", LoanEventType.ACCELERATION: "원금가속상환",
                 LoanEventType.REFINANCING: "대환대출"}
from app.models.payment import ActualPayment, PaymentStatus
from app.models.organization import User
from app.models.reference import Code

MONTHS_AHEAD = 12


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def build_dashboard(db: Session, user: User, as_of: date, f: LoanFilters) -> dict:
    rows = filtered_loans(db, user, f)
    loan_ids = [loan.id for loan, _, _ in rows]
    with read_cache(db, loan_ids):   # 현재 버전·상환일별 금액을 알림 요약과 함께 한 번만 읽는다
        return _build_dashboard(db, user, as_of, f, rows, loan_ids)


def _build_dashboard(db: Session, user: User, as_of: date, f: LoanFilters, rows, loan_ids) -> dict:
    holidays = load_holidays(db)
    codes = {c.id: c.label for c in db.execute(select(Code)).scalars()}

    midterm_ids, rate_hist_count = set(), {}
    midterm_labels: dict = defaultdict(list)   # 차입금별 적용 중인 중도변수 이름(예: 조기상환(전액))
    full_repaid: set = set()                   # 전액 조기상환 → 상태 '종료'
    if loan_ids:
        for loan_id, etype, repay_type in db.execute(
            select(LoanEvent.loan_id, LoanEvent.event_type, EarlyRepayment.repay_type)
            .outerjoin(EarlyRepayment, EarlyRepayment.loan_event_id == LoanEvent.id)
            .where(LoanEvent.loan_id.in_(loan_ids), LoanEvent.status == LoanEventStatus.APPLIED,
                   LoanEvent.event_type.in_(MIDTERM_EVENT_TYPES))
            .order_by(LoanEvent.event_date, LoanEvent.created_at)
        ).all():
            midterm_ids.add(loan_id)
            if etype == LoanEventType.EARLY_REPAYMENT:
                label = "조기상환(전액)" if repay_type == "FULL" else "조기상환(일부)"
                if repay_type == "FULL":
                    full_repaid.add(loan_id)
            else:
                label = MIDTERM_LABEL.get(etype, etype.value)
            if label not in midterm_labels[loan_id]:
                midterm_labels[loan_id].append(label)
        rate_hist_count = dict(db.execute(
            select(InterestRateHistory.loan_id, func.count(InterestRateHistory.id))
            .where(InterestRateHistory.loan_id.in_(loan_ids), InterestRateHistory.is_deleted.is_(False))
            .group_by(InterestRateHistory.loan_id)
        ).all())

    this_month = as_of.replace(day=1)
    next_month = this_month + relativedelta(months=1)
    month_keys = [_month_key(this_month + relativedelta(months=i)) for i in range(MONTHS_AHEAD)]
    monthly = {k: {"principal": 0, "interest": 0} for k in month_keys}
    quarterly = {q: {"principal": 0, "interest": 0, "due_total": 0} for q in (1, 2, 3, 4)}
    dorm_map = {}

    k = defaultdict(int)
    by_fund: dict[str, dict] = {}
    table = []
    dormitories = set()

    for loan, dorm, fund in rows:
        dormitories.add(dorm.id)
        dorm_map[dorm.id] = dorm
        k["loan_count"] += 1
        k["total_assigned"] += loan.assigned_amount
        fslot = by_fund.setdefault(fund.fund_name, {"fund": fund.fund_name, "loan_count": 0, "assigned": 0, "remaining": 0})
        fslot["loan_count"] += 1
        fslot["assigned"] += loan.assigned_amount

        try:
            version = get_current_version(db, loan.id)
        except NoScheduleError:
            version = None
        amounts = scheduled_by_due_date(db, version) if version else {}

        paid_principal_planned = sum(m["principal"] for d, m in amounts.items() if d <= as_of)
        remaining = loan.assigned_amount - paid_principal_planned if version else loan.assigned_amount
        k["total_remaining"] += remaining
        fslot["remaining"] += remaining

        next_due = None
        for d in sorted(amounts):
            m = amounts[d]
            if m["total"] <= 0:
                continue
            if d.year == as_of.year:
                k["year_total"] += m["total"]
                k["year_interest"] += m["interest"]
                q = quarterly[(d.month - 1) // 3 + 1]
                q["principal"] += m["principal"]
                q["interest"] += m["interest"]
                if d <= as_of:
                    q["due_total"] += m["total"]      # 기준일까지 도래한 회차의 계획액(달성률 분모)
            mk = _month_key(d)
            if mk == _month_key(this_month):
                k["this_month_total"] += m["total"]
            elif mk == _month_key(next_month):
                k["next_month_total"] += m["total"]
            if mk in monthly:
                monthly[mk]["principal"] += m["principal"]
                monthly[mk]["interest"] += m["interest"]
            if f.period_from and f.period_to and f.period_from <= d <= f.period_to:
                k["period_total"] += m["total"]
            if next_due is None and effective_due_date(d, holidays) >= as_of:
                next_due = (d, m)

        r_now = rate_on(rate_history(db, loan.id), as_of)
        current_rate = float(r_now) if r_now is not None else None

        table.append({
            "loan_id": loan.id, "loan_code": loan.loan_code,
            "dormitory_id": dorm.id, "dormitory_name": dorm.dormitory_name, "school_name": dorm.school_name,
            "fund": fund.fund_name, "rate_type": loan.rate_type.value, "current_rate": current_rate,
            "repay_method": codes.get(loan.repay_method_code_id), "repay_cycle": codes.get(loan.repay_cycle_code_id),
            "status": (LoanStatus.CLOSED.value if loan.status == LoanStatus.ACTIVE and loan.id in full_repaid
                       else loan.status.value), "assigned_amount": loan.assigned_amount, "remaining_principal": remaining,
            "next_due_date": next_due[0] if next_due else None,
            "next_payment_deadline": effective_due_date(next_due[0], holidays) if next_due else None,
            "next_due_total": next_due[1]["total"] if next_due else None,
            "schedule_version_no": version.version_no if version else None,
            "schedule_status": version.status.value if version else "NO_SCHEDULE",
            "has_midterm_event": loan.id in midterm_ids,
            "midterm_labels": midterm_labels.get(loan.id, []),
            "has_rate_change": rate_hist_count.get(loan.id, 0) >= 2,
            # Phase 21: 차입금 관리 목록용
            "project_type": dorm.project_type, "grace_start_date": loan.grace_start_date, "grace_end_date": loan.grace_end_date,
            "principal_repay_start_date": loan.principal_repay_start_date, "repay_period_months": loan.repay_period_months,
            "maturity_date": max((d for d, m in amounts.items() if m["total"] > 0), default=None),
        })

    alerts = build_alert_summary(db, user, as_of, filters=f)
    notices = build_notice_status(db, user, as_of, dorm_map, alerts["repayment_upcoming"]["items"])
    kpis = {
        "dormitory_count": len(dormitories),
        "loan_count": k["loan_count"],
        "total_assigned": k["total_assigned"],
        "total_remaining_principal": k["total_remaining"],
        "this_month_scheduled": k["this_month_total"],
        "next_month_scheduled": k["next_month_total"],
        "this_year_scheduled": k["year_total"],
        "this_year_interest": k["year_interest"],
        "rate_change_upcoming_count": alerts["rate_changes_upcoming"]["count"],
        "overdue_count": alerts["overdue"]["count"],
        "overdue_amount": alerts["overdue"]["amount"],
    }
    if notices["scope"] == "FOUNDATION":
        kpis["notice_unreceived_count"] = notices["unreceived"]["count"]
    if f.period_from and f.period_to:
        kpis["period_scheduled"] = k["period_total"]

    return {
        "as_of": as_of,
        "period": {"from": f.period_from, "to": f.period_to} if f.period_from and f.period_to else None,
        "kpis": kpis,
        "monthly_schedule": [{"month": mk, **monthly[mk], "total": monthly[mk]["principal"] + monthly[mk]["interest"]}
                             for mk in month_keys],
        "quarterly_schedule": _quarters(db, as_of, loan_ids, quarterly),
        "notices": notices,
        "by_fund": sorted(by_fund.values(), key=lambda x: -x["remaining"]),
        "loans": table,
        "alerts": alerts,
    }


def _quarters(db: Session, as_of: date, loan_ids: list, plan: dict) -> list[dict]:
    paid = {q: {"principal": 0, "interest": 0} for q in (1, 2, 3, 4)}
    if loan_ids:
        for p in db.execute(select(ActualPayment).where(
                ActualPayment.loan_id.in_(loan_ids), ActualPayment.status == PaymentStatus.ACTIVE,
                ActualPayment.is_deleted.is_(False), ActualPayment.due_date >= date(as_of.year, 1, 1),
                ActualPayment.due_date <= date(as_of.year, 12, 31))).scalars():
            slot = paid[(p.due_date.month - 1) // 3 + 1]
            slot["principal"] += p.actual_principal
            slot["interest"] += p.actual_interest
    out = []
    for q in (1, 2, 3, 4):
        started = date(as_of.year, 3 * q - 2, 1) <= as_of
        planned = {"principal": plan[q]["principal"], "interest": plan[q]["interest"],
                   "total": plan[q]["principal"] + plan[q]["interest"]}
        actual = {**paid[q], "total": paid[q]["principal"] + paid[q]["interest"]} if started else None
        out.append({"year": as_of.year, "quarter": q, "label": f"{q}분기", "months": f"{3 * q - 2}~{3 * q}월",
                    **planned,                      # 이전 화면 호환: principal/interest/total = 계획액
                    "planned": planned, "paid": actual, "started": started,
                    "planned_due_total": plan[q]["due_total"]})   # 기준일까지 상환예정일이 지난 회차의 계획액
    return out
