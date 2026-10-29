"""안내공문(§28~29) — 양식 기준정보와 발급 이력(스냅샷)."""
import uuid
from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import AuditMixin, Base


class DocumentTemplate(Base, AuditMixin):
    """
    공문 양식. 입금계좌처럼 기관이 정하는 값은 코드에 넣지 않고 양식 설정(config)으로 관리한다(44번).
    config 예: {"bank_account": "...", "account_holder": "..."}
    """

    __tablename__ = "document_template"

    template_name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    template_type: Mapped[str] = mapped_column(String(30), default="REPAYMENT_NOTICE", nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class GeneratedDocument(Base, AuditMixin):
    """
    발급된 공문. 발급 당시의 값을 스냅샷(generated_data)으로 보존한다 —
    이후 원리금 테이블이 바뀌어도 이미 발송한 공문 내용은 그대로 재현된다.
    """

    __tablename__ = "generated_document"

    dormitory_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("dormitory.id"), nullable=False, index=True)
    template_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("document_template.id"), nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    payment_deadline: Mapped[date] = mapped_column(Date, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    generated_data: Mapped[dict] = mapped_column(JSONB, nullable=False)
