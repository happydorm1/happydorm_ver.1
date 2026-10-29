"""
2단계 ERD 설계서 '6. 이벤트 테이블' 섹션 — 우선 Phase 6(상환유예)만 구현.
조기상환/가속상환/대환/연체는 Phase 7~8에서 같은 LOAN_EVENT 부모 아래 추가된다.
"""
import enum
import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, Enum, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import AuditMixin, Base


class LoanEventType(str, enum.Enum):
    DEFERRAL = "DEFERRAL"
    EARLY_REPAYMENT = "EARLY_REPAYMENT"
    ACCELERATION = "ACCELERATION"
    REFINANCING = "REFINANCING"
    DELINQUENCY = "DELINQUENCY"
    RATE_CHANGE = "RATE_CHANGE"


class LoanEventStatus(str, enum.Enum):
    APPLIED = "APPLIED"       # 적용완료
    CANCELLED = "CANCELLED"   # 취소(43번 — 물리삭제 금지)


class DeferTarget(str, enum.Enum):
    PRINCIPAL = "PRINCIPAL"
    PRINCIPAL_INTEREST = "PRINCIPAL_INTEREST"
    INTEREST = "INTEREST"


class LoanEvent(Base, AuditMixin):
    __tablename__ = "loan_event"

    loan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("loan.id"), nullable=False, index=True)
    event_type: Mapped[LoanEventType] = mapped_column(Enum(LoanEventType), nullable=False, index=True)
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[LoanEventStatus] = mapped_column(Enum(LoanEventStatus), default=LoanEventStatus.APPLIED, nullable=False)
    resulting_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repayment_schedule_version.id"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    deferral: Mapped["Deferral | None"] = relationship(back_populates="loan_event", uselist=False)

    def __repr__(self) -> str:
        return f"<LoanEvent {self.event_type} loan={self.loan_id}>"


class Deferral(Base, AuditMixin):
    """
    상환유예 상세. 2단계에서 실데이터(첨부3)로 검증한 알고리즘을 그대로 구현:
    - 접수시점과 일치하는 회차 1건을 동결
    - 동결 총액을 접수일+1년부터 입력된 주기·횟수로 분할 회수(정규 회차에 가산)
    - 회수 중인 잔여 동결원금에 매 회차 별도 단리이자 부과
    - 마지막 회수회차에서 자동보정
    """

    __tablename__ = "deferral"

    loan_event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("loan_event.id"), nullable=False)
    frozen_schedule_row_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repayment_schedule_row.id"), nullable=False
    )
    defer_target: Mapped[DeferTarget] = mapped_column(Enum(DeferTarget), nullable=False)
    frozen_principal_total: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    frozen_interest_total: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    recovery_cycle_months: Mapped[int] = mapped_column(Integer, nullable=False)  # 3=분기, 6=반기
    recovery_count: Mapped[int] = mapped_column(Integer, nullable=False)
    recovery_start_date: Mapped[date] = mapped_column(Date, nullable=False)

    loan_event: Mapped["LoanEvent"] = relationship(back_populates="deferral")
    recovery_rows: Mapped[list["DeferralRecoveryRow"]] = relationship(
        back_populates="deferral", order_by="DeferralRecoveryRow.installment_seq", cascade="all, delete-orphan"
    )


class DeferralRecoveryRow(Base, AuditMixin):
    __tablename__ = "deferral_recovery_row"

    deferral_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("deferral.id"), nullable=False, index=True)
    schedule_row_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repayment_schedule_row.id"), nullable=False
    )
    installment_seq: Mapped[int] = mapped_column(Integer, nullable=False)  # 1~N
    recovery_principal: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    recovery_interest: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    additional_interest_on_frozen_principal: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    deferral: Mapped["Deferral"] = relationship(back_populates="recovery_rows")


