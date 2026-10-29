import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import require_roles
from app.models.organization import SPC, User, UserRole
from app.schemas.organization import SPCCreate, SPCRead, SPCUpdate

router = APIRouter(prefix="/spc", tags=["SPC"])

WRITE_ROLES = (UserRole.SYSTEM_ADMIN, UserRole.FOUNDATION_FUND_MANAGER)
READ_ROLES = tuple(UserRole)  # 전체 역할 조회 가능


@router.get("", response_model=list[SPCRead])
def list_spc(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*READ_ROLES)),
):
    stmt = select(SPC).where(SPC.is_deleted.is_(False)).order_by(SPC.spc_name)
    if current_user.role == UserRole.SPC_MANAGER:          # 자기 SPC만
        stmt = stmt.where(SPC.id == current_user.spc_id)
    return db.execute(stmt).scalars().all()


@router.post("", response_model=SPCRead, status_code=201)
def create_spc(
    payload: SPCCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*WRITE_ROLES)),
):
    spc = SPC(**payload.model_dump(), created_by=current_user.id)
    db.add(spc)
    db.commit()
    db.refresh(spc)
    return spc


@router.get("/{spc_id}", response_model=SPCRead)
def get_spc(
    spc_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*READ_ROLES)),
):
    spc = db.get(SPC, spc_id)
    if spc is None or spc.is_deleted:
        raise HTTPException(status_code=404, detail="SPC를 찾을 수 없습니다.")
    if current_user.role == UserRole.SPC_MANAGER and spc.id != current_user.spc_id:
        raise HTTPException(status_code=403, detail="해당 SPC에 대한 접근 권한이 없습니다.")
    return spc


@router.put("/{spc_id}", response_model=SPCRead)
def update_spc(
    spc_id: uuid.UUID,
    payload: SPCUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*WRITE_ROLES)),
):
    spc = db.get(SPC, spc_id)
    if spc is None or spc.is_deleted:
        raise HTTPException(status_code=404, detail="SPC를 찾을 수 없습니다.")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(spc, field, value)
    spc.updated_by = current_user.id
    db.commit()
    db.refresh(spc)
    return spc


@router.delete("/{spc_id}", status_code=204)
def deactivate_spc(
    spc_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.SYSTEM_ADMIN)),
):
    """물리삭제 금지 원칙 — is_deleted 플래그만 변경(사용중지)."""
    spc = db.get(SPC, spc_id)
    if spc is None or spc.is_deleted:
        raise HTTPException(status_code=404, detail="SPC를 찾을 수 없습니다.")
    spc.is_deleted = True
    spc.updated_by = current_user.id
    db.commit()
