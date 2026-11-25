"""Phase 11: 안내문 — 양식 관리, 미리보기, 발송, Excel/PDF 다운로드."""
import uuid
from datetime import date, datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import mask_account, protect_account, reveal_account
from app.core.database import get_db
from app.core.security import ensure_can_download, get_current_user, require_roles
from app.domain.notice_service import NoticeError, build_notice_data, get_active_template, issue_notice
from app.engine.notice_export import (PdfUnavailableError, build_notice_workbook, build_notices_workbook, notice_pdf,
                                      notices_pdf)
from app.domain.notice_status import mark_received, notice_targets, notices_issued
from app.models.document import DocumentTemplate, GeneratedDocument
from app.models.organization import Dormitory, User, UserRole

router = APIRouter(tags=["안내문"])

WRITE_ROLES = (UserRole.SYSTEM_ADMIN, UserRole.FOUNDATION_FUND_MANAGER)
FOUNDATION_ROLES = (UserRole.SYSTEM_ADMIN, UserRole.FOUNDATION_FUND_MANAGER, UserRole.FOUNDATION_APPROVER)


class TemplateConfig(BaseModel):
    bank_account: str = Field(..., min_length=3, max_length=100, description="입금계좌(은행명 + 계좌번호)")
    account_holder: str = Field(..., min_length=1, max_length=50, description="예금주")


class TemplateCreate(BaseModel):
    template_name: str = Field(..., max_length=100)
    template_type: str = "REPAYMENT_NOTICE"
    config: TemplateConfig


class TemplateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    template_name: str
    template_type: str
    config: dict
    is_active: bool


class NoticeRequest(BaseModel):
    due_date: date
    template_id: uuid.UUID | None = None
    payment_deadline: date | None = Field(default=None, description="납부기한을 직접 지정(미지정 시 상환예정일)")
    bank_account: str | None = None
    account_holder: str | None = None


class NoticeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    dormitory_id: uuid.UUID
    due_date: date
    payment_deadline: date
    title: str
    created_at: datetime
    created_by: uuid.UUID | None
    generated_data: dict
    notified_users: int = 0                    # 발송 직후 응답에만: 시스템 알림을 받은 SPC 담당자 수
    received_at: datetime | None = None        # 기숙사(SPC 담당자) 수신 시각 — 열람·내려받기·알림 읽음
    received_via: str | None = None


def _template_out(t: DocumentTemplate) -> TemplateRead:
    """양식 조회 시 계좌는 끝 4자리만 보인다(저장은 암호화). 실제 값은 안내문 미리보기·발송본에서 확인."""
    cfg = dict(t.config or {})
    if "bank_account" in cfg:
        cfg["bank_account"] = mask_account(cfg["bank_account"])
    return TemplateRead(id=t.id, template_name=t.template_name, template_type=t.template_type, config=cfg, is_active=t.is_active)


def _notice_out(doc: GeneratedDocument) -> NoticeRead:
    data = dict(doc.generated_data)
    data["account"] = reveal_account(data.get("account"))
    return NoticeRead(id=doc.id, dormitory_id=doc.dormitory_id, due_date=doc.due_date, payment_deadline=doc.payment_deadline,
                      title=doc.title, created_at=doc.created_at, created_by=doc.created_by, generated_data=data,
                      received_at=doc.received_at, received_via=doc.received_via)


def _dormitory(db: Session, dormitory_id: uuid.UUID, user: User) -> Dormitory:
    d = db.get(Dormitory, dormitory_id)
    if d is None or d.is_deleted:
        raise HTTPException(status_code=404, detail="기숙사를 찾을 수 없습니다.")
    if user.role == UserRole.SPC_MANAGER and d.spc_id != user.spc_id:
        raise HTTPException(status_code=403, detail="해당 기숙사에 대한 접근 권한이 없습니다.")
    return d


# ---------- 양식 ----------
@router.post("/document-templates", response_model=TemplateRead, status_code=201)
def create_template(payload: TemplateCreate, db: Session = Depends(get_db),
                    current_user: User = Depends(require_roles(*WRITE_ROLES))):
    """안내문 양식 등록 — 입금계좌·예금주는 코드가 아니라 기준정보로 관리(44번)."""
    if db.execute(select(DocumentTemplate).where(DocumentTemplate.template_name == payload.template_name)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="이미 등록된 양식명입니다.")
    t = DocumentTemplate(template_name=payload.template_name, template_type=payload.template_type,
                         config=protect_account(payload.config.model_dump()), created_by=current_user.id)
    db.add(t)
    db.commit()
    db.refresh(t)
    return _template_out(t)


@router.get("/document-templates", response_model=list[TemplateRead])
def list_templates(db: Session = Depends(get_db), current_user: User = Depends(require_roles(*FOUNDATION_ROLES))):
    return [_template_out(t) for t in db.execute(select(DocumentTemplate).where(DocumentTemplate.is_deleted.is_(False))
                                                 .order_by(DocumentTemplate.created_at.desc())).scalars().all()]


