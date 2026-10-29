"""Phase 12: 공휴일 기준정보 + 영업일 조회."""
import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user, require_roles
from app.domain.calendar_service import load_holidays, years_without_holidays
from app.domain.holiday_sync import HolidaySyncError, sync_enabled, sync_year
from app.engine.business_day import (
    REPAYMENT_THRESHOLDS, next_business_day, non_business_reason, prev_business_day, subtract_business_days,
)
from app.models.calendar import Holiday, HolidaySyncLog
from app.models.organization import User, UserRole

router = APIRouter(tags=["영업일·공휴일"])

WRITE_ROLES = (UserRole.SYSTEM_ADMIN, UserRole.FOUNDATION_FUND_MANAGER)


class HolidayIn(BaseModel):
    holiday_date: date
    holiday_name: str = Field(..., max_length=80)
    holiday_type: str = Field(default="PUBLIC", pattern="^(PUBLIC|BANK|OTHER)$")


class HolidayBulk(BaseModel):
    items: list[HolidayIn] = Field(..., min_length=1, max_length=500)


class HolidayRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    holiday_date: date
    holiday_name: str
    holiday_type: str
    source: str


class SyncLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    year: int
    synced_at: datetime
    status: str
    fetched: int
    created: int
    updated: int
    deactivated: int
    message: str | None
    triggered_by: str


def _upsert(db: Session, item: HolidayIn, user_id) -> tuple[Holiday, str]:
    existing = db.execute(select(Holiday).where(Holiday.holiday_date == item.holiday_date)).scalar_one_or_none()
    if existing is None:
        h = Holiday(holiday_date=item.holiday_date, holiday_name=item.holiday_name, holiday_type=item.holiday_type, created_by=user_id)
        db.add(h)
        return h, "created"
    if existing.is_deleted:  # 사용중지했던 날짜를 다시 등록 — 새로 만들지 않고 되살린다(43번)
        existing.is_deleted = False
        existing.holiday_name, existing.holiday_type, existing.updated_by = item.holiday_name, item.holiday_type, user_id
        return existing, "reactivated"
    return existing, "exists"


@router.get("/holidays", response_model=list[HolidayRead])
def list_holidays(year: int | None = None, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    stmt = select(Holiday).where(Holiday.is_deleted.is_(False))
    if year:
        stmt = stmt.where(Holiday.holiday_date >= date(year, 1, 1), Holiday.holiday_date <= date(year, 12, 31))
    return db.execute(stmt.order_by(Holiday.holiday_date)).scalars().all()


@router.post("/holidays", response_model=HolidayRead, status_code=201)
def create_holiday(payload: HolidayIn, db: Session = Depends(get_db), current_user: User = Depends(require_roles(*WRITE_ROLES))):
    h, status = _upsert(db, payload, current_user.id)
    if status == "exists":
        raise HTTPException(status_code=409, detail="이미 등록된 날짜입니다.")
    db.commit()
    db.refresh(h)
    return h


@router.post("/holidays/bulk")
def bulk_holidays(payload: HolidayBulk, db: Session = Depends(get_db), current_user: User = Depends(require_roles(*WRITE_ROLES))):
    """연 단위 공휴일을 한꺼번에 등록. 이미 있는 날짜는 건너뛴다."""
    counts = {"created": 0, "reactivated": 0, "exists": 0}
    seen = set()
    for item in payload.items:
        if item.holiday_date in seen:
            continue
        seen.add(item.holiday_date)
        _, status = _upsert(db, item, current_user.id)
        counts[status] += 1
        db.flush()
    db.commit()
    return counts


@router.delete("/holidays/{holiday_id}", status_code=204)
def deactivate_holiday(holiday_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(require_roles(*WRITE_ROLES))):
    """삭제 대신 사용중지(물리삭제 금지)."""
    h = db.get(Holiday, holiday_id)
    if h is None or h.is_deleted:
        raise HTTPException(status_code=404, detail="공휴일을 찾을 수 없습니다.")
    h.is_deleted = True
    h.updated_by = current_user.id
    db.commit()


@router.get("/calendar/business-day")
def business_day_info(date_: date = Query(..., alias="date"), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    holidays = load_holidays(db)
    reason = non_business_reason(date_, holidays)
    return {"date": date_, "is_business_day": reason is None, "reason": reason,
            "next_business_day": next_business_day(date_, holidays), "prev_business_day": prev_business_day(date_, holidays),
            "holidays_missing_for_year": bool(years_without_holidays(db, {date_.year}))}


@router.get("/calendar/repayment-alert-dates")
def repayment_alert_dates(due_date: date, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """상환예정일의 D-15/10/5/3/1 알림 기준일(영업일 기준). 달력일이 아니다."""
    holidays = load_holidays(db)
    return {
        "due_date": due_date,
        "due_date_is_business_day": non_business_reason(due_date, holidays) is None,
        "alert_dates": {f"D-{n}": subtract_business_days(due_date, n, holidays) for n in REPAYMENT_THRESHOLDS},
        "warnings": ([f"{due_date.year}년 공휴일이 등록되어 있지 않아 주말만 반영된 날짜입니다."]
                     if years_without_holidays(db, {due_date.year}) else []),
    }


@router.post("/holidays/sync", response_model=SyncLogRead)
def sync_holidays(year: int = Query(..., ge=2000, le=2100), db: Session = Depends(get_db),
                  current_user: User = Depends(require_roles(*WRITE_ROLES))):
    """공식 공휴일 데이터(한국천문연구원 특일 정보)로 해당 연도 공휴일·대체·임시공휴일 + 은행 휴무 규칙을 반영한다.
    담당자가 직접 입력한 공휴일은 바꾸지 않는다. 호출 실패 시 DB는 그대로이고 FAILED 이력만 남는다."""
    try:
        log = sync_year(db, year, triggered_by=current_user.email)
    except HolidaySyncError as e:
        raise HTTPException(status_code=503, detail=str(e))
    if log.status != "SUCCESS":
        raise HTTPException(status_code=502, detail=f"공휴일 동기화 실패: {log.message}")
    return log


@router.get("/holidays/sync-logs", response_model=list[SyncLogRead])
def sync_logs(year: int | None = None, limit: int = 20, db: Session = Depends(get_db),
              current_user: User = Depends(require_roles(*WRITE_ROLES))):
    stmt = select(HolidaySyncLog)
    if year:
        stmt = stmt.where(HolidaySyncLog.year == year)
    return db.execute(stmt.order_by(HolidaySyncLog.synced_at.desc()).limit(min(max(limit, 1), 200))).scalars().all()


@router.get("/holidays/sync-status")
def sync_status(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return {"enabled": sync_enabled(), "source": "한국천문연구원 특일 정보(공공데이터포털)"}
