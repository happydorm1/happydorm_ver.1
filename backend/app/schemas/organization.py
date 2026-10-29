import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.organization import DormitoryStatus, UserRole


# ---------- SPC ----------
class SPCBase(BaseModel):
    spc_name: str = Field(..., max_length=100)
    contact_info: dict | None = None
    remark: str | None = None


class SPCCreate(SPCBase):
    pass


class SPCUpdate(BaseModel):
    spc_name: str | None = None
    contact_info: dict | None = None
    remark: str | None = None


class SPCRead(SPCBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


# ---------- User ----------
class UserBase(BaseModel):
    name: str = Field(..., max_length=50)
    email: EmailStr
    phone: str | None = None
    role: UserRole
    department: str | None = None
    spc_id: uuid.UUID | None = None


class UserCreate(UserBase):
    password: str = Field(..., min_length=8, max_length=128)   # 세부 규칙은 core/password_policy
    can_download: bool = True


class UserUpdate(BaseModel):
    name: str | None = None
    phone: str | None = None
    role: UserRole | None = None
    spc_id: uuid.UUID | None = None
    department: str | None = None
    is_active: bool | None = None
    can_download: bool | None = None              # 조회자 다운로드 허용 여부
    password: str | None = Field(default=None, max_length=128)   # 관리자 비밀번호 초기화
    change_reason: str | None = None


class UserRead(UserBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    is_active: bool
    can_download: bool
    locked_until: datetime | None = None
    last_login_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


# ---------- Dormitory ----------
class DormitoryBase(BaseModel):
    dormitory_name: str = Field(..., max_length=100)
    school_name: str = Field(..., max_length=100)
    project_type: str | None = None
    spc_id: uuid.UUID
    manager_user_id: uuid.UUID | None = None


class DormitoryCreate(DormitoryBase):
    pass


class DormitoryUpdate(BaseModel):
    dormitory_name: str | None = None
    school_name: str | None = None
    project_type: str | None = None
    spc_id: uuid.UUID | None = None
    manager_user_id: uuid.UUID | None = None
    status: DormitoryStatus | None = None


class DormitoryRead(DormitoryBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    status: DormitoryStatus
    created_at: datetime
    updated_at: datetime
