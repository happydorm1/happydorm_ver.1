import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user, require_roles
from app.models.organization import Dormitory, User, UserRole
from app.schemas.organization import DormitoryCreate, DormitoryRead, DormitoryUpdate

router = APIRouter(prefix="/dormitories", tags=["기숙사"])

WRITE_ROLES = (UserRole.SYSTEM_ADMIN, UserRole.FOUNDATION_FUND_MANAGER)


@router.get("", response_model=list[DormitoryRead])
def list_dormitories(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    stmt = select(Dormitory).where(Dormitory.is_deleted.is_(False))

    # Row-level 권한: SPC 담당자는 자신의 SPC 소속 기숙사만 조회 가능 (ERD 설계 4-2 원칙)
    if current_user.role == UserRole.SPC_MANAGER:
        if current_user.spc_id is None:
            return []
        stmt = stmt.where(Dormitory.spc_id == current_user.spc_id)

    stmt = stmt.order_by(Dormitory.dormitory_name)
    return db.execute(stmt).scalars().all()


@router.post("", response_model=DormitoryRead, status_code=201)
def create_dormitory(
    payload: DormitoryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*WRITE_ROLES)),
):
    dormitory = Dormitory(**payload.model_dump(), created_by=current_user.id)
    db.add(dormitory)
    db.commit()
    db.refresh(dormitory)
    return dormitory


@router.get("/{dormitory_id}", response_model=DormitoryRead)
def get_dormitory(
    dormitory_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    dormitory = db.get(Dormitory, dormitory_id)
    if dormitory is None or dormitory.is_deleted:
        raise HTTPException(status_code=404, detail="기숙사를 찾을 수 없습니다.")

    if (
        current_user.role == UserRole.SPC_MANAGER
        and dormitory.spc_id != current_user.spc_id
    ):
        raise HTTPException(status_code=403, detail="해당 기숙사에 대한 접근 권한이 없습니다.")

    return dormitory


@router.put("/{dormitory_id}", response_model=DormitoryRead)
def update_dormitory(
    dormitory_id: uuid.UUID,
    payload: DormitoryUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*WRITE_ROLES)),
):
    dormitory = db.get(Dormitory, dormitory_id)
    if dormitory is None or dormitory.is_deleted:
        raise HTTPException(status_code=404, detail="기숙사를 찾을 수 없습니다.")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(dormitory, field, value)
    dormitory.updated_by = current_user.id
    db.commit()
    db.refresh(dormitory)
    return dormitory


@router.delete("/{dormitory_id}", status_code=204)
def close_dormitory(
    dormitory_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.SYSTEM_ADMIN)),
):
    """사업종료 처리 — 물리삭제 대신 상태값 변경."""
    from app.models.organization import DormitoryStatus

    dormitory = db.get(Dormitory, dormitory_id)
    if dormitory is None or dormitory.is_deleted:
        raise HTTPException(status_code=404, detail="기숙사를 찾을 수 없습니다.")
    dormitory.status = DormitoryStatus.CLOSED
    dormitory.updated_by = current_user.id
    db.commit()
