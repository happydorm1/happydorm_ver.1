"""Phase 11: 안내공문 — 양식 관리, 미리보기, 발급, Excel/PDF 다운로드."""
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
from app.engine.notice_export import PdfUnavailableError, build_notice_workbook, convert_xlsx_to_pdf
from app.models.document import DocumentTemplate, GeneratedDocument
from app.models.organization import Dormitory, User, UserRole

router = APIRouter(tags=["안내공문"])

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


def _template_out(t: DocumentTemplate) -> TemplateRead:
    """양식 조회 시 계좌는 끝 4자리만 보인다(저장은 암호화). 실제 값은 공문 미리보기·발급본에서 확인."""
    cfg = dict(t.config or {})
    if "bank_account" in cfg:
        cfg["bank_account"] = mask_account(cfg["bank_account"])
    return TemplateRead(id=t.id, template_name=t.template_name, template_type=t.template_type, config=cfg, is_active=t.is_active)


def _notice_out(doc: GeneratedDocument) -> NoticeRead:
    data = dict(doc.generated_data)
    data["account"] = reveal_account(data.get("account"))
    return NoticeRead(id=doc.id, dormitory_id=doc.dormitory_id, due_date=doc.due_date, payment_deadline=doc.payment_deadline,
                      title=doc.title, created_at=doc.created_at, created_by=doc.created_by, generated_data=data)


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
    """공문 양식 등록 — 입금계좌·예금주는 코드가 아니라 기준정보로 관리(44번)."""
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
        raise HTTPException(status_code=404, detail="공문 양식을 찾을 수 없습니다.")
    t.is_active = False
    t.updated_by = current_user.id
    db.commit()
    db.refresh(t)
    return _template_out(t)


# ---------- 공문 ----------
@router.get("/dormitories/{dormitory_id}/notices/preview")
def preview_notice(dormitory_id: uuid.UUID, due_date: date, template_id: uuid.UUID | None = None,
                   payment_deadline: date | None = None, db: Session = Depends(get_db),
                   current_user: User = Depends(require_roles(*FOUNDATION_ROLES))):
    """발급 전 미리보기(저장하지 않음). 기숙사 + 상환예정일만 고르면 모든 숫자가 자동으로 채워진다."""
    dorm = _dormitory(db, dormitory_id, current_user)
    try:
        template = get_active_template(db, template_id)
        return build_notice_data(db, dorm, due_date, template, deadline_override=payment_deadline)
    except NoticeError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/dormitories/{dormitory_id}/notices", response_model=NoticeRead, status_code=201)
def create_notice(dormitory_id: uuid.UUID, payload: NoticeRequest, db: Session = Depends(get_db),
                  current_user: User = Depends(require_roles(*WRITE_ROLES))):
    """공문 발급 — 발급 당시 값을 스냅샷으로 저장한다."""
    dorm = _dormitory(db, dormitory_id, current_user)
    try:
        template = get_active_template(db, payload.template_id)
        doc = issue_notice(db, dorm, payload.due_date, template, deadline_override=payload.payment_deadline,
                           account_override={"bank_account": payload.bank_account, "account_holder": payload.account_holder},
                           user_id=current_user.id)
    except NoticeError as e:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(e))
    return _notice_out(doc)


@router.get("/dormitories/{dormitory_id}/notices", response_model=list[NoticeRead])
def list_notices(dormitory_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _dormitory(db, dormitory_id, current_user)
    return [_notice_out(d) for d in db.execute(select(GeneratedDocument).where(GeneratedDocument.dormitory_id == dormitory_id,
                                                                            GeneratedDocument.is_deleted.is_(False))
                                               .order_by(GeneratedDocument.created_at.desc())).scalars().all()]


def _get_notice(db: Session, notice_id: uuid.UUID, user: User) -> GeneratedDocument:
    doc = db.get(GeneratedDocument, notice_id)
    if doc is None or doc.is_deleted:
        raise HTTPException(status_code=404, detail="공문을 찾을 수 없습니다.")
    _dormitory(db, doc.dormitory_id, user)
    return doc


@router.get("/notices/{notice_id}", response_model=NoticeRead)
def get_notice(notice_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _notice_out(_get_notice(db, notice_id, current_user))


@router.get("/notices/{notice_id}/download")
def download_notice(notice_id: uuid.UUID, format: str = "xlsx", db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user)):
    """발급된 공문 내려받기(xlsx 또는 pdf). 항상 발급 당시 스냅샷에서 만들어 몇 번을 받아도 같다."""
    ensure_can_download(current_user)
    doc = _get_notice(db, notice_id, current_user)
    fmt = format.lower()
    if fmt not in ("xlsx", "pdf"):
        raise HTTPException(status_code=422, detail="format은 xlsx 또는 pdf여야 합니다.")
    content = build_notice_workbook(_notice_out(doc).generated_data)
    media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if fmt == "pdf":
        try:
            content = convert_xlsx_to_pdf(content)
        except PdfUnavailableError as e:
            raise HTTPException(status_code=501, detail=str(e))
        media = "application/pdf"
    filename = quote(f"{doc.title}.{fmt}")
    return Response(content=content, media_type=media,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"})
