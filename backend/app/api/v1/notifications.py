"""Phase 12: 알림 배치 실행, 내 알림함, 대시보드 업무 알림 요약."""
import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user, require_roles
from app.domain.alerts_service import build_alert_summary
from app.domain.notification_service import scan_notifications
from app.models.notification import Notification
from app.models.organization import User, UserRole

router = APIRouter(tags=["알림"])

WRITE_ROLES = (UserRole.SYSTEM_ADMIN, UserRole.FOUNDATION_FUND_MANAGER)


class NotificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    loan_id: uuid.UUID
    dormitory_id: uuid.UUID
    notification_type: str
    reference_date: date
    trigger_date: date
    title: str
    body: str
    payload: dict
    channel: str
    is_read: bool
    created_at: datetime


@router.post("/notifications/scan")
def run_scan(as_of: date | None = None, dormitory_id: uuid.UUID | None = None, db: Session = Depends(get_db),
             current_user: User = Depends(require_roles(*WRITE_ROLES))):
    """알림 생성 배치를 실행한다(여러 번 실행해도 중복 생성 없음). 운영에서는 하루 한 번 스케줄러가 호출한다."""
    return scan_notifications(db, as_of or date.today(), dormitory_id=dormitory_id)


@router.get("/notifications", response_model=list[NotificationRead])
def my_notifications(unread_only: bool = False, limit: int = 50, db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_user)):
    stmt = select(Notification).where(Notification.recipient_user_id == current_user.id, Notification.is_deleted.is_(False))
    if unread_only:
        stmt = stmt.where(Notification.is_read.is_(False))
    return db.execute(stmt.order_by(Notification.created_at.desc()).limit(min(max(limit, 1), 200))).scalars().all()


@router.post("/notifications/{notification_id}/read", response_model=NotificationRead)
def mark_read(notification_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    n = db.get(Notification, notification_id)
    if n is None or n.recipient_user_id != current_user.id:  # 남의 알림은 존재 여부도 알려주지 않는다
        raise HTTPException(status_code=404, detail="알림을 찾을 수 없습니다.")
    if not n.is_read:
        n.is_read, n.read_at = True, datetime.now(timezone.utc)
        db.commit()
        db.refresh(n)
    return n


@router.post("/notifications/read-all")
def mark_all_read(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    res = db.execute(update(Notification).where(Notification.recipient_user_id == current_user.id,
                                                Notification.is_read.is_(False))
                     .values(is_read=True, read_at=datetime.now(timezone.utc)))
    db.commit()
    return {"marked": res.rowcount}


@router.get("/alerts/summary")
def alerts_summary(as_of: date | None = None, dormitory_id: uuid.UUID | None = None, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    """대시보드 업무 알림: 상환 예정 · 금리변동 예정 · 미납 · 확인 필요(§35)."""
    return build_alert_summary(db, current_user, as_of or date.today(), dormitory_id=dormitory_id)
