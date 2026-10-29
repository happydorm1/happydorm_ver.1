"""Phase 13: 대시보드 API — KPI(§34) · 업무 알림(§35) · 검색조건(§36) · 월별 상환예정 · 재원별 현황 · 차입금 목록."""
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.domain.dashboard_service import build_dashboard
from app.domain.loan_filters import LoanFilters
from app.models.loan import LoanStatus, RateType
from app.models.organization import SPC, Dormitory, User, UserRole
from app.models.reference import Code, Fund

router = APIRouter(prefix="/dashboard", tags=["대시보드"])


def _filters(
    dormitory_id: uuid.UUID | None = None,
    school: str | None = None,
    spc_id: uuid.UUID | None = None,
    manager_user_id: uuid.UUID | None = None,
    fund_id: uuid.UUID | None = None,
    rate_type: RateType | None = None,
    repay_method_code_id: uuid.UUID | None = None,
    repay_cycle_code_id: uuid.UUID | None = None,
    status: list[LoanStatus] = Query(default=[]),
    has_rate_change: bool | None = None,
    has_midterm_event: bool | None = None,
    period_from: date | None = None,
    period_to: date | None = None,
) -> LoanFilters:
    if (period_from is None) != (period_to is None):
        raise HTTPException(status_code=422, detail="기간은 시작일과 종료일을 함께 지정해야 합니다.")
    if period_from and period_to and period_from > period_to:
        raise HTTPException(status_code=422, detail="기간 시작일이 종료일보다 늦습니다.")
    return LoanFilters(dormitory_id=dormitory_id, school=school, spc_id=spc_id, manager_user_id=manager_user_id,
                       fund_id=fund_id, rate_type=rate_type, repay_method_code_id=repay_method_code_id,
                       repay_cycle_code_id=repay_cycle_code_id, statuses=list(status), has_rate_change=has_rate_change,
                       has_midterm_event=has_midterm_event, period_from=period_from, period_to=period_to)


@router.get("")
def get_dashboard(as_of: date | None = None, filters: LoanFilters = Depends(_filters),
                  db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """대시보드 한 화면에 필요한 데이터를 한 번에. SPC 담당자는 자기 SPC 차입금만 집계된다."""
    return build_dashboard(db, current_user, as_of or date.today(), filters)


@router.get("/filter-options")
def filter_options(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """검색조건 드롭다운 목록 — 사용자 권한 범위 안의 값만."""
    dstmt = select(Dormitory).where(Dormitory.is_deleted.is_(False))
    if current_user.role == UserRole.SPC_MANAGER:
        dstmt = dstmt.where(Dormitory.spc_id == current_user.spc_id) if current_user.spc_id else dstmt.where(Dormitory.id.is_(None))
    dorms = db.execute(dstmt.order_by(Dormitory.dormitory_name)).scalars().all()
    spc_ids = {d.spc_id for d in dorms}
    manager_ids = {d.manager_user_id for d in dorms if d.manager_user_id}
    spcs = db.execute(select(SPC).where(SPC.id.in_(spc_ids)).order_by(SPC.spc_name)).scalars().all() if spc_ids else []
    managers = db.execute(select(User).where(User.id.in_(manager_ids)).order_by(User.name)).scalars().all() if manager_ids else []
    funds = db.execute(select(Fund).where(Fund.is_deleted.is_(False), Fund.is_active.is_(True)).order_by(Fund.fund_name)).scalars().all()
    codes = db.execute(select(Code).where(Code.is_deleted.is_(False), Code.is_active.is_(True),
                                          Code.group_code.in_(["REPAY_METHOD", "REPAY_CYCLE"]))
                       .order_by(Code.group_code, Code.sort_order)).scalars().all()
    return {
        "dormitories": [{"id": d.id, "name": d.dormitory_name, "school": d.school_name} for d in dorms],
        "schools": sorted({d.school_name for d in dorms}),
        "spcs": [{"id": s.id, "name": s.spc_name} for s in spcs],
        "managers": [{"id": u.id, "name": u.name} for u in managers],
        "funds": [{"id": x.id, "name": x.fund_name} for x in funds],
        "rate_types": [{"value": "FIXED", "label": "고정금리"}, {"value": "VARIABLE", "label": "변동금리"}],
        "repay_methods": [{"id": c.id, "label": c.label} for c in codes if c.group_code == "REPAY_METHOD"],
        "repay_cycles": [{"id": c.id, "label": c.label} for c in codes if c.group_code == "REPAY_CYCLE"],
        "statuses": [{"value": "ACTIVE", "label": "진행중"}, {"value": "CLOSED", "label": "종료"},
                     {"value": "REFINANCED_OUT", "label": "대환종료"}],
    }
