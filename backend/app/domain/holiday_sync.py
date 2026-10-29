"""
공휴일 자동 동기화 — 수기 입력 대신 공식 데이터(한국천문연구원 특일 정보)로 매년 공휴일·대체공휴일·임시공휴일을 받아온다.

반영 규칙
- KASI 공휴일(isHoliday=Y)은 PUBLIC/KASI로 추가·이름 갱신. 사용중지돼 있던 같은 날짜는 되살린다.
- 추가 휴무 규칙(설정 BANK_EXTRA_CLOSURES, 기본 없음 — 5/1·12/31은 휴무일 아님으로 확정)은 BANK/BANK_RULE로 추가.
  규칙에서 빠진 BANK_RULE 날짜는 사용중지(물리삭제 없음). 이미 공휴일이면 공휴일로 둔다.
- 지난번에 KASI로 받았는데 이번 응답에 없는 날짜(임시공휴일 취소 등)는 사용중지. 단, 받아온 건수가 0이면 아무것도 지우지 않는다(장애 방어).
- 담당자가 직접 넣은 MANUAL 공휴일은 절대 건드리지 않는다.
- 호출이 하나라도 실패하면 DB를 바꾸지 않고 FAILED 이력만 남긴다.
"""
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.engine.holiday_source import HolidaySourceError, bank_rule_dates, fetch_year, http_fetcher
from app.models.calendar import Holiday, HolidaySyncLog


class HolidaySyncError(Exception):
    pass


def sync_enabled() -> bool:
    return bool(settings.KASI_SERVICE_KEY)


def _log(db: Session, year: int, status: str, triggered_by: str, **kw) -> HolidaySyncLog:
    log = HolidaySyncLog(year=year, synced_at=datetime.now(timezone.utc), status=status, triggered_by=triggered_by, **kw)
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def sync_year(db: Session, year: int, *, triggered_by: str = "MANUAL", fetcher=None) -> HolidaySyncLog:
    if not sync_enabled():
        raise HolidaySyncError("공휴일 API 서비스키(KASI_SERVICE_KEY)가 설정되어 있지 않습니다. 공공데이터포털에서 발급 후 .env에 등록하세요.")
    try:
        fetched = fetch_year(year, service_key=settings.KASI_SERVICE_KEY, url=settings.KASI_API_URL,
                             fetcher=fetcher or http_fetcher)
    except HolidaySourceError as e:
        db.rollback()
        return _log(db, year, "FAILED", triggered_by, message=str(e))

    counts = {"created": 0, "updated": 0, "deactivated": 0}
    existing = {h.holiday_date: h for h in db.execute(
        select(Holiday).where(Holiday.holiday_date >= date(year, 1, 1), Holiday.holiday_date <= date(year, 12, 31))
    ).scalars()}

    def upsert(d: date, name: str, htype: str, source: str):
        h = existing.get(d)
        if h is None:
            h = Holiday(holiday_date=d, holiday_name=name, holiday_type=htype, source=source)
            db.add(h)
            existing[d] = h
            counts["created"] += 1
            return
        if h.source == "MANUAL" and not h.is_deleted:
            return  # 담당자 입력은 보존
        if source == "BANK_RULE" and h.source == "KASI" and not h.is_deleted:
            return  # 이미 법정 공휴일인 날은 공휴일로 둔다
        if h.is_deleted or h.holiday_name != name or h.source != source or h.holiday_type != htype:
            h.is_deleted, h.holiday_name, h.holiday_type, h.source = False, name, htype, source
            counts["updated"] += 1

    kasi_dates = set()
    for d, name in fetched:
        upsert(d, name, "PUBLIC", "KASI")
        kasi_dates.add(d)
    rule_dates = set()
    for d, name in bank_rule_dates(year, settings.BANK_EXTRA_CLOSURES):
        upsert(d, name, "BANK", "BANK_RULE")
        rule_dates.add(d)
    for d, h in existing.items():   # 규칙에서 빠진 은행휴무(예: 예전 기본값 5/1·12/31)는 사용중지
        if h.source == "BANK_RULE" and not h.is_deleted and d not in rule_dates:
            h.is_deleted = True
            counts["deactivated"] += 1

    if fetched:  # 0건이면(장애·미발표) 기존 데이터를 지우지 않는다
        for d, h in existing.items():
            if h.source == "KASI" and not h.is_deleted and d not in kasi_dates:
                h.is_deleted = True
                counts["deactivated"] += 1

    db.commit()
    msg = None if fetched else "API가 0건을 반환했습니다(해당 연도 공휴일 미발표 가능). 기존 데이터는 유지했습니다."
    return _log(db, year, "SUCCESS", triggered_by, fetched=len(fetched), message=msg, **counts)


def retire_stale_bank_rules(db: Session) -> int:
    """서비스키가 없어도 실행: 현재 규칙에 없는 BANK_RULE 휴무일을 모든 연도에서 사용중지한다(일일 배치·관리자 점검용)."""
    rows = db.execute(select(Holiday).where(Holiday.source == "BANK_RULE", Holiday.is_deleted.is_(False))).scalars().all()
    n = 0
    for h in rows:
        if h.holiday_date not in {d for d, _ in bank_rule_dates(h.holiday_date.year, settings.BANK_EXTRA_CLOSURES)}:
            h.is_deleted = True
            n += 1
    if n:
        db.commit()
    return n


def needs_sync(db: Session, year: int, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    last = db.execute(
        select(HolidaySyncLog).where(HolidaySyncLog.year == year, HolidaySyncLog.status == "SUCCESS")
        .order_by(HolidaySyncLog.synced_at.desc())
    ).scalars().first()
    return last is None or (now - last.synced_at) > timedelta(days=settings.HOLIDAY_RESYNC_DAYS)


def ensure_holidays(db: Session, years: set[int], *, triggered_by: str = "DAILY_SCAN", fetcher=None) -> list[dict]:
    """일일 배치용: 서비스키가 있으면, 최근 동기화가 오래된 연도만 다시 받는다. 실패해도 예외를 던지지 않는다."""
    if not sync_enabled():
        return [{"year": y, "status": "SKIPPED", "message": "서비스키 미설정"} for y in sorted(years)]
    out = []
    for y in sorted(years):
        if not needs_sync(db, y):
            out.append({"year": y, "status": "UP_TO_DATE"})
            continue
        log = sync_year(db, y, triggered_by=triggered_by, fetcher=fetcher)
        out.append({"year": y, "status": log.status, "created": log.created, "updated": log.updated,
                    "deactivated": log.deactivated, "message": log.message})
    return out
