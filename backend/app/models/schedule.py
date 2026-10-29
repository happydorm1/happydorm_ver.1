"""
2단계 ERD 설계서 '5. 원리금 테이블' 섹션 구현.
"""
import enum
import uuid
from datetime import date

from sqlalchemy import BigInteger, Boolean, Date, Enum, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import AuditMixin, Base


class ScheduleVersionStatus(str, enum.Enum):
    NORMAL = "NORMAL"              # 정상
    NEEDS_REVIEW = "NEEDS_REVIEW"  # 검토필요 (§37 자동검증 실패)


class RowType(str, enum.Enum):
    GRACE = "GRACE"
    NORMAL = "NORMAL"
    YEAR_END_SPLIT_MEMO = "YEAR_END_SPLIT_MEMO"
    YEAR_END_SPLIT_REAL = "YEAR_END_SPLIT_REAL"
    CLOSING = "CLOSING"
    DEFERRAL_RECOVERY = "DEFERRAL_RECOVERY"  # 정규 상환주기와 무관하게 유예회수를 위해 추가된 행


class RepaymentScheduleVersion(Base, AuditMixin):
    __tablename__ = "repayment_schedule_version"

    loan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("loan.id"), nullable=False, index=True)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    generated_reason: Mapped[str] = mapped_column(String(30), nullable=False)
    # 최초생성/금리변동/조기상환/유예/가속상환/대환
    previous_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repayment_schedule_version.id"), nullable=True
    )
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    status: Mapped[ScheduleVersionStatus] = mapped_column(
        Enum(ScheduleVersionStatus), default=ScheduleVersionStatus.NORMAL, nullable=False
    )
    validation_errors: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    rows: Mapped[list["RepaymentScheduleRow"]] = relationship(
        back_populates="version", order_by="RepaymentScheduleRow.seq_no", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<ScheduleVersion loan={self.loan_id} v{self.version_no}>"


class RepaymentScheduleRow(Base, AuditMixin):
    __tablename__ = "repayment_schedule_row"

    version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repayment_schedule_version.id"), nullable=False, index=True
    )
    seq_no: Mapped[int] = mapped_column(Integer, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    interest_calc_start: Mapped[date] = mapped_column(Date, nullable=False)
    interest_calc_end: Mapped[date] = mapped_column(Date, nullable=False)
    days: Mapped[int] = mapped_column(Integer, nullable=False)
    installment_no: Mapped[int | None] = mapped_column(Integer, nullable=True)

    balance_before: Mapped[int] = mapped_column(BigInteger, nullable=False)
    principal_amount: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    interest_amount: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    unpaid_interest_amount: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    balance_after: Mapped[int] = mapped_column(BigInteger, nullable=False)
    applied_rate: Mapped[float] = mapped_column(Numeric(8, 5), nullable=False)

    row_type: Mapped[RowType] = mapped_column(Enum(RowType), nullable=False)
    memo_interest: Mapped[float | None] = mapped_column(Numeric(16, 4), nullable=True)

    version: Mapped["RepaymentScheduleVersion"] = relationship(back_populates="rows")

    @property
    def interest_total(self) -> int:
        return self.interest_amount + self.unpaid_interest_amount

    @property
    def principal_and_interest_total(self) -> int:
        return self.principal_amount + (getattr(self, "deferral_recovery_principal", 0) or 0) + self.interest_total

    @property
    def principal_total(self) -> int:
        """원금상환액 = 정규 원금 + 이 행에서 회수하는 상환유예 원금(models/event.py의 부분합)."""
        return self.principal_amount + (getattr(self, "deferral_recovery_principal", 0) or 0)

    def __repr__(self) -> str:
        return f"<Row {self.due_date} P={self.principal_amount} I={self.interest_amount}>"
