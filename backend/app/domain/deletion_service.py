"""
Phase 18c: 관리자 삭제(숨김)·복구.

물리삭제는 하지 않는다(금융 업무 감사 추적). 삭제하면 is_deleted=True로 모든 화면·대시보드·보고서·알림·공문에서 빠지고,
DeletionRecord에 무엇을 함께 숨겼는지 남겨 그대로 복구한다. 변경이력(audit_log)에도 DELETE/RESTORE로 남는다.

- 기숙사: 기숙사 + 소속 차입금 전부(그 차입금의 원리금 테이블·납부·공문은 차입금·기숙사를 통해 함께 안 보임)
- 차입금: 차입금 1건. 차입금 코드는 DB에서 고유값이라, 같은 코드를 다시 등록할 수 있도록 삭제 중에는 코드 뒤에 표식을 붙이고 복구 때 되돌린다
- 원리금 테이블: 그 차입금의 모든 버전 → 조건 수정 후 다시 생성 가능(새 버전 번호는 이어서 매김)
- 안내공문: 발급본 1건(+그 공문의 SPC 담당자 알림)
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.audit import record_audit
from app.models.document import GeneratedDocument
from app.models.event import EventRequest, EventRequestStatus, LoanEvent, LoanEventStatus
from app.models.loan import Loan
from app.models.notification import Notification
from app.models.organization import DeletionRecord, Dormitory, User
from app.models.schedule import RepaymentScheduleVersion

LABEL = {"DORMITORY": "기숙사", "LOAN": "차입금", "SCHEDULE": "원리금 테이블", "NOTICE": "안내공문"}


class DeletionError(Exception):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def _now():
    return datetime.now(timezone.utc)


def _pending_requests(db: Session, loan_ids: list) -> list[EventRequest]:
    if not loan_ids:
        return []
    return list(db.execute(select(EventRequest).where(EventRequest.loan_id.in_(loan_ids), EventRequest.is_deleted.is_(False),
                                                      EventRequest.status == EventRequestStatus.REQUESTED)).scalars())


def _ensure_no_pending(db: Session, loans: list[Loan]) -> None:
    pend = _pending_requests(db, [l.id for l in loans])
    if pend:
        codes = sorted({l.loan_code for l in loans if l.id in {p.loan_id for p in pend}})
        raise DeletionError(f"결재 대기 중인 기안이 있습니다({', '.join(codes)}). 먼저 승인·반려·회수한 뒤 삭제하세요.")


def _hide_loan(db: Session, loan: Loan, rec_id: uuid.UUID, user_id, reason: str) -> dict:
    original = loan.loan_code
    tag = "~" + rec_id.hex[:7]
    loan.loan_code = original[: 30 - len(tag)] + tag      # 삭제 중에는 코드를 비워 같은 코드로 새로 등록할 수 있게
    loan.is_deleted = True
    loan.updated_by = user_id
    record_audit(db, table_name="loan", record_id=loan.id, loan_id=loan.id, action="DELETE", user_id=user_id,
                 field="is_deleted", before=False, after=True, reason=reason)
    return {"id": str(loan.id), "code": original}


def _unhide_loan(db: Session, info: dict, user_id, reason: str) -> None:
    loan = db.get(Loan, uuid.UUID(info["id"]))
    if loan is None:
        return
    taken = db.execute(select(Loan).where(Loan.loan_code == info["code"], Loan.id != loan.id)).scalar_one_or_none()
    if taken:
        raise DeletionError(f"차입금 코드 '{info['code']}'를 지금 다른 차입금이 쓰고 있어 복구할 수 없습니다. 그 차입금의 코드를 바꾸거나 삭제한 뒤 복구하세요.")
    loan.loan_code = info["code"]
    loan.is_deleted = False
    loan.updated_by = user_id
    record_audit(db, table_name="loan", record_id=loan.id, loan_id=loan.id, action="RESTORE", user_id=user_id,
                 field="is_deleted", before=True, after=False, reason=reason)


def _record(db: Session, target_type: str, target_id, label: str, reason: str, detail: dict, user_id) -> DeletionRecord:
    rec = DeletionRecord(id=uuid.uuid4(), target_type=target_type, target_id=target_id, label=label[:300],
                         reason=reason, detail=detail, created_by=user_id)
    return rec


def _reason(reason: str) -> str:
    reason = (reason or "").strip()
    if not reason:
        raise DeletionError("삭제 사유를 입력하세요.", 422)
    return reason[:300]


# ---------- 삭제 ----------
def delete_dormitory(db: Session, dormitory_id: uuid.UUID, reason: str, user: User) -> DeletionRecord:
    reason = _reason(reason)
    dorm = db.get(Dormitory, dormitory_id)
    if dorm is None or dorm.is_deleted:
        raise DeletionError("기숙사를 찾을 수 없습니다.", 404)
    loans = list(db.execute(select(Loan).where(Loan.dormitory_id == dorm.id, Loan.is_deleted.is_(False))).scalars())
    _ensure_no_pending(db, loans)
    rec = _record(db, "DORMITORY", dorm.id, f"{dorm.dormitory_name} ({dorm.school_name})", reason, {}, user.id)
    hidden = [_hide_loan(db, l, rec.id, user.id, f"기숙사 삭제: {reason}") for l in loans]
    dorm.is_deleted = True
    dorm.updated_by = user.id
    record_audit(db, table_name="dormitory", record_id=dorm.id, loan_id=None, action="DELETE", user_id=user.id,
                 field="is_deleted", before=False, after=True, reason=reason)
    rec.detail = {"loans": hidden}
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return rec


def delete_loan(db: Session, loan_id: uuid.UUID, reason: str, user: User) -> DeletionRecord:
    reason = _reason(reason)
    loan = db.get(Loan, loan_id)
    if loan is None or loan.is_deleted:
        raise DeletionError("차입금을 찾을 수 없습니다.", 404)
    _ensure_no_pending(db, [loan])
    dorm = db.get(Dormitory, loan.dormitory_id)
    rec = _record(db, "LOAN", loan.id, f"{loan.loan_code} · {dorm.dormitory_name if dorm else ''}", reason, {}, user.id)
    rec.detail = {"loans": [_hide_loan(db, loan, rec.id, user.id, reason)]}
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return rec


def delete_schedule(db: Session, loan_id: uuid.UUID, reason: str, user: User) -> DeletionRecord:
    reason = _reason(reason)
    loan = db.get(Loan, loan_id)
    if loan is None or loan.is_deleted:
        raise DeletionError("차입금을 찾을 수 없습니다.", 404)
    versions = list(db.execute(select(RepaymentScheduleVersion).where(RepaymentScheduleVersion.loan_id == loan.id,
                                                                      RepaymentScheduleVersion.is_deleted.is_(False))).scalars())
    if not versions:
        raise DeletionError("삭제할 원리금 테이블이 없습니다.", 404)
    applied = db.execute(select(LoanEvent).where(LoanEvent.loan_id == loan.id, LoanEvent.status == LoanEventStatus.APPLIED)).scalars().first()
    if applied:
        raise DeletionError("적용된 중도변수 이벤트가 있어 원리금 테이블을 삭제할 수 없습니다. 이벤트 관리에서 최근 것부터 취소한 뒤 삭제하세요.")
    _ensure_no_pending(db, [loan])
    rec = _record(db, "SCHEDULE", loan.id, f"{loan.loan_code} 원리금 테이블 V{', V'.join(str(v.version_no) for v in sorted(versions, key=lambda v: v.version_no))}",
                  reason, {}, user.id)
    rec.detail = {"versions": [{"id": str(v.id), "is_current": v.is_current} for v in versions]}
    for v in versions:
        v.is_deleted = True
        v.is_current = False
    record_audit(db, table_name="repayment_schedule_version", record_id=loan.id, loan_id=loan.id, action="DELETE",
                 user_id=user.id, field="versions", before=len(versions), after=0, reason=reason)
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return rec


def delete_notice(db: Session, notice_id: uuid.UUID, reason: str, user: User) -> DeletionRecord:
    reason = _reason(reason)
    doc = db.get(GeneratedDocument, notice_id)
    if doc is None or doc.is_deleted:
        raise DeletionError("공문을 찾을 수 없습니다.", 404)
    notes = [n for n in db.execute(select(Notification).where(Notification.notification_type == "NOTICE_ISSUED",
                                                               Notification.is_deleted.is_(False))).scalars()
             if (n.payload or {}).get("notice_id") == str(doc.id)]
    rec = _record(db, "NOTICE", doc.id, f"{doc.title} (상환일 {doc.due_date})", reason,
                  {"notifications": [str(n.id) for n in notes]}, user.id)
    doc.is_deleted = True
    for n in notes:
        n.is_deleted = True
    for src in (doc.generated_data or {}).get("sources", []):
        record_audit(db, table_name="generated_document", record_id=doc.id, loan_id=uuid.UUID(src["loan_id"]), action="DELETE",
                     user_id=user.id, field="title", before=doc.title, after=None, reason=reason)
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return rec


# ---------- 복구 ----------
def restore(db: Session, record_id: uuid.UUID, user: User) -> DeletionRecord:
    rec = db.get(DeletionRecord, record_id)
    if rec is None or rec.is_deleted:
        raise DeletionError("삭제 기록을 찾을 수 없습니다.", 404)
    if rec.restored_at:
        raise DeletionError("이미 복구했습니다.")
    why = f"복구: {rec.reason}"
    d = rec.detail or {}
    if rec.target_type == "DORMITORY":
        dorm = db.get(Dormitory, rec.target_id)
        for info in d.get("loans", []):
            _unhide_loan(db, info, user.id, why)
        dorm.is_deleted = False
        record_audit(db, table_name="dormitory", record_id=dorm.id, loan_id=None, action="RESTORE", user_id=user.id,
                     field="is_deleted", before=True, after=False, reason=why)
    elif rec.target_type == "LOAN":
        loan = db.get(Loan, rec.target_id)
        dorm = db.get(Dormitory, loan.dormitory_id) if loan else None
        if dorm is None or dorm.is_deleted:
            raise DeletionError("이 차입금의 기숙사가 삭제되어 있습니다. 기숙사를 먼저 복구하세요.")
        for info in d.get("loans", []):
            _unhide_loan(db, info, user.id, why)
    elif rec.target_type == "SCHEDULE":
        loan = db.get(Loan, rec.target_id)
        if loan is None or loan.is_deleted:
            raise DeletionError("이 원리금 테이블의 차입금이 삭제되어 있습니다. 차입금(또는 기숙사)을 먼저 복구하세요.")
        live = db.execute(select(RepaymentScheduleVersion).where(RepaymentScheduleVersion.loan_id == loan.id,
                                                                 RepaymentScheduleVersion.is_deleted.is_(False))).scalars().first()
        if live:
            raise DeletionError("이 차입금에 지금 쓰는 원리금 테이블이 있어 복구할 수 없습니다. 현재 테이블을 먼저 삭제한 뒤 복구하세요.")
        for info in d.get("versions", []):
            v = db.get(RepaymentScheduleVersion, uuid.UUID(info["id"]))
            if v:
                v.is_deleted = False
                v.is_current = bool(info.get("is_current"))
        record_audit(db, table_name="repayment_schedule_version", record_id=loan.id, loan_id=loan.id, action="RESTORE",
                     user_id=user.id, field="versions", before=0, after=len(d.get("versions", [])), reason=why)
    elif rec.target_type == "NOTICE":
        doc = db.get(GeneratedDocument, rec.target_id)
        dorm = db.get(Dormitory, doc.dormitory_id) if doc else None
        if dorm is None or dorm.is_deleted:
            raise DeletionError("이 공문의 기숙사가 삭제되어 있습니다. 기숙사를 먼저 복구하세요.")
        doc.is_deleted = False
        for nid in d.get("notifications", []):
            n = db.get(Notification, uuid.UUID(nid))
            if n:
                n.is_deleted = False
        for src in (doc.generated_data or {}).get("sources", []):
            record_audit(db, table_name="generated_document", record_id=doc.id, loan_id=uuid.UUID(src["loan_id"]),
                         action="RESTORE", user_id=user.id, field="title", before=None, after=doc.title, reason=why)
    rec.restored_at = _now()
    rec.restored_by = user.id
    db.commit()
    db.refresh(rec)
    return rec