@router.post("/document-templates/{template_id}/deactivate", response_model=TemplateRead)
def deactivate_template(template_id: uuid.UUID, db: Session = Depends(get_db),
                        current_user: User = Depends(require_roles(*WRITE_ROLES))):
    """삭제 대신 사용중지(43번)."""
    t = db.get(DocumentTemplate, template_id)
    if t is None or t.is_deleted:
        raise HTTPException(status_code=404, detail="안내문 양식을 찾을 수 없습니다.")
    t.is_active = False
    t.updated_by = current_user.id
    db.commit()
    db.refresh(t)
    return _template_out(t)


# ---------- 안내문 ----------
@router.get("/dormitories/{dormitory_id}/notices/preview")
def preview_notice(dormitory_id: uuid.UUID, due_date: date, template_id: uuid.UUID | None = None,
                   payment_deadline: date | None = None, db: Session = Depends(get_db),
                   current_user: User = Depends(require_roles(*FOUNDATION_ROLES))):
    """발송 전 미리보기(저장하지 않음). 기숙사 + 상환예정일만 고르면 모든 숫자가 자동으로 채워진다."""
    dorm = _dormitory(db, dormitory_id, current_user)
    try:
        template = get_active_template(db, template_id)
        return build_notice_data(db, dorm, due_date, template, deadline_override=payment_deadline)
    except NoticeError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/dormitories/{dormitory_id}/notices", response_model=NoticeRead, status_code=201)
def create_notice(dormitory_id: uuid.UUID, payload: NoticeRequest, db: Session = Depends(get_db),
                  current_user: User = Depends(require_roles(*WRITE_ROLES))):
    """안내문 발송 — 발송 당시 값을 스냅샷으로 저장한다."""
    dorm = _dormitory(db, dormitory_id, current_user)
    try:
        template = get_active_template(db, payload.template_id)
        doc = issue_notice(db, dorm, payload.due_date, template, deadline_override=payload.payment_deadline,
                           account_override={"bank_account": payload.bank_account, "account_holder": payload.account_holder},
                           user_id=current_user.id)
    except NoticeError as e:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(e))
    out = _notice_out(doc)
    out.notified_users = getattr(doc, "notified_count", 0)
    return out


@router.get("/dormitories/{dormitory_id}/notices", response_model=list[NoticeRead])
def list_notices(dormitory_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _dormitory(db, dormitory_id, current_user)
    return [_notice_out(d) for d in db.execute(select(GeneratedDocument).where(GeneratedDocument.dormitory_id == dormitory_id,
                                                                            GeneratedDocument.is_deleted.is_(False))
                                               .order_by(GeneratedDocument.created_at.desc())).scalars().all()]


def _get_notice(db: Session, notice_id: uuid.UUID, user: User) -> GeneratedDocument:
    doc = db.get(GeneratedDocument, notice_id)
    if doc is None or doc.is_deleted:
        raise HTTPException(status_code=404, detail="안내문을 찾을 수 없습니다.")
    _dormitory(db, doc.dormitory_id, user)
    return doc


# ---------- Phase 21: 원리금 상환 › 안내문 ----------
@router.get("/notices/targets")
def list_notice_targets(as_of: date | None = None, date_from: date | None = None, date_to: date | None = None,
                        include_issued: bool = False, dormitory_id: uuid.UUID | None = None, db: Session = Depends(get_db),
                        current_user: User = Depends(require_roles(*FOUNDATION_ROLES))):
    """발송대상 현황 — 기본은 납부기한 영업일 D-15 이내 회차 중 안내문 미발송(기숙사·상환예정일 단위)."""
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="기간 시작일이 종료일보다 늦습니다.")
    return notice_targets(db, current_user, as_of or date.today(), date_from=date_from, date_to=date_to,
                          include_issued=include_issued, dormitory_id=dormitory_id)


@router.get("/notices/issued")
def list_notices_issued(date_from: date | None = None, date_to: date | None = None, received: bool | None = None,
                        dormitory_id: uuid.UUID | None = None, paid: str | None = None, db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_user)):
    """발송완료 — 기숙사·상환예정일별 최근 발송본과 수신 확인 여부(SPC 담당자는 자기 기숙사만)."""
    return notices_issued(db, current_user, date_from=date_from, date_to=date_to, received=received, dormitory_id=dormitory_id, paid=paid)



BULK_MAX = 300
_PAY_LABEL = {"PAID": "입금 완료", "CHECKING": "확인 중", "PARTIAL": "일부 입금", "OVERDUE": "미입금 · 기한 경과", "UNPAID": "입금 전"}
_PAY_VIA = {"AUTO": "자동 승인", "APPROVED": "승인", "MANUAL": "수동 처리", "PAYMENT": "실적 입력"}


