"""공휴일 데이터 로딩과 '공휴일 미등록' 점검(§35 확인필요 항목)."""
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.calendar import Holiday


def load_holidays(db: Session) -> set[date]:
    return set(db.execute(select(Holiday.holiday_date).where(Holiday.is_deleted.is_(False))).scalars().all())


def years_without_holidays(db: Session, years: set[int]) -> list[int]:
    """공휴일이 하나도 등록되지 않은 연도 — 영업일 계산이 주말만 반영되어 부정확해질 수 있다."""
    present = {d.year for d in load_holidays(db)}
    return sorted(y for y in years if y not in present)
