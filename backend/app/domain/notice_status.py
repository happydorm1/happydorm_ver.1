"""
Phase 20: 안내공문 발급·수신 현황(대시보드).

- 수신: 기숙사 측(그 기숙사 SPC 담당자)이 공문을 열람·내려받거나 공문 발급 알림을 읽으면 처음 한 번 기록한다(mark_received).
  재단 직원이 열어 보는 것은 수신이 아니다.
- 같은 기숙사·상환예정일 공문을 다시 발급하면 가장 최근 발급본만 본다(이전 발급본의 수신 여부는 무시).
- 재단(관리자·자금담당자): '안내공문 발송예정(상환예정)'에 발급완료·수신 여부 표시, '안내공문 미수신' 목록.
- SPC 담당자: '안내공문 수신' — 자기 기숙사 공문만.
"""
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.document import GeneratedDocument
from app.models.organization import Dormitory, User, UserRole

FOUNDATION_NOTICE_ROLES = (UserRole.SYSTEM_ADMIN, UserRole.FOUNDATION_FUND_MANAGER)
RECEIVED_LOOKBACK_DAYS = 31     # SPC '안내공문 수신' 목록: 납부기한이 지난 지 한 달 이내 공문까지 보여 준다


def mark_received(db: Session, doc: GeneratedDocument, user: User, via: str) -> bool:
    """SPC 담당자가 자기 기숙사 공문을 열람·내려받거나 알림을 읽었을 때. 처음 한 번만 기록하고 True."""
    if user.role != UserRole.SPC_MANAGER or doc.received_at is not None or doc.is_deleted:
        return False
    dorm = db.get(Dormitory, doc.dormitory_id)
    if dorm is None or dorm.spc_id != user.spc_id:
        return False
    doc.received_at, doc.received_by, doc.received_via = datetime.now(timezone.utc), user.id, via
    db.commit()
    return True


def _latest_docs(db: Session, dorm_ids: set, since: date | None = None) -> dict[tuple, GeneratedDocument]:
    if not dorm_ids:
        return {}
    q = select(GeneratedDocument).where(GeneratedDocument.dormitory_id.in_(dorm_ids), GeneratedDocument.is_deleted.is_(False))
    if since:
        q = q.where(GeneratedDocument.payment_deadline >= since)
    out: dict[tuple, GeneratedDocument] = {}
    for d in db.execute(q.order_by(GeneratedDocument.created_at)).scalars():
        out[(d.dormitory_id, d.due_date)] = d          # 나중 발급본이 덮어쓴다
    return out


def _item(doc: GeneratedDocument, dorm: Dormitory, names: dict) -> dict:
    t = (doc.generated_data or {}).get("totals") or {}
    return {"notice_id": doc.id, "dormitory_id": dorm.id, "dormitory_name": dorm.dormitory_name, "school_name": dorm.school_name,
            "title": doc.title, "due_date": doc.due_date, "payment_deadline": doc.payment_deadline, "issued_at": doc.created_at,
            "total": t.get("total"), "received": doc.received_at is not None, "received_at": doc.received_at,
            "received_by_name": names.get(doc.received_by), "received_via": doc.received_via}


def build_notice_status(db: Session, user: User, as_of: date, dorms: dict, upcoming: list[dict]) -> dict:
    """dorms: 권한·검색조건이 적용된 기숙사 {id: Dormitory}. upcoming: 상환예정 알림 항목(여기에 발급·수신 여부를 붙인다)."""
    ids = set(dorms)
    docs = _latest_docs(db, ids, since=as_of - timedelta(days=RECEIVED_LOOKBACK_DAYS))
    uids = {d.received_by for d in docs.values() if d.received_by}
    names = {u.id: u.name for u in db.execute(select(User).where(User.id.in_(uids))).scalars()} if uids else {}

    for x in upcoming:
        doc = docs.get((x.get("dormitory_id"), x["due_date"]))
        x["notice_issued"] = doc is not None
        x["notice_id"] = doc.id if doc else None
        x["notice_received"] = bool(doc and doc.received_at)

    if user.role == UserRole.SPC_MANAGER:
        mine = sorted((_item(d, dorms[k[0]], names) for k, d in docs.items() if k[0] in dorms),
                      key=lambda i: (i["received"], -i["due_date"].toordinal()))
        return {"scope": "SPC", "received": {"count": len(mine), "unconfirmed": sum(1 for i in mine if not i["received"]),
                                             "items": mine}}
    if user.role in FOUNDATION_NOTICE_ROLES:
        pending = sorted((_item(d, dorms[k[0]], names) for k, d in docs.items()
                          if d.received_at is None and d.payment_deadline >= as_of),
                         key=lambda i: (i["payment_deadline"], i["dormitory_name"]))
        return {"scope": "FOUNDATION", "unreceived": {"count": len(pending), "items": pending}}
    return {"scope": "NONE"}


