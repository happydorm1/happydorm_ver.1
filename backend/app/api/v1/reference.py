import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user, require_roles
from app.domain.audit import record_audit
from app.models.organization import User, UserRole
from app.models.reference import Code, Fund
from app.schemas.reference import CodeCreate, CodeRead, FundCreate, FundRead

router = APIRouter(prefix="/reference", tags=["기준정보"])

ADMIN_ONLY = (UserRole.SYSTEM_ADMIN,)


@router.get("/funds", response_model=list[FundRead])
def list_funds(include_inactive: bool = False, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    stmt = select(Fund).where(Fund.is_deleted.is_(False))
    if not include_inactive:
        stmt = stmt.where(Fund.is_active.is_(True))
    return db.execute(stmt).scalars().all()


@router.post("/funds", response_model=FundRead, status_code=201)
def create_fund(
    payload: FundCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*ADMIN_ONLY)),
):
    """44번 요구사항: 새로운 재원을 코드 수정 없이 추가."""
    existing = db.execute(select(Fund).where(Fund.fund_name == payload.fund_name)).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="이미 등록된 재원명입니다.")
    fund = Fund(**payload.model_dump(), created_by=current_user.id)
    db.add(fund)
    db.commit()
    db.refresh(fund)
    return fund


@router.get("/codes", response_model=list[CodeRead])
def list_codes(
    group_code: str | None = None,
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    stmt = select(Code).where(Code.is_deleted.is_(False))
    if not include_inactive:
        stmt = stmt.where(Code.is_active.is_(True))
    if group_code:
        stmt = stmt.where(Code.group_code == group_code)
    stmt = stmt.order_by(Code.group_code, Code.sort_order)
    return db.execute(stmt).scalars().all()


@router.post("/codes", response_model=CodeRead, status_code=201)
def create_code(
    payload: CodeCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*ADMIN_ONLY)),
):
    """44번 요구사항: 새로운 상환주기/상환유형/거치유형을 코드 수정 없이 추가."""
    existing = db.execute(
        select(Code).where(Code.group_code == payload.group_code, Code.code == payload.code)
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="이미 등록된 코드입니다.")
    code = Code(**payload.model_dump(), created_by=current_user.id)
    db.add(code)
    db.commit()
    db.refresh(code)
    return code



@router.post("/funds/{fund_id}/active", response_model=FundRead)
def set_fund_active(fund_id: uuid.UUID, value: bool, db: Session = Depends(get_db),
                    current_user: User = Depends(require_roles(*ADMIN_ONLY))):
    """§43: 삭제 대신 사용중지/재사용. 사용중지된 재원은 새 차입금 등록 목록에서만 빠지고 기존 차입금은 그대로."""
    f = db.get(Fund, fund_id)
    if f is None or f.is_deleted:
        raise HTTPException(status_code=404, detail="재원을 찾을 수 없습니다.")
    record_audit(db, table_name="fund", record_id=f.id, loan_id=None, action="UPDATE", user_id=current_user.id,
                 field="is_active", before=f.is_active, after=value)
    f.is_active = value
    db.commit()
    db.refresh(f)
    return f


@router.post("/codes/{code_id}/active", response_model=CodeRead)
def set_code_active(code_id: uuid.UUID, value: bool, db: Session = Depends(get_db),
                    current_user: User = Depends(require_roles(*ADMIN_ONLY))):
    c = db.get(Code, code_id)
    if c is None or c.is_deleted:
        raise HTTPException(status_code=404, detail="코드를 찾을 수 없습니다.")
    record_audit(db, table_name="code", record_id=c.id, loan_id=None, action="UPDATE", user_id=current_user.id,
                 field="is_active", before=c.is_active, after=value)
    c.is_active = value
    db.commit()
    db.refresh(c)
    return c