class Acceleration(Base, AuditMixin):
    """
    원금가속상환 상세. 첨부4·5(실사례 + 업무절차)로 확정한 규칙:
    - 시작시점 = 접수일 이후 첫 정규 상환예정일
    - 시작시점부터 매 회차 원금 = ROUND(기존 스케줄 원금 × 가속배율)
    - 시작시점부터 이자는 재원과 무관하게 단리방식으로 전환
    - 잔여원금이 가속상환액 이하가 되는 회차에서 잔액 전액으로 마감, 이후 회차 삭제
    """

    __tablename__ = "acceleration"

    loan_event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("loan_event.id"), nullable=False)
    start_installment_no: Mapped[int] = mapped_column(Integer, nullable=False)
    start_due_date: Mapped[date] = mapped_column(Date, nullable=False)
    acceleration_multiplier: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)  # 기본 1.5(가변)
    closing_installment_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    original_total_installments: Mapped[int] = mapped_column(Integer, nullable=False)


class EarlyRepayment(Base, AuditMixin):
    """조기상환 상세(§22/§23). 일부: 상환기간 유지/정기상환액 재계산(확정). 수수료는 건별 입력(기본 0)."""

    __tablename__ = "early_repayment"

    loan_event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("loan_event.id"), nullable=False)
    repay_type: Mapped[str] = mapped_column(String(10), nullable=False)  # PARTIAL / FULL
    repay_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fee_amount: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    balance_before: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_after: Mapped[int] = mapped_column(BigInteger, nullable=False)
    applied_after_installment_no: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Refinancing(Base, AuditMixin):
    """대환대출 상세(§25). 전액: 같은 원장 승계(재원·금리만 교체). 일부: 전환/잔류금액은 담당자 입력."""

    __tablename__ = "refinancing"

    loan_event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("loan_event.id"), nullable=False)
    refi_type: Mapped[str] = mapped_column(String(10), nullable=False)  # FULL / PARTIAL
    new_fund_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("fund.id"), nullable=False)
    converted_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    remaining_amount: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    rejoin_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    new_annual_rate: Mapped[float] = mapped_column(Numeric(8, 5), nullable=False)
    old_fund_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("fund.id"), nullable=True)


class EventRequestStatus(str, enum.Enum):
    REQUESTED = "REQUESTED"   # 결재 대기
    APPROVED = "APPROVED"     # 승인 → 적용 완료
    REJECTED = "REJECTED"     # 반려
    WITHDRAWN = "WITHDRAWN"   # 기안자 회수


class EventRequest(Base, AuditMixin):
    """
    중도변수 이벤트 기안(§45 내부통제: 등록과 승인 분리). EVENT_APPROVAL_REQUIRED=true일 때만 쓰인다.
    입력값(payload)을 그대로 보관했다가, 기안자가 아닌 결재자가 승인하는 순간 즉시 적용과 같은 계산으로 원리금 테이블을 만든다.
    """
    __tablename__ = "event_request"

    loan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("loan.id"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(20), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[EventRequestStatus] = mapped_column(Enum(EventRequestStatus), default=EventRequestStatus.REQUESTED,
                                                       nullable=False, index=True)
    requested_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("app_user.id"), nullable=False)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("app_user.id"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resulting_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    resulting_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


# 원리금 테이블 행에 '이 행에서 회수되는 상환유예 원금'을 함께 싣는다(같은 SELECT 안의 부분합 — 추가 쿼리 없음).
# 화면·Excel에서 원금상환액 = 정규 원금 + 유예 회수원금 이 되어 '상환 전 잔액 - 원금상환액 = 상환 후 잔액'이 그대로 보인다.
from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.orm import column_property  # noqa: E402

from app.models.schedule import RepaymentScheduleRow  # noqa: E402

RepaymentScheduleRow.deferral_recovery_principal = column_property(
    select(func.coalesce(func.sum(DeferralRecoveryRow.recovery_principal), 0))
    .where(DeferralRecoveryRow.schedule_row_id == RepaymentScheduleRow.id)
    .correlate_except(DeferralRecoveryRow)
    .scalar_subquery()
)
