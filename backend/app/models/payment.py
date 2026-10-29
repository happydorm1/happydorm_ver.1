"""
실제 납부이력(§27) — 예정(원리금 테이블)과 실제를 분리해서 관리한다.

원리금 테이블은 이벤트마다 새 버전이 생겨 행 ID가 바뀌므로, 실제 납부는 행 ID가 아니라
(차입금, 상환예정일)에 연결한다. 그래야 버전이 바뀌어도 납부이력이 끊기지 않는다.
한 상환예정일에 여러 번 나눠 납부할 수 있다(누적 합계로 상태를 판단).
"""
import enum
import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, Enum, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import AuditMixin, Base


class PaymentStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    CANCELLED = "CANCELLED"  # 잘못 입력한 납부는 삭제하지 않고 취소 이력으로 남긴다(43번)


class ActualPayment(Base, AuditMixin):
    __tablename__ = "actual_payment"

    loan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("loan.id"), nullable=False, index=True)
    due_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    actual_paid_date: Mapped[date] = mapped_column(Date, nullable=False)
    actual_principal: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    actual_interest: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    status: Mapped[PaymentStatus] = mapped_column(Enum(PaymentStatus), default=PaymentStatus.ACTIVE, nullable=False)
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    @property
    def actual_total(self) -> int:
        return self.actual_principal + self.actual_interest
