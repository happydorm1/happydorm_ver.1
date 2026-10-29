"""
공휴일/은행 비영업일 기준정보(§31). 관리자가 수정할 수 있어야 하므로 코드가 아니라 DB로 관리한다.
토·일요일은 계산으로 처리하고, 이 테이블에는 공휴일·은행 휴무일만 둔다(은행연합회 영업일 기준 — 확정).
법정 공휴일 데이터는 매년 달라지므로(대체공휴일·임시공휴일) 코드에 넣지 않는다.
"""
from datetime import date

from datetime import datetime

from sqlalchemy import Date, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import AuditMixin, Base


class Holiday(Base, AuditMixin):
    __tablename__ = "holiday"

    holiday_date: Mapped[date] = mapped_column(Date, nullable=False, unique=True, index=True)
    holiday_name: Mapped[str] = mapped_column(String(80), nullable=False)
    holiday_type: Mapped[str] = mapped_column(String(10), default="PUBLIC", nullable=False)  # PUBLIC / BANK / OTHER
    # 출처: MANUAL(담당자 입력) / KASI(공공데이터 자동 동기화) / BANK_RULE(은행 휴무 규칙). 자동 동기화는 MANUAL을 건드리지 않는다.
    source: Mapped[str] = mapped_column(String(10), default="MANUAL", server_default="MANUAL", nullable=False)


class HolidaySyncLog(Base):
    """공휴일 자동 동기화 실행 이력 — 언제 무엇을 받아 몇 건을 반영했는지 추적."""

    __tablename__ = "holiday_sync_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)  # SUCCESS / FAILED
    created: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    deactivated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    fetched: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    triggered_by: Mapped[str] = mapped_column(String(40), default="MANUAL", nullable=False)  # 사용자 이메일 / DAILY_SCAN
