import uuid
from datetime import date, datetime
from decimal import Decimal

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.loan import LoanStatus, RateType


# ---------- InterestRateHistory ----------
class InterestRateHistoryBase(BaseModel):
    effective_start_date: date
    effective_end_date: date | None = None
    annual_rate: Decimal = Field(..., gt=0, lt=100)
    rate_basis: str | None = None
    rate_table_name: str | None = None


class InterestRateHistoryCreate(InterestRateHistoryBase):
    pass


class InterestRateHistoryRead(InterestRateHistoryBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    loan_id: uuid.UUID
    is_deleted: bool = False


# ---------- Loan ----------
class GracePrincipalPayment(BaseModel):
    """거치기간 중 원금상환 1건 — 거치기간의 이자 납입일(상환예정일)에 원금을 함께 갚는다."""
    due_date: date
    amount: int = Field(..., gt=0)


def check_grace_payments(payments, grace_start, grace_end, assigned_amount) -> None:
    if not payments:
        return
    if not (grace_start and grace_end):
        raise ValueError("거치기간 중 원금상환은 거치기간(시작·종료일)을 넣어야 입력할 수 있습니다.")
    dates = [p.due_date for p in payments]
    if len(set(dates)) != len(dates):
        raise ValueError("거치기간 중 원금상환에 같은 날짜가 두 번 있습니다.")
    for d in dates:
        if not (grace_start < d <= grace_end):
            raise ValueError(f"거치기간 중 원금상환일 {d}이(가) 거치기간({grace_start} ~ {grace_end}) 밖입니다.")
    if assigned_amount is not None and sum(p.amount for p in payments) >= assigned_amount:
        raise ValueError("거치기간 중 원금상환 합계가 배정금액보다 작아야 합니다.")


class LoanBase(BaseModel):
    dormitory_id: uuid.UUID
    fund_id: uuid.UUID
    loan_code: str = Field(..., max_length=30)
    assigned_amount: int = Field(..., gt=0)
    assigned_date: date
    rate_type: RateType
    repay_cycle_code_id: uuid.UUID
    repay_method_code_id: uuid.UUID
    grace_type_code_id: uuid.UUID | None = None
    grace_start_date: date | None = None
    grace_end_date: date | None = None
    principal_repay_start_date: date
    repay_period_months: int = Field(..., gt=0)
    interest_rounding: Literal["ROUND", "ROUNDDOWN", "ROUNDDOWN10"] = "ROUND"
    grace_principal_payments: list[GracePrincipalPayment] = []

    @field_validator("grace_principal_payments", mode="before")
    @classmethod
    def _none_to_list(cls, v):
        return v or []

    @model_validator(mode="after")
    def check_grace_dates(self):
        if (self.grace_start_date is None) != (self.grace_end_date is None):
            raise ValueError("거치시작일과 거치종료일은 둘 다 입력하거나 둘 다 비워야 합니다.")
        if (
            self.grace_start_date
            and self.grace_end_date
            and self.grace_start_date > self.grace_end_date
        ):
            raise ValueError("거치종료일은 거치시작일보다 빠를 수 없습니다.")
        check_grace_payments(sorted(self.grace_principal_payments, key=lambda p: p.due_date),
                             self.grace_start_date, self.grace_end_date, self.assigned_amount)
        return self


class LoanCreate(LoanBase):
    # 최초 등록 시 금리이력 1건을 함께 입력(배정 시점 적용금리)
    initial_rate: InterestRateHistoryCreate


class LoanUpdate(BaseModel):
    assigned_amount: int | None = None
    grace_start_date: date | None = None
    grace_end_date: date | None = None
    principal_repay_start_date: date | None = None
    repay_period_months: int | None = None
    grace_principal_payments: list[GracePrincipalPayment] | None = None
    status: LoanStatus | None = None
    change_reason: str | None = Field(default=None, max_length=500, description="변경사유(변경이력에 기록)")


class LoanRead(LoanBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    status: LoanStatus
    closed_by_full_repayment: bool = False   # 전액 조기상환 적용 중 → 화면 상태 '종료'
    created_at: datetime
    rate_history: list[InterestRateHistoryRead] = []
