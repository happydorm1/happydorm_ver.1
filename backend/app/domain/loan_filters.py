"""
대시보드 검색조건(§36) — KPI·업무알림·차입금 목록이 모두 같은 조건으로 같은 차입금 집합을 보도록 한 곳에서 만든다.

조건: 기숙사 · 학교 · SPC · 담당자 · 재원 · 금리유형 · 상환유형 · 상환주기 · 기간 · 상태 · 금리변동 여부 · 중도변수 여부
- '기간'은 차입금을 거르는 조건이 아니라 '기간 내 상환예정액' 집계 구간이다(dashboard_service에서 사용).
- 금리변동 여부: 취소되지 않은 금리 이력이 2건 이상(= 금리가 바뀐 적이 있거나 변경금리가 등록됨)인 차입금.
- 중도변수 여부: 적용 중인(취소되지 않은) 상환유예·조기상환·원금가속상환·대환 이벤트가 있는 차입금. 금리변동은 별도 조건이라 제외.
- SPC 담당자는 항상 자기 SPC 소속 기숙사의 차입금으로 한정된다(권한, 조건과 무관).
"""
import uuid
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import Select, exists, func, select
from sqlalchemy.orm import Session

from app.models.event import LoanEvent, LoanEventStatus, LoanEventType
from app.models.loan import InterestRateHistory, Loan, LoanStatus, RateType
from app.models.organization import Dormitory, User, UserRole
from app.models.reference import Fund

MIDTERM_EVENT_TYPES = (
    LoanEventType.DEFERRAL, LoanEventType.EARLY_REPAYMENT, LoanEventType.ACCELERATION, LoanEventType.REFINANCING,
)


@dataclass
class LoanFilters:
    dormitory_id: uuid.UUID | None = None
    school: str | None = None                    # 학교명 부분일치
    spc_id: uuid.UUID | None = None
    manager_user_id: uuid.UUID | None = None     # 기숙사 담당자(재단)
    fund_id: uuid.UUID | None = None
    rate_type: RateType | None = None
    repay_method_code_id: uuid.UUID | None = None
    repay_cycle_code_id: uuid.UUID | None = None
    statuses: list[LoanStatus] = field(default_factory=list)   # 비우면 전체(삭제 제외)
    has_rate_change: bool | None = None
    has_midterm_event: bool | None = None
    period_from: date | None = None
    period_to: date | None = None

    def active_only(self) -> "LoanFilters":
        """업무 알림은 진행 중(ACTIVE) 차입금만 본다 — 같은 조건에 상태만 고정한 복사본."""
        return LoanFilters(**{**self.__dict__, "statuses": [LoanStatus.ACTIVE]})


def _rate_change_exists():
    sub = (
        select(func.count(InterestRateHistory.id))
        .where(InterestRateHistory.loan_id == Loan.id, InterestRateHistory.is_deleted.is_(False))
        .scalar_subquery()
    )
    return sub >= 2


def _midterm_event_exists():
    return exists().where(
        LoanEvent.loan_id == Loan.id,
        LoanEvent.status == LoanEventStatus.APPLIED,
        LoanEvent.event_type.in_(MIDTERM_EVENT_TYPES),
    )


def loan_query(user: User, f: LoanFilters) -> Select:
    stmt = (
        select(Loan, Dormitory, Fund)
        .join(Dormitory, Dormitory.id == Loan.dormitory_id)
        .join(Fund, Fund.id == Loan.fund_id)
        .where(Loan.is_deleted.is_(False), Dormitory.is_deleted.is_(False))
    )
    if user.role == UserRole.SPC_MANAGER:
        stmt = stmt.where(Dormitory.spc_id == user.spc_id) if user.spc_id else stmt.where(Dormitory.id.is_(None))

    if f.dormitory_id:
        stmt = stmt.where(Dormitory.id == f.dormitory_id)
    if f.school:
        stmt = stmt.where(Dormitory.school_name.ilike(f"%{f.school.strip()}%"))
    if f.spc_id:
        stmt = stmt.where(Dormitory.spc_id == f.spc_id)
    if f.manager_user_id:
        stmt = stmt.where(Dormitory.manager_user_id == f.manager_user_id)
    if f.fund_id:
        stmt = stmt.where(Loan.fund_id == f.fund_id)
    if f.rate_type:
        stmt = stmt.where(Loan.rate_type == f.rate_type)
    if f.repay_method_code_id:
        stmt = stmt.where(Loan.repay_method_code_id == f.repay_method_code_id)
    if f.repay_cycle_code_id:
        stmt = stmt.where(Loan.repay_cycle_code_id == f.repay_cycle_code_id)
    if f.statuses:
        stmt = stmt.where(Loan.status.in_(f.statuses))
    if f.has_rate_change is not None:
        cond = _rate_change_exists()
        stmt = stmt.where(cond if f.has_rate_change else ~cond)
    if f.has_midterm_event is not None:
        cond = _midterm_event_exists()
        stmt = stmt.where(cond if f.has_midterm_event else ~cond)
    return stmt.order_by(Dormitory.dormitory_name, Loan.loan_code)


def filtered_loans(db: Session, user: User, f: LoanFilters):
    return db.execute(loan_query(user, f)).all()
