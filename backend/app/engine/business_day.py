"""
영업일 계산(§31) — 토·일 및 등록된 공휴일/은행 휴무일을 제외한다. DB를 모르는 순수 함수.

D-N 규칙: 상환예정일 이전의 'N번째 영업일'. 달력일이 아니라 영업일을 센다.
  예) 상환일이 월요일이면 D-1 = 직전 금요일.
확정된 업무기준(재단): 상환일이 휴일이면 그 다음 날로 납부기한을 지정한다. '다음 날'이 다시 휴일일 수 있어
(예: 토요일 → 일요일) 실제로는 휴일이 끝나는 '다음 영업일'로 해석해 구현했다 — 해석 확인 필요.
알림·미납 판단·공문 납부기한은 모두 이 '납부기한'(effective_due_date)을 기준으로 한다.
"""
from datetime import date, timedelta

REPAYMENT_THRESHOLDS = (15, 10, 5, 3, 1)
_MAX_SCAN = 400


def is_business_day(d: date, holidays: set[date]) -> bool:
    return d.weekday() < 5 and d not in holidays


def non_business_reason(d: date, holidays: set[date]) -> str | None:
    if d.weekday() == 5:
        return "토요일"
    if d.weekday() == 6:
        return "일요일"
    if d in holidays:
        return "공휴일/은행 휴무일"
    return None


def subtract_business_days(d: date, n: int, holidays: set[date]) -> date:
    """d 이전의 n번째 영업일(n>=0). n=0이면 d 이전(포함)에서 가장 가까운 영업일."""
    cur, left, guard = d, n, 0
    if n == 0:
        return prev_business_day(d, holidays)
    while left > 0:
        cur -= timedelta(days=1)
        guard += 1
        if guard > _MAX_SCAN:
            raise ValueError("영업일 계산 범위를 초과했습니다. 공휴일 데이터를 확인하세요.")
        if is_business_day(cur, holidays):
            left -= 1
    return cur


def next_business_day(d: date, holidays: set[date]) -> date:
    """d 당일 포함, 이후 첫 영업일."""
    cur = d
    for _ in range(_MAX_SCAN):
        if is_business_day(cur, holidays):
            return cur
        cur += timedelta(days=1)
    raise ValueError("영업일 계산 범위를 초과했습니다. 공휴일 데이터를 확인하세요.")


def prev_business_day(d: date, holidays: set[date]) -> date:
    """d 당일 포함, 이전 첫 영업일."""
    cur = d
    for _ in range(_MAX_SCAN):
        if is_business_day(cur, holidays):
            return cur
        cur -= timedelta(days=1)
    raise ValueError("영업일 계산 범위를 초과했습니다. 공휴일 데이터를 확인하세요.")


def effective_due_date(due: date, holidays: set[date]) -> date:
    """납부기한: 상환예정일이 영업일이면 그대로, 휴일(주말 포함)이면 다음 영업일."""
    return next_business_day(due, holidays)


def count_business_days(start_exclusive: date, end_inclusive: date, holidays: set[date]) -> int:
    """start 다음 날부터 end까지(end 포함)의 영업일 수. end <= start면 0."""
    n, cur = 0, start_exclusive
    while cur < end_inclusive:
        cur += timedelta(days=1)
        if is_business_day(cur, holidays):
            n += 1
    return n


def current_threshold(due: date, as_of: date, holidays: set[date], thresholds=REPAYMENT_THRESHOLDS) -> int | None:
    """as_of 시점에 이미 도래한 알림 기준 중 가장 임박한(가장 작은) N. 아직 첫 기준(D-15) 전이면 None."""
    passed = [n for n in thresholds if subtract_business_days(due, n, holidays) <= as_of]
    return min(passed) if passed else None
