import uuid
from datetime import date

from pydantic import BaseModel, ConfigDict

from app.models.schedule import RowType, ScheduleVersionStatus


class RepaymentScheduleRowRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    seq_no: int
    due_date: date
    interest_calc_start: date
    interest_calc_end: date
    days: int
    installment_no: int | None
    balance_before: int
    principal_amount: int
    deferral_recovery_principal: int = 0      # 상환유예 회수원금(이 행에서 함께 갚는 동결 원금)
    principal_total: int = 0                  # 원금상환액 = principal_amount + deferral_recovery_principal
    interest_amount: int
    unpaid_interest_amount: int
    interest_total: int
    principal_and_interest_total: int
    balance_after: int
    applied_rate: float
    row_type: RowType
    memo_interest: float | None
    remark: str | None


class RepaymentScheduleVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    loan_id: uuid.UUID
    version_no: int
    generated_reason: str
    is_current: bool
    status: ScheduleVersionStatus
    validation_errors: list[str] | None
    rows: list[RepaymentScheduleRowRead] = []
