"""
Phase 1: 기숙사 / SPC / 사용자 관리
ERD 설계(2단계 산출물) 3번 '조직 테이블' 섹션 그대로 구현.
"""
import enum
import uuid

from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import AuditMixin, Base


class UserRole(str, enum.Enum):
    SYSTEM_ADMIN = "SYSTEM_ADMIN"              # 시스템관리자
    FOUNDATION_FUND_MANAGER = "FOUNDATION_FUND_MANAGER"  # 재단 자금담당자
    FOUNDATION_APPROVER = "FOUNDATION_APPROVER"  # 재단 결재권자
    SPC_MANAGER = "SPC_MANAGER"                 # SPC 담당자
    VIEWER = "VIEWER"                            # 조회자


class DormitoryStatus(str, enum.Enum):
    IN_PROGRESS = "IN_PROGRESS"
    CLOSED = "CLOSED"


class SPC(Base, AuditMixin):
    __tablename__ = "spc"

    spc_name: Mapped[str] = mapped_column(String(100), nullable=False)
    contact_info: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # 담당자 연락처(알림 대상)

    dormitories: Mapped[list["Dormitory"]] = relationship(back_populates="spc")

    def __repr__(self) -> str:
        return f"<SPC {self.spc_name}>"


class User(Base, AuditMixin):
    __tablename__ = "app_user"

    name: Mapped[str] = mapped_column(String(50), nullable=False)
    email: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), nullable=False)
    department: Mapped[str | None] = mapped_column(String(50), nullable=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    # Phase 14 보안: 로그인 실패 잠금, 비밀번호 변경 시 기존 토큰 무효화, 조회자 다운로드 허용 여부(§45 '별도 설정')
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    can_download: Mapped[bool] = mapped_column(default=True, server_default="true", nullable=False)

    # SPC 담당자인 경우 소속 SPC (row-level 권한 필터링에 사용)
    spc_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("spc.id"), nullable=True
    )

    def __repr__(self) -> str:
        return f"<User {self.email} ({self.role})>"


class Dormitory(Base, AuditMixin):
    __tablename__ = "dormitory"

    dormitory_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    school_name: Mapped[str] = mapped_column(String(100), nullable=False)
    project_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # 행복기숙사(사립)/행복기숙사(연합)/에듀21/기숙사형 청년주택

    spc_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("spc.id"), nullable=False)
    manager_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id"), nullable=True
    )
    status: Mapped[DormitoryStatus] = mapped_column(
        Enum(DormitoryStatus), default=DormitoryStatus.IN_PROGRESS, nullable=False
    )

    spc: Mapped["SPC"] = relationship(back_populates="dormitories")

    def __repr__(self) -> str:
        return f"<Dormitory {self.dormitory_name}>"
