"""
알림(§32~33). 발송 이력과 수신함을 한 테이블로 관리한다 — 누구에게 언제 무슨 내용을 보냈는지가 곧 이력이다.
같은 (수신자, 차입금, 알림종류, 기준일)은 한 번만 만들 수 있다 → 매일 배치를 돌려도 중복 발송이 없다.
현재 채널은 시스템 내 알림(IN_APP)뿐이다. 이메일/문자 등 외부 발송은 기관의 서버·보안 환경 확인 후
같은 레코드를 읽어 보내는 어댑터로 붙인다(§46).
"""
import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import AuditMixin, Base


class Notification(Base, AuditMixin):
    __tablename__ = "notification"
    __table_args__ = (
        UniqueConstraint("recipient_user_id", "loan_id", "notification_type", "reference_date", name="uq_notification_once"),
    )

    recipient_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("app_user.id"), nullable=False, index=True)
    loan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("loan.id"), nullable=False, index=True)
    dormitory_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("dormitory.id"), nullable=False)
    notification_type: Mapped[str] = mapped_column(String(20), nullable=False)  # REPAYMENT_D15.. / RATE_CHANGE_D30
    reference_date: Mapped[date] = mapped_column(Date, nullable=False)           # 상환예정일 또는 금리변동 예정일
    trigger_date: Mapped[date] = mapped_column(Date, nullable=False)             # 알림이 만들어진 기준일(as_of)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    channel: Mapped[str] = mapped_column(String(10), default="IN_APP", nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PopupAck(Base, AuditMixin):
    """
    알림 팝업 '확인' 기록. 확인한 항목은 정해진 기간(expires_at)까지 팝업에서 빼고, 그때까지 해결되지 않았으면 다시 띄운다.
    (중도변수 미처리: 다음 날 다시 / 관리자 시스템 안내: 7일 뒤 다시). 해결되면 항목 자체가 사라진다.
    """
    __tablename__ = "popup_ack"
    __table_args__ = (UniqueConstraint("user_id", "item_key", name="uq_popup_ack"),)

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("app_user.id"), nullable=False, index=True)
    item_key: Mapped[str] = mapped_column(String(200), nullable=False)
    acked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
