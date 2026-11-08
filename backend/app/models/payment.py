"""
실제 납부이력(§27) — 예정(원리금 테이블)과 실제를 분리해서 관리한다.

원리금 테이블은 이벤트마다 새 버전이 생겨 행 ID가 바뀌므로, 실제 납부는 행 ID가 아니라
(차입금, 상환예정일)에 연결한다. 그래야 버전이 바뀌어도 납부이력이 끊기지 않는다.
한 상환예정일에 여러 번 나눠 납부할 수 있다(누적 합계로 상태를 판단).
"""
import enum
import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
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


class DepositStatus(str, enum.Enum):
    PENDING = "PENDING"        # 대기 — 담당자 확인·승인 필요
    COMPLETED = "COMPLETED"    # 완료 — 자동 승인 또는 자금담당자 승인(납부 기록 생성)
    EXCLUDED = "EXCLUDED"      # 제외 — 원리금 상환이 아닌 입금 등(사유 필수)


class BankDeposit(Base, AuditMixin):
    """
    은행 입금내역(Phase 23) — 거래내역 파일 업로드 또는 직접 입력.
    발급한 안내공문과 대사(짝짓기)해, 공문 합계 2,000만 원 미만이고 1원까지 같으며 납부기한 안이면 자동 승인,
    그 밖에는 '대기'로 두고 재단 자금담당자가 승인한다. 완료되면 공문의 차입금별 원금·이자로 납부 기록(ActualPayment)을 만든다.
    """

    __tablename__ = "bank_deposit"

    deposit_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    depositor: Mapped[str] = mapped_column(String(100), nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    bank_memo: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="UPLOAD", nullable=False)     # UPLOAD / MANUAL
    file_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    dup_key: Mapped[str] = mapped_column(String(400), nullable=False, index=True)          # 같은 거래 중복 업로드 방지
    notice_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("generated_document.id"), nullable=True, index=True)
    status: Mapped[DepositStatus] = mapped_column(Enum(DepositStatus), default=DepositStatus.PENDING, nullable=False, index=True)
    hold_reasons: Mapped[list | None] = mapped_column(JSONB, nullable=True)                # 대기 사유 코드
    auto_approved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    manual_done: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)       # 수동 처리 완료(납부 기록은 상환 실적 관리에서 직접)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    payment_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)                 # 승인으로 만든 납부 기록(취소 시 함께 취소)
