"""증빙자료 저장·조회(Phase 17). 파일 본문은 AES-256-GCM으로 암호화해 ATTACHMENT_DIR에 둔다(§45 '파일 저장 시 AES-256')."""
import hashlib
import mimetypes
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.crypto import CryptoError, decrypt_bytes, encrypt_bytes
from app.domain.audit import record_audit
from app.models.attachment import OWNER_TYPES, Attachment

# 실행 파일·스크립트는 확장자와 상관없이 막는다(첫 바이트 확인)
BLOCKED_MAGIC = (b"MZ", b"\x7fELF", b"#!")


class AttachmentError(Exception):
    pass


def allowed_extensions() -> set[str]:
    return {e.strip().lower().lstrip(".") for e in settings.ATTACHMENT_EXTENSIONS.split(",") if e.strip()}


def _dir() -> Path:
    d = Path(settings.ATTACHMENT_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_name(name: str) -> str:
    name = Path(name.replace("\\", "/")).name.strip() or "file"
    return "".join(ch for ch in name if ch not in '<>:"|?*\x00')[:200]


def validate(file_name: str, data: bytes) -> str:
    ext = Path(file_name).suffix.lower().lstrip(".")
    if ext not in allowed_extensions():
        raise AttachmentError(f"올릴 수 없는 형식입니다(.{ext or '확장자 없음'}). 허용: {', '.join(sorted(allowed_extensions()))}")
    if not data:
        raise AttachmentError("빈 파일입니다.")
    if len(data) > settings.ATTACHMENT_MAX_MB * 1024 * 1024:
        raise AttachmentError(f"파일이 너무 큽니다(최대 {settings.ATTACHMENT_MAX_MB}MB).")
    if any(data.startswith(m) for m in BLOCKED_MAGIC):
        raise AttachmentError("실행 파일은 올릴 수 없습니다.")
    return ext


def save(db: Session, *, loan_id, owner_type: str, owner_id, file_name: str, data: bytes, description: str | None,
         user_id) -> Attachment:
    if owner_type not in OWNER_TYPES:
        raise AttachmentError("첨부 대상이 올바르지 않습니다.")
    name = _safe_name(file_name)
    validate(name, data)
    key = uuid.uuid4().hex
    (_dir() / key).write_bytes(encrypt_bytes(data))
    a = Attachment(loan_id=loan_id, owner_type=owner_type, owner_id=owner_id, file_name=name,
                   content_type=mimetypes.guess_type(name)[0] or "application/octet-stream", size_bytes=len(data),
                   sha256=hashlib.sha256(data).hexdigest(), storage_key=key, encrypted=True,
                   description=(description or "").strip()[:300] or None, created_by=user_id)
    db.add(a)
    db.flush()
    record_audit(db, table_name="attachment", record_id=a.id, loan_id=loan_id, action="CREATE", user_id=user_id,
                 field=owner_type, after=f"{name} ({len(data):,}바이트)")
    db.commit()
    db.refresh(a)
    return a


def read(a: Attachment) -> bytes:
    path = _dir() / a.storage_key
    if not path.exists():
        raise AttachmentError("저장된 파일을 찾을 수 없습니다(첨부 폴더 백업·복구 확인 필요).")
    try:
        data = decrypt_bytes(path.read_bytes()) if a.encrypted else path.read_bytes()
    except CryptoError as e:
        raise AttachmentError("파일을 복호화할 수 없습니다(손상되었거나 암호화 키가 다름).") from e
    if hashlib.sha256(data).hexdigest() != a.sha256:
        raise AttachmentError("파일이 손상되었습니다(해시 불일치).")
    return data


def deactivate(db: Session, a: Attachment, reason: str, user_id) -> Attachment:
    if a.deactivated_at:
        raise AttachmentError("이미 사용중지된 첨부입니다.")
    a.deactivated_at, a.deactivated_by, a.deactivate_reason = datetime.now(timezone.utc), user_id, reason
    record_audit(db, table_name="attachment", record_id=a.id, loan_id=a.loan_id, action="CANCEL", user_id=user_id,
                 field="deactivated", before=a.file_name, after="사용중지", reason=reason)
    db.commit()
    db.refresh(a)
    return a


def link_to(db: Session, sources: list[Attachment], owner_type: str, owner_id, user_id) -> int:
    """결재 승인 시: 기안에 붙은 증빙을 만들어진 이벤트에도 연결(같은 파일을 가리키는 새 목록 행, 파일 복사 없음)."""
    n = 0
    for s in sources:
        if s.deactivated_at:
            continue
        db.add(Attachment(loan_id=s.loan_id, owner_type=owner_type, owner_id=owner_id, file_name=s.file_name,
                          content_type=s.content_type, size_bytes=s.size_bytes, sha256=s.sha256, storage_key=s.storage_key,
                          encrypted=s.encrypted, description=s.description, created_by=user_id,
                          remark=f"기안 첨부에서 연결({s.id})"))
        n += 1
    return n


def notes_by_due_date(db: Session, loan_ids) -> dict:
    """Excel 출력용: {(loan_id, 상환일): '메모1 / 메모2'} — 사용중지 메모 제외."""
    from sqlalchemy import select
    from app.models.attachment import ScheduleNote
    ids = list(loan_ids)
    out: dict = {}
    if not ids:
        return out
    for n in db.execute(select(ScheduleNote).where(ScheduleNote.loan_id.in_(ids), ScheduleNote.is_deleted.is_(False),
                                                   ScheduleNote.deactivated_at.is_(None)).order_by(ScheduleNote.created_at)).scalars():
        k = (n.loan_id, n.due_date)
        out[k] = (out[k] + " / " if k in out else "") + n.note
    return out


def remark_with_note(base: str, note: str | None) -> str:
    return f"{base} | 메모: {note}" if note else base
