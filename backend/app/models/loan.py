"""
Phase 2: 차입금 기본정보.
2단계 ERD 설계서 '4. 차입금 핵심 테이블' 섹션 구현.
"""
import enum
import uuid
from datetime import date

from sqlalchemy import BigInteger, Date, Enum, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import AuditMixin, Base


class RateType(str, enum.Enum):
    FIXED = "FIXED"        # 고정금리
    VARIABLE = "VARIABLE"  # 변동금리 (공공자금관리기금 7년주기 등)


class LoanStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"
    REFINANCED_OUT = "REFINANCED_OUT"  # 대환으로 종료(39/25번 — 이력 보존, 물리삭제 아님)


class Loan(Base, AuditMixin):
    __tablename__ = "loan"

    dormitory_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dormitory.id"), nullable=False, index=True
    )
    fund_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fund.id"), nullable=False
    )

    loan_code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    # 표시용 코드. 기숙사명/차입금명을 PK로 쓰지 않는다(39번 원칙) — 실제 PK는 id(UUID).

    assigned_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)  # 원 단위 정수
    assigned_date: Mapped[date] = mapped_column(Date, nullable=False)

    rate_type: Mapped[RateType] = mapped_column(Enum(RateType), nullable=False)

    repay_cycle_code_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("code.id"), nullable=False)
    repay_method_code_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("code.id"), nullable=False)
    grace_type_code_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("code.id"), nullable=True)

    grace_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    grace_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    principal_repay_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    # 거치기간 중 원금상환(사원총회 협의로 건설 사업비 잔여액 등을 상환) — [{"due_date": "YYYY-MM-DD", "amount": 원}]
    # 거치기간의 이자 납입일에 원금을 함께 갚고, 이후 이자는 줄어든 잔액으로 계산한다.
    grace_principal_payments: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    repay_period_months: Mapped[int] = mapped_column(nullable=False)

    # 이자 원단위 처리: ROUND(반올림, 기본) / ROUNDDOWN(절사 — 구 차입금 일부, 첨부6에서 확인)
    interest_rounding: Mapped[str] = mapped_column(String(12), default="ROUND", server_default="ROUND", nullable=False)
    # 원금 끝수처리 — 원금균등의 회차당 원금(배정액 ÷ 회차수)에 적용. 선택지는 이자 끝수처리와 같다(2026-11-28)
    principal_rounding: Mapped[str] = mapped_column(String(12), default="ROUND", server_default="ROUND", nullable=False)
    # 첫 이자납입일(선택) — 비우면 지급일 다음 상환일. 지급일과 상환일이 너무 가까워 다음 상환일로 합친 경우 지정(2026-11-29)
    first_interest_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # 회차 이자 조정(기존 엑셀 예외 그대로 이관) — [{"due_date": "YYYY-MM-DD", "interest": 정수, "reason": ""}]
    # 최초 원리금 테이블(V1)에서 그 상환일의 이자 합계를 이 금액으로 맞춘다(원금·잔액은 그대로)
    interest_adjustments: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    status: Mapped[LoanStatus] = mapped_column(Enum(LoanStatus), default=LoanStatus.ACTIVE, nullable=False)

    # 대환대출 추적(25번/39번 — 원 차입금 이력 보존)
    refinanced_to_loan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("loan.id"), nullable=True
    )
    refinanced_from_loan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("loan.id"), nullable=True
    )

    rate_history: Mapped[list["InterestRateHistory"]] = relationship(
        back_populates="loan", order_by="InterestRateHistory.effective_start_date"
    )

    def __repr__(self) -> str:
        return f"<Loan {self.loan_code}>"


class InterestRateHistory(Base, AuditMixin):
    """
    금리 변동 이력. 차입금에 '현재금리 1개'만 저장하지 않고 이력으로 관리한다(7번 요구사항).
    적용규칙(1단계에서 실데이터로 검증 완료): 해당 회차의 이자계산종료일이 포함되는
    기간의 금리를 그 회차 전체에 적용한다(일할 안분 없음).
    """

    __tablename__ = "interest_rate_history"

    loan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("loan.id"), nullable=False, index=True
    )
    effective_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    effective_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # NULL = 다음 변경금리가 아직 고시되지 않음(§9 — "변경금리" 미정 상태, 마지막 known rate로 계속 적용)

    annual_rate: Mapped[float] = mapped_column(Numeric(8, 5), nullable=False)  # 예: 2.24600
    rate_basis: Mapped[str | None] = mapped_column(String(200), nullable=True)  # 금리 산출근거
    rate_table_name: Mapped[str | None] = mapped_column(String(50), nullable=True)  # 금리적용표명

    loan: Mapped["Loan"] = relationship(back_populates="rate_history")

    def __repr__(self) -> str:
        return f"<RateHistory loan={self.loan_id} {self.effective_start_date}~{self.effective_end_date} {self.annual_rate}%>"