def _kst(dt) -> str:
    if not dt:
        return ""
    if dt.tzinfo is not None:
        from zoneinfo import ZoneInfo
        dt = dt.astimezone(ZoneInfo("Asia/Seoul"))
    return dt.strftime("%Y-%m-%d %H:%M")


class BulkDownloadIn(BaseModel):
    ids: list[uuid.UUID] = Field(..., min_length=1, description="내려받을 안내문 id(화면 순서대로)")
    format: str = "xlsx"
    label: str = Field(default="", max_length=200, description="조회조건 설명(목록 시트 부제)")


@router.post("/notices/bulk-download")
def bulk_download_notices(body: BulkDownloadIn, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    발송완료 조회 결과 전체를 한 파일로(2026-11-25).
    xlsx: 첫 시트 '목록' + 안내문 1건당 시트 1장(개별 Excel과 같은 양식) / pdf: 안내문 1건당 1페이지씩 이어 붙인 한 파일.
    권한은 발송완료 목록과 같다(SPC 담당자는 자기 기숙사만). 목록에 없는 id(다시 발송돼 밀려난 이전 발송본 등)는 오류.
    """
    ensure_can_download(current_user)
    fmt = body.format.lower()
    if fmt not in ("xlsx", "pdf"):
        raise HTTPException(status_code=422, detail="format은 xlsx 또는 pdf여야 합니다.")
    ids = list(dict.fromkeys(body.ids))
    if len(ids) > BULK_MAX:
        raise HTTPException(status_code=422, detail=f"한 번에 {BULK_MAX}건까지 내려받을 수 있습니다. 기간·기숙사를 좁혀 조회하세요.")
    rows = {r["notice_id"]: r for r in notices_issued(db, current_user)["items"]}
    missing = [i for i in ids if i not in rows]
    if missing:
        raise HTTPException(status_code=404, detail=f"내려받을 수 없는 안내문이 {len(missing)}건 있습니다. 다시 조회한 뒤 받으세요.")
    items = []
    for i in ids:
        r, doc = dict(rows[i]), db.get(GeneratedDocument, i)
        r["issued_at"] = _kst(r.get("issued_at"))
        r["received_label"] = (f"수신 확인 ({r.get('received_by_name') or ''} {_kst(r.get('received_at'))})".replace("( ", "(")
                               if r["received"] else "미수신")
        pl = _PAY_LABEL.get(r.get("pay_status") or "", "-")
        via = _PAY_VIA.get(r.get("pay_via") or "")
        r["pay_label"] = f"{pl} ({via})" if r.get("pay_status") == "PAID" and via else pl
        items.append((r, _notice_out(doc).generated_data))
    stamp = date.today().strftime("%Y%m%d")
    if fmt == "xlsx":
        content = build_notices_workbook(items, subtitle=body.label or f"{len(items)}건 · 내려받은 날 {date.today()}")
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        try:
            content = notices_pdf(items)
        except PdfUnavailableError as e:
            raise HTTPException(status_code=501, detail=str(e))
        media = "application/pdf"
    for doc_id in ids:                                   # 기숙사 측(SPC 담당자)이 내려받으면 수신(재단 직원은 기록 안 함)
        mark_received(db, db.get(GeneratedDocument, doc_id), current_user, "DOWNLOAD")
    filename = quote(f"발송완료_안내문_{len(items)}건_{stamp}.{fmt}")
    return Response(content=content, media_type=media,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"})


@router.get("/notices/{notice_id}", response_model=NoticeRead)
def get_notice(notice_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    doc = _get_notice(db, notice_id, current_user)
    mark_received(db, doc, current_user, "VIEW")            # 기숙사 측(SPC 담당자)이 열람하면 수신
    return _notice_out(doc)


@router.get("/notices/{notice_id}/download")
def download_notice(notice_id: uuid.UUID, format: str = "xlsx", db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user)):
    """발송된 안내문 내려받기(xlsx 또는 pdf). 항상 발송 당시 스냅샷에서 만들어 몇 번을 받아도 같다."""
    ensure_can_download(current_user)
    doc = _get_notice(db, notice_id, current_user)
    fmt = format.lower()
    if fmt not in ("xlsx", "pdf"):
        raise HTTPException(status_code=422, detail="format은 xlsx 또는 pdf여야 합니다.")
    data = _notice_out(doc).generated_data
    content = build_notice_workbook(data)
    mark_received(db, doc, current_user, "DOWNLOAD")        # 기숙사 측(SPC 담당자)이 내려받으면 수신
    media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if fmt == "pdf":
        try:
            content = notice_pdf(data, content)
        except PdfUnavailableError as e:
            raise HTTPException(status_code=501, detail=str(e))
        media = "application/pdf"
    filename = quote(f"{doc.title}.{fmt}")
    return Response(content=content, media_type=media,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"})


