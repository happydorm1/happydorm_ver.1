"""
중도변수 이벤트 결재(§45 내부통제) — EVENT_APPROVAL_REQUIRED=true일 때 이벤트 등록 API는 즉시 적용하지 않고 '기안'만 만든다.
  기안(자금담당·관리자) → 승인(결재권자·자금담당·관리자, 단 기안자 본인 불가) → 그 순간 원리금 테이블 새 버전 생성
  반려(사유 필수) / 회수(기안자 본인)
결재 대기 건은 '중도변수 미처리'로 대시보드·로그인 팝업에 나온다.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.access import get_accessible_loan
from app.core.database import get_db
from app.core.security import require_roles
from app.domain.audit import record_audit
from app.domain.event_dispatch import APPLY_ERRORS, LABELS, apply_from_payload
from app.models.event import EventRequest, EventRequestStatus
from app.models.loan import Loan, LoanStatus
from app.models.organization import Dormitory, User, UserRole

router = APIRouter(prefix="/event-requests", tags=["이벤트 결재"])
FOUNDATION = (UserRole.SYSTEM_ADMIN, UserRole.FOUNDATION_FUND_MANAGER, UserRole.FOUNDATION_APPROVER)
APPROVERS = FOUNDATION


def _out(db: Session, r: EventRequest) -> dict:
    loan = db.get(Loan, r.loan_id)
    dorm = db.get(Dormitory, loan.dormitory_id) if loan else None
    names = {u.id: u.name for u in db.execute(select(User).where(User.id.in_([x for x in (r.requested_by, r.decided_by) if x]))).scalars()}
    return {"id": r.id, "loan_id": r.loan_id, "loan_code": loan.loan_code if loan else None,
            "dormitory_name": dorm.dormitory_name if dorm else None, "event_type": r.event_type,
            "event_label": LABELS.get(r.event_type, r.event_type), "payload": r.payload, "status": r.status.value,
            "requested_by": r.requested_by, "requested_by_name": names.get(r.requested_by), "requested_at": r.created_at,
            "decided_by": r.decided_by, "decided_by_name": names.get(r.decided_by), "decided_at": r.decided_at,
            "decision_note": r.decision_note, "resulting_event_id": r.resulting_event_id,
            "resulting_version_id": r.resulting_version_id, "last_error": r.last_error}


def submit_request(db: Session, loan_id: uuid.UUID, event_type: str, payload: BaseModel, user: User) -> JSONResponse:
    """이벤트 등록 API가 결재 모드일 때 호출 — 202와 기안 내용을 돌려준다(원리금 테이블은 아직 그대로)."""
    loan = get_accessible_loan(db, loan_id, user)
    if loan.status != LoanStatus.ACTIVE:
        raise HTTPException(status_code=422, detail="진행 중인 차입금만 기안할 수 있습니다.")
    pending = db.execute(select(EventRequest).where(EventRequest.loan_id == loan_id,
                                                   EventRequest.status == EventRequestStatus.REQUESTED)).scalars().first()
    if pending:
        raise HTTPException(status_code=409, detail="이 차입금에 결재 대기 중인 기안이 있습니다. 먼저 승인·반려·회수하세요.")
    r = EventRequest(loan_id=loan_id, event_type=event_type, payload=jsonable_encoder(payload),
                     requested_by=user.id, created_by=user.id)
    db.add(r)
    db.flush()
    record_audit(db, table_name="event_request", record_id=r.id, loan_id=loan_id, action="CREATE", user_id=user.id,
                 field="status", after=f"{LABELS[event_type]} 기안(결재 대기)")
    db.commit()
    db.refresh(r)
    return JSONResponse(status_code=202, content=jsonable_encoder({"approval_required": True, "request": _out(db, r)}))


def _get(db: Session, request_id: uuid.UUID, user: User) -> EventRequest:
    r = db.get(EventRequest, request_id)
    if r is None or r.is_deleted:
        raise HTTPException(status_code=404, detail="기안을 찾을 수 없습니다.")
    get_accessible_loan(db, r.loan_id, user)
    return r


def _require_pending(r: EventRequest):
    if r.status != EventRequestStatus.REQUESTED:
        raise HTTPException(status_code=409, detail=f"이미 처리된 기안입니다({r.status.value}).")


@router.get("")
def list_requests(status: EventRequestStatus | None = EventRequestStatus.REQUESTED, loan_id: uuid.UUID | None = None,
                  limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db),
                  current_user: User = Depends(require_roles(*FOUNDATION))):
    q = (select(EventRequest).join(Loan, Loan.id == EventRequest.loan_id)          # 삭제(숨김)된 차입금의 기안은 숨김
         .where(EventRequest.is_deleted.is_(False), Loan.is_deleted.is_(False)))
    if status:
        q = q.where(EventRequest.status == status)
    if loan_id:
        q = q.where(EventRequest.loan_id == loan_id)
    return [_out(db, r) for r in db.execute(q.order_by(EventRequest.created_at.desc()).limit(limit)).scalars()]


@router.get("/{request_id}")
def get_request(request_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(require_roles(*FOUNDATION))):
    return _out(db, _get(db, request_id, current_user))


class Decision(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


@router.post("/{request_id}/approve")
def approve(request_id: uuid.UUID, body: Decision | None = None, db: Session = Depends(get_db),
            current_user: User = Depends(require_roles(*APPROVERS))):
    r = _get(db, request_id, current_user)
    _require_pending(r)
    if r.requested_by == current_user.id:
        raise HTTPException(status_code=403, detail="기안자 본인은 승인할 수 없습니다(등록·승인 분리).")
    requester, loan_id, etype, payload = r.requested_by, r.loan_id, r.event_type, r.payload
    try:
        event_id, version_id = apply_from_payload(db, etype, loan_id, payload, requester)
    except APPLY_ERRORS as e:
        db.rollback()
        r = db.get(EventRequest, request_id)
        r.last_error = str(e)
        db.commit()
        raise HTTPException(status_code=422, detail=f"적용할 수 없어 결재 대기로 남겼습니다: {e}")
    r = db.get(EventRequest, request_id)
    _link_request_attachments(db, r, event_id, version_id, current_user.id)
    r.status, r.decided_by, r.decided_at = EventRequestStatus.APPROVED, current_user.id, datetime.now(timezone.utc)
    r.decision_note, r.resulting_event_id, r.resulting_version_id, r.last_error = (body.note if body else None), event_id, version_id, None
    record_audit(db, table_name="event_request", record_id=r.id, loan_id=loan_id, action="APPROVE", user_id=current_user.id,
                 field="status", before="REQUESTED", after="APPROVED", reason=r.decision_note)
    db.commit()
    db.refresh(r)
    return _out(db, r)


def _link_request_attachments(db: Session, r: EventRequest, event_id, version_id, user_id) -> None:
    """기안에 붙은 증빙을 만들어진 이벤트에도 연결(Phase 17). 금리변동처럼 event_id가 없으면 새 버전을 만든 이벤트를 찾는다."""
    from sqlalchemy import select as _select
    from app.domain.attachment_service import link_to
    from app.models.attachment import Attachment
    from app.models.event import LoanEvent
    srcs = db.execute(_select(Attachment).where(Attachment.owner_type == "EVENT_REQUEST", Attachment.owner_id == r.id,
                                                Attachment.is_deleted.is_(False), Attachment.deactivated_at.is_(None))).scalars().all()
    if not srcs:
        return
    if event_id is None and version_id is not None:
        event_id = db.execute(_select(LoanEvent.id).where(LoanEvent.loan_id == r.loan_id, LoanEvent.resulting_version_id == version_id)
                              .order_by(LoanEvent.created_at.desc())).scalars().first()
    if event_id is not None:
        link_to(db, srcs, "LOAN_EVENT", event_id, user_id)


@router.post("/{request_id}/reject")
def reject(request_id: uuid.UUID, body: Decision, db: Session = Depends(get_db),
           current_user: User = Depends(require_roles(*APPROVERS))):
    if not (body.note or "").strip():
        raise HTTPException(status_code=422, detail="반려 사유를 입력하세요.")
    r = _get(db, request_id, current_user)
    _require_pending(r)
    r.status, r.decided_by, r.decided_at, r.decision_note = EventRequestStatus.REJECTED, current_user.id, datetime.now(timezone.utc), body.note
    record_audit(db, table_name="event_request", record_id=r.id, loan_id=r.loan_id, action="REJECT", user_id=current_user.id,
                 field="status", before="REQUESTED", after="REJECTED", reason=body.note)
    db.commit()
    db.refresh(r)
    return _out(db, r)


@router.post("/{request_id}/withdraw")
def withdraw(request_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(require_roles(*FOUNDATION))):
    r = _get(db, request_id, current_user)
    _require_pending(r)
    if r.requested_by != current_user.id:
        raise HTTPException(status_code=403, detail="기안자 본인만 회수할 수 있습니다.")
    r.status, r.decided_by, r.decided_at = EventRequestStatus.WITHDRAWN, current_user.id, datetime.now(timezone.utc)
    record_audit(db, table_name="event_request", record_id=r.id, loan_id=r.loan_id, action="WITHDRAW", user_id=current_user.id,
                 field="status", before="REQUESTED", after="WITHDRAWN")
    db.commit()
    db.refresh(r)
    return _out(db, r)
