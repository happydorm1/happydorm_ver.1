"""
기준정보(마스터) 테이블.
재원·상환주기·상환유형·거치유형은 관리자가 코드 수정 없이 추가할 수 있어야 한다(44번 요구사항).
"""
import enum
import uuid

from sqlalchemy import Boolean, Enum, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import AuditMixin, Base


class InterestMethod(str, enum.Enum):
    SIMPLE = "SIMPLE"          # 단리 (공공자금관리기금 방식)
    PPMT_IPMT = "PPMT_IPMT"    # 원리금균등 (주택도시기금 방식)


class Fund(Base, AuditMixin):
    """차입금 재원. 공공자금관리기금/주택도시기금이 기본값이며, 관리자가 추가 가능."""

    __tablename__ = "fund"

    fund_name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    default_day_count_basis: Mapped[int] = mapped_column(Integer, default=365, nullable=False)
    interest_method: Mapped[InterestMethod] = mapped_column(Enum(InterestMethod), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    def __repr__(self) -> str:
        return f"<Fund {self.fund_name}>"


class CodeGroup(str, enum.Enum):
    """CODE.group_code로 쓰이는 분류. 그룹 자체는 코드이므로 여기 값이 늘어날 수 있음."""

    REPAY_CYCLE = "REPAY_CYCLE"        # 분기/반기/월
    REPAY_METHOD = "REPAY_METHOD"      # 원금균등/원리금균등/만기일시/사용자정의
    GRACE_TYPE = "GRACE_TYPE"          # 일반거치/건설거치/원금거치/원금이자별도
    DEFER_TARGET = "DEFER_TARGET"      # 상환유예 대상(원금/원금+이자/이자)


class Code(Base, AuditMixin):
    """공통 코드 테이블 — 상환주기/상환유형/거치유형 등 기준정보를 하드코딩하지 않기 위함."""

    __tablename__ = "code"
    __table_args__ = (UniqueConstraint("group_code", "code", name="uq_code_group_code"),)

    group_code: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(30), nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    extra_value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # 예: REPAY_CYCLE의 extra_value = {"periods_per_year": 4}
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    def __repr__(self) -> str:
        return f"<Code {self.group_code}:{self.code}>"
