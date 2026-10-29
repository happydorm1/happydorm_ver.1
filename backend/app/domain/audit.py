"""수정/취소 이력을 명시적으로 남기는 헬퍼 (등록은 models/audit_listeners.py가 자동 기록)."""
import uuid

from sqlalchemy.orm import Session

from app.models.audit import AuditLog


def record_audit(
    db: Session, *, table_name: str, record_id: uuid.UUID, loan_id: uuid.UUID | None, action: str,
    user_id: uuid.UUID | None, field: str | None = None, before=None, after=None, reason: str | None = None,
) -> None:
    db.add(AuditLog(
        table_name=table_name, record_id=record_id, loan_id=loan_id, action=action, changed_field=field,
        before_value=None if before is None else str(before), after_value=None if after is None else str(after),
        changed_by=user_id, change_reason=reason,
    ))
