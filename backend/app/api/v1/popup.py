"""로그인 알림 팝업 — 중도변수 미처리, 관리자 시스템 안내(공휴일 연동)."""
from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.domain.popup_service import acknowledge, popup_items
from app.models.organization import User

router = APIRouter(prefix="/popup", tags=["알림 팝업"])


class AckIn(BaseModel):
    keys: list[str] = Field(..., min_length=1, max_length=500)


@router.get("")
def get_popup(as_of: date | None = None, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return popup_items(db, current_user, as_of or date.today())


@router.post("/ack")
def ack_popup(payload: AckIn, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """'확인' — 중도변수 항목은 오늘 하루, 시스템 안내는 7일간 숨긴다. 그때까지 해결되지 않으면 다시 뜬다."""
    return {"acknowledged": acknowledge(db, current_user, payload.keys)}
