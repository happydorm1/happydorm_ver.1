"""
중도변수 이벤트 '적용' 공통 진입점 — 즉시 적용(기본)과 결재 후 적용(EVENT_APPROVAL_REQUIRED=true)이 같은 계산 경로를 쓴다.
각 함수는 (생성된 LoanEvent id 또는 None, 결과 원리금 테이블 버전 id)를 돌려준다.
"""
import uuid

from sqlalchemy.orm import Session

from app.domain.acceleration_service import AccelerationError, apply_acceleration
from app.domain.deferral_service import DeferralError, apply_deferral
from app.domain.early_repayment_service import EarlyRepaymentError, apply_early_repayment
from app.domain.rate_change_service import RateChangeError, apply_rate_change
from app.domain.refinancing_service import RefinancingError, apply_refinancing
from app.schemas.event import AccelerationCreate, DeferralCreate, EarlyRepaymentCreate, RateChangeCreate, RefinancingCreate

APPLY_ERRORS = (AccelerationError, DeferralError, EarlyRepaymentError, RateChangeError, RefinancingError)

LABELS = {"DEFERRAL": "상환유예", "RATE_CHANGE": "금리변동", "ACCELERATION": "원금가속상환",
          "EARLY_REPAYMENT": "조기상환", "REFINANCING": "대환대출"}


def _deferral(db, loan_id, p: DeferralCreate, by):
    e = apply_deferral(db, loan_id, event_date=p.event_date, defer_target=p.defer_target,
                       recovery_cycle_months=p.recovery_cycle_months, recovery_count=p.recovery_count,
                       reason=p.reason, created_by=by)
    return e.id, e.resulting_version_id


def _rate_change(db, loan_id, p: RateChangeCreate, by):
    v = apply_rate_change(db, loan_id, effective_start_date=p.effective_start_date, annual_rate=p.annual_rate,
                          rate_table_name=p.rate_table_name, rate_basis=p.rate_basis, created_by=by)
    return None, v.id


def _acceleration(db, loan_id, p: AccelerationCreate, by):
    e = apply_acceleration(db, loan_id, event_date=p.event_date, multiplier=p.acceleration_multiplier,
                           reason=p.reason, created_by=by)
    return e.id, e.resulting_version_id


def _early(db, loan_id, p: EarlyRepaymentCreate, by):
    e = apply_early_repayment(db, loan_id, event_date=p.event_date, repay_type=p.repay_type, repay_amount=p.repay_amount,
                              fee_amount=p.fee_amount, reason=p.reason, created_by=by)
    return e.id, e.resulting_version_id


def _refi(db, loan_id, p: RefinancingCreate, by):
    e = apply_refinancing(db, loan_id, refi_type=p.refi_type, event_date=p.event_date, new_fund_id=p.new_fund_id,
                          new_annual_rate=p.new_annual_rate, rate_table_name=p.rate_table_name,
                          converted_amount=p.converted_amount, remaining_amount=p.remaining_amount,
                          rejoin_date=p.rejoin_date, reason=p.reason, created_by=by)
    return e.id, e.resulting_version_id


REGISTRY = {
    "DEFERRAL": (DeferralCreate, _deferral),
    "RATE_CHANGE": (RateChangeCreate, _rate_change),
    "ACCELERATION": (AccelerationCreate, _acceleration),
    "EARLY_REPAYMENT": (EarlyRepaymentCreate, _early),
    "REFINANCING": (RefinancingCreate, _refi),
}


def apply_from_payload(db: Session, event_type: str, loan_id: uuid.UUID, payload: dict, created_by: uuid.UUID | None):
    schema, fn = REGISTRY[event_type]
    return fn(db, loan_id, schema.model_validate(payload), created_by)
