"""
Phase 17: 증빙자료 첨부(§21·22 '근거·첨부자료·증빙자료')와 상환일 메모(§20 '사용자가 직접 비고 추가').
- 파일은 DB가 아니라 ATTACHMENT_DIR에 AES-256-GCM으로 암호화해 저장하고, DB에는 목록·해시만 둔다.
- 삭제하지 않는다: 잘못 올린 파일은 '사용중지'(사유 기록) — 파일과 기록은 남는다(§43).
"""
import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import AuditMixin, Base

OWNER_TYPES = ("LOAN", "LOAN_EVENT", "EVENT_REQUEST", "PAYMENT")


class Attachment(Base, AuditMixin):
    __tablename__ = "attachment"

    loan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("loan.id"), nullable=False, index=True)
    owner_type: Mapped[str] = mapped_column(String(20), nullable=False)          # LOAN / LOAN_EVENT / EVENT_REQUEST / PAYMENT
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)          # 올린 사람이 보던 원래 이름
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)             # 원본 파일 해시(위·변조 확인)
    storage_key: Mapped[str] = mapped_column(String(80), nullable=False)        # 저장 파일명(무작위) — 결재 승인 시 같은 파일을 이벤트에 연결
    encrypted: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(300), nullable=True)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deactivated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    deactivate_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class ScheduleNote(Base, AuditMixin):
    """
    상환일 메모(사용자 비고). 원리금 테이블 행은 버전마다 새로 만들어지므로 '차입금 + 상환예정일'에 붙여
    어느 버전을 보든 그 상환일에 함께 보인다. 몇 번째 버전에서 썼는지도 남긴다. 수정은 새 메모 + 이전 메모 사용중지.
    """
    __tablename__ = "schedule_note"

    loan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("loan.id"), nullable=False, index=True)
    due_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    written_on_version_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deactivated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
