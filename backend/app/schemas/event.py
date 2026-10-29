import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.event import DeferTarget, LoanEventStatus, LoanEventType
from app.schemas.schedule import RepaymentScheduleVersionRead


class RateChangeCreate(BaseModel):
    effective_start_date: date
    annual_rate: Decimal = Field(..., gt=0, lt=100)
    rate_table_name: str | None = None
    rate_basis: str | None = None


class DeferralCreate(BaseModel):
    event_date: date
    defer_target: DeferTarget
    recovery_cycle_months: int = Field(..., description="3=분기, 6=반기")
    recovery_count: int = Field(..., gt=0, le=40)
    reason: str | None = None

    @field_validator("recovery_cycle_months")
    @classmethod
    def validate_cycle(cls, v: int) -> int:
        if v not in (3, 6):
            raise ValueError("회수주기는 분기(3) 또는 반기(6)만 지원합니다.")
        return v


class DeferralRecoveryRowRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    installment_seq: int
    recovery_principal: int
    recovery_interest: int
    additional_interest_on_frozen_principal: int


class DeferralRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    defer_target: DeferTarget
    frozen_principal_total: int
    frozen_interest_total: int
    recovery_cycle_months: int
    recovery_count: int
    recovery_start_date: date
    recovery_rows: list[DeferralRecoveryRowRead] = []


class LoanEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    loan_id: uuid.UUID
    event_type: LoanEventType
    event_date: date
    status: LoanEventStatus
    reason: str | None
    deferral: DeferralRead | None = None


class DeferralApplyResult(BaseModel):
    event: LoanEventRead
    resulting_schedule: RepaymentScheduleVersionRead


class AccelerationCreate(BaseModel):
    event_date: date
    acceleration_multiplier: Decimal = Field(default=Decimal("1.5"), gt=0, le=10)
    reason: str | None = None


class EarlyRepaymentCreate(BaseModel):
    event_date: date
    repay_type: str = Field(..., description="PARTIAL(일부) / FULL(전액)")
    repay_amount: int | None = Field(default=None, gt=0, description="일부 조기상환 금액(원). 전액은 생략")
    fee_amount: int = Field(default=0, ge=0, description="수수료(건별 입력, 기본 0)")
    reason: str | None = None

    @field_validator("repay_type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        v = v.upper()
        if v not in ("PARTIAL", "FULL"):
            raise ValueError("repay_type은 PARTIAL 또는 FULL이어야 합니다.")
        return v


class RefinancingCreate(BaseModel):
    refi_type: str = Field(..., description="FULL(전액) / PARTIAL(일부)")
    event_date: date
    new_fund_id: uuid.UUID
    new_annual_rate: Decimal = Field(..., gt=0, lt=100)
    rate_table_name: str | None = None
    # 일부대환 전용(담당자 직접 입력)
    converted_amount: int | None = Field(default=None, gt=0)
    remaining_amount: int | None = Field(default=None, gt=0)
    rejoin_date: date | None = None
    reason: str | None = None

    @field_validator("refi_type")
    @classmethod
    def validate_refi_type(cls, v: str) -> str:
        v = v.upper()
        if v not in ("FULL", "PARTIAL"):
            raise ValueError("refi_type은 FULL 또는 PARTIAL이어야 합니다.")
        return v
