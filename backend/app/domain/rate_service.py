"""
금리 조회 로직.
1단계에서 실제 엑셀로 검증한 규칙: 해당 회차의 '이자계산종료일'이 포함되는 기간의
금리를 그 회차 전체에 적용한다(일할 안분 없음). 변경금리가 아직 고시되지 않은
구간(effective_end_date IS NULL인 마지막 이력)은 마지막 known rate를 계속 적용한다.
"""
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.loan import InterestRateHistory


class RateNotRegisteredError(Exception):
    """해당 날짜를 커버하는 금리 이력이 전혀 없는 경우(37번 자동검증의 '금리 미등록' 상태)."""


def get_applicable_rate(db: Session, loan_id, as_of_end_date: date) -> Decimal:
    stmt = (
        select(InterestRateHistory)
        .where(InterestRateHistory.loan_id == loan_id)
        .where(InterestRateHistory.is_deleted.is_(False))
        .where(InterestRateHistory.effective_start_date <= as_of_end_date)
        .order_by(InterestRateHistory.effective_start_date.desc())
    )
    candidate = db.execute(stmt).scalars().first()
    if candidate is None:
        raise RateNotRegisteredError(
            f"{as_of_end_date} 기준 적용 가능한 금리 이력이 없습니다. 금리 등록이 필요합니다."
        )
    return candidate.annual_rate


def rate_history(db: Session, loan_id) -> list:
    """삭제되지 않은 금리 이력(적용시작일 순). 조회 캐시(read_cache) 안이면 미리 읽어 둔 값을 쓴다."""
    cache = db.info.get("read_cache")
    if cache is not None and ("hist", loan_id) in cache:
        return cache[("hist", loan_id)]
    return db.execute(select(InterestRateHistory).where(InterestRateHistory.loan_id == loan_id, InterestRateHistory.is_deleted.is_(False))
                      .order_by(InterestRateHistory.effective_start_date)).scalars().all()


def rate_on(hist: list, as_of_end_date: date):
    """get_applicable_rate와 같은 규칙을 이미 읽은 이력에 적용(시작일이 기준일 이하인 것 중 가장 늦은 것)."""
    cand = [h for h in hist if h.effective_start_date <= as_of_end_date]
    return max(cand, key=lambda h: h.effective_start_date).annual_rate if cand else None
