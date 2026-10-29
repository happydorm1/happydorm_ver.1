"""
로그인 알림 팝업(업무기준 확정 2026-10-28).
  · 중도변수 미처리(midterm_check) → 재단 자금담당·결재권자·시스템관리자에게. '확인'하면 그날은 숨기고, 다음 날에도 미처리면 다시 띄운다.
  · 관리자 시스템 안내(공휴일 API 서비스키 미등록, 올해·내년 공휴일 미등록, 최근 동기화 실패) → 시스템관리자에게.
    관리자가 인지(확인)하지 않으면 접속할 때마다, 확인했어도 7일 뒤까지 해결되지 않았으면 다시 안내한다.
해결되면(금리 등록·이벤트 적용·서비스키 등록 등) 항목이 계산에서 빠지므로 팝업도 저절로 사라진다.
"""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.alerts_service import build_alert_summary
from app.domain.calendar_service import years_without_holidays
from app.domain.holiday_sync import sync_enabled
from app.core.config import settings
from app.models.calendar import HolidaySyncLog
from app.models.security import BackupLog
from app.models.notification import PopupAck
from app.models.organization import User, UserRole

KST = ZoneInfo("Asia/Seoul")
MIDTERM_ROLES = {UserRole.SYSTEM_ADMIN, UserRole.FOUNDATION_FUND_MANAGER, UserRole.FOUNDATION_APPROVER}
SYSTEM_REMIND_DAYS = 7


def _system_items(db: Session, as_of: date) -> list[dict]:
    items = []
    if not sync_enabled():
        items.append({"key": "SYSTEM:HOLIDAY_KEY_MISSING", "group": "SYSTEM", "kind": "HOLIDAY_KEY_MISSING",
                      "label": "공휴일 연동 미설정",
                      "detail": "공공데이터포털 '한국천문연구원 특일 정보' 서비스키가 등록되지 않았습니다. "
                                "발급 후 서버 .env의 KASI_SERVICE_KEY에 등록하고 공휴일 동기화를 실행하세요. "
                                "그전까지 영업일·납부기한 계산은 주말만 반영합니다."})
    years = {as_of.year, as_of.year + 1}
    missing = years_without_holidays(db, years)
    if missing:
        items.append({"key": f"SYSTEM:HOLIDAY_YEARS:{'-'.join(map(str, missing))}", "group": "SYSTEM",
                      "kind": "HOLIDAY_YEARS_MISSING", "label": "공휴일 미등록",
                      "detail": f"{', '.join(map(str, missing))}년 공휴일이 없습니다. 설·추석·대체공휴일이 영업일로 계산될 수 있습니다."})
    for y in sorted(years):
        last = db.execute(select(HolidaySyncLog).where(HolidaySyncLog.year == y)
                          .order_by(HolidaySyncLog.synced_at.desc())).scalars().first()
        if last is not None and last.status == "FAILED":
            items.append({"key": f"SYSTEM:HOLIDAY_SYNC_FAILED:{last.id}", "group": "SYSTEM", "kind": "HOLIDAY_SYNC_FAILED",
                          "label": "공휴일 동기화 실패",
                          "detail": f"{y}년 공휴일 동기화가 실패했습니다({last.synced_at:%Y-%m-%d}): {last.message or ''}".strip()})
    items += _backup_items(db)
    return items


def _backup_items(db: Session, now: datetime | None = None) -> list[dict]:
    """백업이 없거나 오래됐거나 마지막 백업이 실패했으면 관리자에게 안내(키에 날짜를 넣어, 해결 전까지 7일마다 다시)."""
    now = now or datetime.now(timezone.utc)
    last = db.execute(select(BackupLog).where(BackupLog.kind == "BACKUP").order_by(BackupLog.started_at.desc())).scalars().first()
    last_ok = db.execute(select(BackupLog).where(BackupLog.kind == "BACKUP", BackupLog.status == "SUCCESS")
                         .order_by(BackupLog.started_at.desc())).scalars().first()
    items = []
    if last is not None and last.status == "FAILED":
        items.append({"key": f"SYSTEM:BACKUP_FAILED:{last.id}", "group": "SYSTEM", "kind": "BACKUP_FAILED", "label": "백업 실패",
                      "detail": f"{last.started_at.astimezone(KST):%Y-%m-%d %H:%M} 백업이 실패했습니다: {(last.message or '')[:200]}"})
    if last_ok is None or now - last_ok.started_at > timedelta(hours=settings.BACKUP_ALERT_HOURS):
        when = "한 번도 성공한 적이 없습니다" if last_ok is None else f"마지막 성공 {last_ok.started_at.astimezone(KST):%Y-%m-%d %H:%M}"
        items.append({"key": f"SYSTEM:BACKUP_STALE:{last_ok.id if last_ok else 'none'}", "group": "SYSTEM", "kind": "BACKUP_STALE",
                      "label": "백업 확인 필요",
                      "detail": f"DB 백업이 {settings.BACKUP_ALERT_HOURS}시간 넘게 없습니다({when}). "
                                "서버의 일일 백업 작업(python -m app.jobs.backup run)이 실행되는지 확인하세요."})
    return items


def popup_items(db: Session, user: User, as_of: date, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    items: list[dict] = []
    if user.role in MIDTERM_ROLES:
        mids = build_alert_summary(db, user, as_of)["needs_attention"]["midterm_pending"]
        items += [{**m, "group": "MIDTERM"} for m in mids]
    if user.role == UserRole.SYSTEM_ADMIN:
        items += _system_items(db, as_of)

    acks = {a.item_key: a for a in db.execute(select(PopupAck).where(PopupAck.user_id == user.id)).scalars()}
    shown = [i for i in items if not (i["key"] in acks and acks[i["key"]].expires_at > now)]
    return {"as_of": as_of, "items": shown, "hidden_count": len(items) - len(shown), "total_count": len(items)}


def _expiry(key: str, now: datetime) -> datetime:
    if key.startswith("SYSTEM:"):
        return now + timedelta(days=SYSTEM_REMIND_DAYS)
    tomorrow = now.astimezone(KST).date() + timedelta(days=1)          # 중도변수: 다음 날 0시(한국시간)부터 다시
    return datetime.combine(tomorrow, time(0), tzinfo=KST)


def acknowledge(db: Session, user: User, keys: list[str], now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    out = []
    for k in dict.fromkeys(keys):
        exp = _expiry(k, now)
        ack = db.execute(select(PopupAck).where(PopupAck.user_id == user.id, PopupAck.item_key == k)).scalar_one_or_none()
        if ack is None:
            db.add(PopupAck(user_id=user.id, item_key=k, acked_at=now, expires_at=exp))
        else:
            ack.acked_at, ack.expires_at = now, exp
        out.append({"key": k, "hidden_until": exp})
    db.commit()
    return out
