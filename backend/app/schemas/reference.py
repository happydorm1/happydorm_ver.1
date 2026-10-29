import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.reference import InterestMethod


class FundBase(BaseModel):
    fund_name: str = Field(..., max_length=50)
    default_day_count_basis: int = 365
    interest_method: InterestMethod


class FundCreate(FundBase):
    pass


class FundRead(FundBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    is_active: bool


class CodeBase(BaseModel):
    group_code: str = Field(..., max_length=30)
    code: str = Field(..., max_length=30)
    label: str = Field(..., max_length=100)
    extra_value: dict | None = None
    sort_order: int = 0


class CodeCreate(CodeBase):
    pass


class CodeRead(CodeBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    is_active: bool
