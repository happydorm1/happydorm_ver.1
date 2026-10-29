"""
원리금 계산엔진 — 화면/DB와 분리된 순수 계산 로직(41번 요구사항).

1단계에서 실제 엑셀(첨부1~7) 대조검증으로 확정한 규칙을 그대로 구현한다:
- 이자기간: 초일산입 말일불산입 (end - start)
- 공자(단리): ROUND(잔여원금 * 일수 * 금리 / 365, 0)
- 주택(PPMT/IPMT): 닫힌 형태(analytic) 공식, 반기 단위
- 회계연도(12/31) 경계는 분리행 생성 — 공자는 양쪽 모두 실지급, 주택은 뒤쪽 행만 실지급(앞은 메모)
- 마지막 회차 자동보정(원단위 오차를 마지막 회차에서 흡수)

이 모듈은 DB를 직접 알지 못한다 — 금리는 rate_lookup(as_of_end_date) 콜백으로 주입받는다.
(domain/schedule_service.py가 Phase2의 rate_service.get_applicable_rate를 이 콜백으로 연결한다)
"""
import datetime
import math
from decimal import ROUND_DOWN, Decimal
from dataclasses import dataclass, field
from typing import Callable, Literal

from dateutil.relativedelta import relativedelta

RateLookup = Callable[[datetime.date], float]


def excel_round(x: float) -> int:
    """Excel ROUND(x,0)와 동일: 0.5는 0에서 먼 쪽으로 올림. (Python excel_round()는 짝수쪽 반올림이라 1~수원 차이 발생)"""
    return int(math.floor(x + 0.5)) if x >= 0 else -int(math.floor(-x + 0.5))


def _eom(d: datetime.date) -> datetime.date:
    next_month = d.replace(day=28) + datetime.timedelta(days=4)
    return next_month - datetime.timedelta(days=next_month.day)


def add_months_eom(d: datetime.date, months: int) -> datetime.date:
    return _eom(d + relativedelta(months=months))


def days_between(start: datetime.date, end: datetime.date) -> int:
    """초일 산입, 말일 불산입 (1단계에서 안내공문 원본으로 확인)."""
    return (end - start).days


def calc_simple_interest(balance: int, days: int, annual_rate: float, rounding: str = "ROUND") -> int:
    """
    ROUND(잔여원금 × 일수 × 금리 / 365)  — rounding="ROUND": Excel ROUND(0.5 올림, 기본)
    rounding="ROUNDDOWN": 원 미만 절사(구 차입금 일부가 Excel ROUNDDOWN 사용 — 첨부6 확인).
    절사는 부동소수 오차로 정수 경계에서 틀리지 않도록 Decimal로 정확히 계산한다.
    """
    if rounding == "ROUNDDOWN":
        exact = Decimal(balance) * days * Decimal(str(annual_rate)) / 100 / 365
        return int(exact.to_integral_value(rounding=ROUND_DOWN))
    return excel_round(balance * days * annual_rate / 100 / 365)


def split_at_year_end(period_start: datetime.date, period_end: datetime.date):
    if period_start.year == period_end.year:
        return [(period_start, period_end)]
    boundary = datetime.date(period_start.year, 12, 31)
    return [(period_start, boundary), (boundary, period_end)]


@dataclass
class ScheduleRow:
    due_date: datetime.date
    interest_calc_start: datetime.date
    interest_calc_end: datetime.date
    days: int
    balance_before: int
    principal_amount: int
    interest_amount: int  # 실제 당기 청구 이자(공자: 분리된 각 구간 실액 / 주택: 실행 회차만)
    balance_after: int
    applied_rate: float
    row_type: Literal["GRACE", "NORMAL", "YEAR_END_SPLIT_MEMO", "YEAR_END_SPLIT_REAL", "CLOSING"]
    memo_interest: float | None = None  # 주택(PPMT) 분리 시 앞쪽 메모행에만 존재
    installment_no: int | None = None
    remark: str | None = None


def _anchor_months_for_freq(months_per_period: int) -> list[int]:
    """재단 상환일은 2/5/8/11월말(분기) 또는 2/8월말(반기)에 고정되는 패턴(1단계 실데이터 확인)."""
    if months_per_period == 3:
        return [2, 5, 8, 11]
    if months_per_period == 6:
        return [2, 8]
    if months_per_period == 1:
        return list(range(1, 13))
    raise ValueError(f"지원하지 않는 상환주기(months_per_period={months_per_period})")


