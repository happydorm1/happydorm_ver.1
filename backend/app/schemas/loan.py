import uuid
from datetime import date, datetime
from decimal import Decimal

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    interest_rounding: Literal["ROUND", "ROUNDDOWN"] = "ROUND"

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
    status: LoanStatus | None = None
    change_reason: str | None = Field(default=None, max_length=500, description="변경사유(변경이력에 기록)")


class LoanRead(LoanBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    status: LoanStatus
    created_at: datetime
    rate_history: list[InterestRateHistoryRead] = []