def ensure_uuid(v) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(v)) if v else None
    except ValueError:
        return None


# ---------- Phase 21: 원리금 상환 › 안내공문 — 발송대상 현황 / 발송완료 ----------
TARGET_DAYS = 15   # 기본 발송대상: 납부기한이 영업일 D-15 이내(대시보드 '발송예정'과 같은 기준)


def notice_targets(db: Session, user: User, as_of: date, *, date_from: date | None = None, date_to: date | None = None,
                   include_issued: bool = False, dormitory_id: uuid.UUID | None = None) -> dict:
    """기숙사·상환예정일 단위 발송대상. 기간을 주지 않으면 납부기한 영업일 D-15 이내(오늘~), 주면 상환예정일이 그 기간인 회차."""
    from app.domain.calendar_service import load_holidays
    from app.domain.loan_filters import LoanFilters, filtered_loans
    from app.domain.schedule_amounts import scheduled_by_due_date
    from app.domain.version_utils import NoScheduleError, get_current_version, read_cache
    from app.engine.business_day import count_business_days, effective_due_date

    holidays = load_holidays(db)
    rows = filtered_loans(db, user, LoanFilters(dormitory_id=dormitory_id).active_only())
    slots: dict[tuple, dict] = {}
    with read_cache(db, [loan.id for loan, _, _ in rows]):
        for loan, dorm, fund in rows:
            try:
                amounts = scheduled_by_due_date(db, get_current_version(db, loan.id))
            except NoScheduleError:
                continue
            for d, m in amounts.items():
                if m["total"] <= 0:
                    continue
                deadline = effective_due_date(d, holidays)
                if date_from or date_to:
                    if (date_from and d < date_from) or (date_to and d > date_to):
                        continue
                elif not (deadline >= as_of and count_business_days(as_of, deadline, holidays) <= TARGET_DAYS):
                    continue
                s = slots.setdefault((dorm.id, d), {
                    "dormitory_id": dorm.id, "dormitory_name": dorm.dormitory_name, "school_name": dorm.school_name,
                    "project_type": dorm.project_type, "due_date": d, "payment_deadline": deadline,
                    "business_days_left": count_business_days(as_of, deadline, holidays) if deadline >= as_of else None,
                    "loans": [], "funds": set(), "principal": 0, "interest": 0, "total": 0})
                s["loans"].append(loan.loan_code)
                s["funds"].add(fund.fund_name)
                for k in ("principal", "interest", "total"):
                    s[k] += m[k]
    docs = _latest_docs(db, {k[0] for k in slots})
    out = []
    for k, s in slots.items():
        doc = docs.get(k)
        s["funds"] = sorted(s["funds"])
        s.update(notice_id=doc.id if doc else None, issued=doc is not None, issued_at=doc.created_at if doc else None,
                 received=bool(doc and doc.received_at))
        if doc is None or include_issued:
            out.append(s)
    out.sort(key=lambda x: (x["payment_deadline"], x["dormitory_name"]))
    return {"as_of": as_of, "default_window": not (date_from or date_to), "days": TARGET_DAYS, "count": len(out), "items": out}


def notices_issued(db: Session, user: User, *, date_from: date | None = None, date_to: date | None = None,
                   received: bool | None = None, dormitory_id: uuid.UUID | None = None) -> dict:
    """발송완료: 기숙사·상환예정일별 가장 최근 발급본(상환예정일 기준 기간), 수신 확인 여부 포함."""
    q = select(Dormitory).where(Dormitory.is_deleted.is_(False))
    if user.role == UserRole.SPC_MANAGER:
        q = q.where(Dormitory.spc_id == user.spc_id)
    if dormitory_id:
        q = q.where(Dormitory.id == dormitory_id)
    dorms = {d.id: d for d in db.execute(q).scalars()}
    docs = _latest_docs(db, set(dorms))
    uids = {d.received_by for d in docs.values() if d.received_by}
    names = {u.id: u.name for u in db.execute(select(User).where(User.id.in_(uids))).scalars()} if uids else {}
    items = []
    for (did, due), doc in docs.items():
        if (date_from and due < date_from) or (date_to and due > date_to):
            continue
        if received is not None and (doc.received_at is not None) != received:
            continue
        items.append(_item(doc, dorms[did], names))
    items.sort(key=lambda i: (-i["due_date"].toordinal(), i["dormitory_name"]))
    return {"count": len(items), "unreceived": sum(1 for i in items if not i["received"]), "items": items}