def first_anchor_due_date(start_date: datetime.date, months_per_period: int) -> datetime.date:
    """시작일 이후 가장 가까운 상환주기 anchor 월말일을 찾는다(같은 달도 가능)."""
    anchors = _anchor_months_for_freq(months_per_period)
    candidates = []
    for yy in (start_date.year, start_date.year + 1):
        for m in anchors:
            candidates.append(_eom(datetime.date(yy, m, 1)))
    return min(c for c in candidates if c >= start_date)


def generate_grace_period_rows(
    *,
    assigned_amount: int,
    start_date: datetime.date,
    grace_end_date: datetime.date,
    months_per_period: int,
    rate_lookup: RateLookup,
    rounding: str = "ROUND",
) -> list[ScheduleRow]:
    """
    거치기간: 원금상환 없이 이자만 발생(10~11번 요구사항).
    PPMT/IPMT는 '회차 수'가 확정되어야 계산 가능하므로, 거치기간에는 재원과 무관하게
    단리방식으로 계산한다(1단계에서 실데이터로 확인된 전제).
    """
    rows: list[ScheduleRow] = []
    period_start = start_date
    due_date = first_anchor_due_date(start_date, months_per_period)

    while due_date <= grace_end_date:
        subperiods = split_at_year_end(period_start, due_date)
        for idx, (s, e) in enumerate(subperiods):
            is_real = idx == len(subperiods) - 1
            days = days_between(s, e)
            rate = rate_lookup(e)
            interest = calc_simple_interest(assigned_amount, days, rate, rounding)
            rows.append(
                ScheduleRow(
                    due_date=due_date,
                    interest_calc_start=s,
                    interest_calc_end=e,
                    days=days,
                    balance_before=assigned_amount,
                    principal_amount=0,
                    interest_amount=interest,
                    balance_after=assigned_amount,
                    applied_rate=rate,
                    row_type="YEAR_END_SPLIT_REAL" if len(subperiods) > 1 else "GRACE",
                    remark="거치기간",
                )
            )
        period_start = due_date
        due_date = add_months_eom(due_date, months_per_period)
    return rows


def generate_equal_principal_schedule(
    *,
    balance_start: int,
    assigned_amount: int,
    first_due_date: datetime.date,
    total_installments: int,
    months_per_period: int,
    rate_lookup: RateLookup,
    period_start_date: datetime.date,
    start_installment_no: int = 0,
    rounding: str = "ROUND",
) -> list[ScheduleRow]:
    """
    공자기금 방식(원금균등, 단리). 1단계 Test A(161/161행 100% 일치) 로직 그대로.

    start_installment_no: 0이면 처음부터(최초생성). 금리변동 등으로 특정 회차부터
    재계산을 재개할 때는 '그 회차 직전까지 완료된 회차 번호'를 넘긴다 — 회차당
    원금(principal_per_installment)은 항상 원래 총 배정금액/총회차 기준으로 고정되고,
    실제 상환이력과 무관하게 변하지 않는다(2단계에서 확인된 원칙).
    """
    principal_per_installment = excel_round(assigned_amount / total_installments)

    rows: list[ScheduleRow] = []
    balance = balance_start
    period_start = period_start_date
    due_date = first_due_date
    installment_no = start_installment_no

    while installment_no < total_installments:
        installment_no += 1
        is_last = installment_no == total_installments
        principal = balance if is_last else principal_per_installment

        subperiods = split_at_year_end(period_start, due_date)
        for idx, (s, e) in enumerate(subperiods):
            is_real = idx == len(subperiods) - 1
            days = days_between(s, e)
            rate = rate_lookup(e)
            interest = calc_simple_interest(balance, days, rate, rounding)
            rows.append(
                ScheduleRow(
                    due_date=due_date,
                    interest_calc_start=s,
                    interest_calc_end=e,
                    days=days,
                    balance_before=balance,
                    principal_amount=principal if is_real else 0,
                    interest_amount=interest,
                    balance_after=balance - principal if is_real else balance,
                    applied_rate=rate,
                    row_type="CLOSING" if (is_last and is_real) else (
                        "YEAR_END_SPLIT_REAL" if len(subperiods) > 1 else "NORMAL"
                    ),
                    installment_no=installment_no if is_real else None,
                    remark="마지막회차 자동보정" if (is_last and is_real and principal != principal_per_installment) else None,
                )
            )
        balance -= principal
        period_start = due_date
        due_date = add_months_eom(due_date, months_per_period)

    return rows


