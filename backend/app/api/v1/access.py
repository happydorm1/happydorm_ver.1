"""차입금 단위 접근권한 공통 검사 — SPC 담당자는 자신의 SPC 소속 기숙사의 차입금만 접근 가능."""
import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.loan import Loan
from app.models.organization import Dormitory, User, UserRole


def get_accessible_loan(db: Session, loan_id: uuid.UUID, user: User) -> Loan:
    loan = db.get(Loan, loan_id)
    if loan is None or loan.is_deleted:
        raise HTTPException(status_code=404, detail="차입금을 찾을 수 없습니다.")
    if user.role == UserRole.SPC_MANAGER:
        dorm = db.get(Dormitory, loan.dormitory_id)
        if dorm is None or dorm.spc_id != user.spc_id:
            raise HTTPException(status_code=403, detail="해당 차입금에 대한 접근 권한이 없습니다.")
    return loan