def generate_equal_payment_schedule(
    *,
    balance_start: int,
    first_due_date: datetime.date,
    total_installments: int,
    months_per_period: int,
    rate_lookup: RateLookup,
    period_start_date: datetime.date,
) -> list[ScheduleRow]:
    """
    주택도시기금 방식(원리금균등, PPMT/IPMT). 1단계 Test B(81/81행 100% 일치) 로직 그대로.
    회계연도 경계 분리 시: 앞쪽은 메모(실지급 0), 뒤쪽 실행 회차에 전체 이자 귀속.
    금리는 변경이력이 있을 수 있으므로(VARIABLE), 회차마다 rate_lookup으로 조회한다.
    """
    periods_per_year = 12 // months_per_period
    rows: list[ScheduleRow] = []
    balance = balance_start
    period_start = period_start_date
    due_date = first_due_date
    installment_no = 0

    pv = balance_start  # PPMT/IPMT 닫힌 형태 계산 기준 원금(이 버전 동안 고정)

    while installment_no < total_installments:
        installment_no += 1
        is_last = installment_no == total_installments
        rate = rate_lookup(due_date) / 100
        r = rate / periods_per_year
        n = total_installments
        A = pv * r / (1 - (1 + r) ** -n)
        v = 1 + r
        bal_before_analytic = pv * v ** (installment_no - 1) - A * (v ** (installment_no - 1) - 1) / r
        interest_full = excel_round(bal_before_analytic * r)
        principal_full = excel_round(A - bal_before_analytic * r)
        if is_last:
            principal_full = balance  # 마지막 회차 자동보정 — 잔액 전액 상환

        subperiods = split_at_year_end(period_start, due_date)
        if len(subperiods) == 1:
            s, e = subperiods[0]
            days = days_between(s, e)
            rows.append(
                ScheduleRow(
                    due_date=due_date, interest_calc_start=s, interest_calc_end=e, days=days,
                    balance_before=balance, principal_amount=principal_full,
                    interest_amount=interest_full, balance_after=balance - principal_full,
                    applied_rate=rate * 100,
                    row_type="CLOSING" if is_last else "NORMAL",
                    installment_no=installment_no,
                    remark="마지막회차 자동보정" if is_last else None,
                )
            )
        else:
            (s1, e1), (s2, e2) = subperiods
            d1, d2 = days_between(s1, e1), days_between(s2, e2)
            memo_interest = interest_full * d1 / (d1 + d2)
            rows.append(
                ScheduleRow(
                    due_date=due_date, interest_calc_start=s1, interest_calc_end=e1, days=d1,
                    balance_before=balance, principal_amount=0, interest_amount=0,
                    balance_after=balance, applied_rate=rate * 100,
                    row_type="YEAR_END_SPLIT_MEMO", memo_interest=memo_interest,
                )
            )
            rows.append(
                ScheduleRow(
                    due_date=due_date, interest_calc_start=s2, interest_calc_end=e2, days=d2,
                    balance_before=balance, principal_amount=principal_full,
                    interest_amount=interest_full, balance_after=balance - principal_full,
                    applied_rate=rate * 100,
                    row_type="CLOSING" if is_last else "YEAR_END_SPLIT_REAL",
                    memo_interest=interest_full * d2 / (d1 + d2),
                    installment_no=installment_no,
                    remark="마지막회차 자동보정" if is_last else None,
                )
            )

        balance -= principal_full
        period_start = due_date
        due_date = add_months_eom(due_date, months_per_period)

    return rows


# ---------- §37 검증 ----------
@dataclass
class ValidationResult:
    is_valid: bool
    errors: list[str] = field(default_factory=list)


def validate_schedule(rows: list[ScheduleRow], assigned_amount: int) -> ValidationResult:
    errors = []

    total_principal = sum(r.principal_amount for r in rows)
    if total_principal != assigned_amount:
        errors.append(f"총 원금상환액({total_principal:,})이 배정금액({assigned_amount:,})과 일치하지 않습니다.")

    if rows:
        final_balance = rows[-1].balance_after
        if final_balance != 0:
            errors.append(f"최종 잔여원금이 0원이 아닙니다({final_balance:,}).")

    for r in rows:
        if r.row_type != "YEAR_END_SPLIT_MEMO":
            if r.balance_before - r.principal_amount != r.balance_after:
                errors.append(f"{r.due_date} 회차: 상환후잔액 = 상환전잔액 - 원금상환액 불일치")
        if r.days != days_between(r.interest_calc_start, r.interest_calc_end):
            errors.append(f"{r.due_date} 회차: 이자계산일수 불일치")

    return ValidationResult(is_valid=len(errors) == 0, errors=errors)
