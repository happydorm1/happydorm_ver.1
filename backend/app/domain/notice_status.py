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


DUE_SOON_DAYS = 3   # 납부기한 영업일 D-3 이내인데 입금이 없으면 '납부기한 임박 미입금'


def payment_states(db: Session, docs: dict, as_of: date | None = None) -> dict:
    """
    발송완료 공문별 입금(상환) 상태 — {(기숙사, 상환예정일): {...}}.
    1) 입금 확인·승인에서 완료된 입금 → PAID(입금일·시각, 자동 승인/승인/수동 처리)
    2) 입금 기록은 없지만 상환 실적 관리에 공문 금액만큼 납부 기록이 있음 → PAID(실적 입력)
    3) 연결된 대기 입금 → CHECKING(확인 중)
    4) 일부만 납부 → PARTIAL / 그 밖에 납부기한 전 → UNPAID, 지남 → OVERDUE
    재발급한 공문이어도 같은 기숙사·상환예정일의 입금이면 이어서 본다.
    """
    from app.models.payment import ActualPayment, BankDeposit, DepositStatus, PaymentStatus
    out: dict = {}
    if not docs:
        return out
    keys = set(docs)
    deps = db.execute(
        select(BankDeposit, GeneratedDocument.dormitory_id, GeneratedDocument.due_date)
        .join(GeneratedDocument, GeneratedDocument.id == BankDeposit.notice_id)
        .where(BankDeposit.is_deleted.is_(False), BankDeposit.status != DepositStatus.EXCLUDED,
               GeneratedDocument.dormitory_id.in_({k[0] for k in keys}))
        .order_by(BankDeposit.deposit_date.desc())).all()
    by_key: dict = {}
    for dep, did, due in deps:
        if (did, due) in keys:
            by_key.setdefault((did, due), []).append(dep)
    loan_ids = {uuid.UUID(s["loan_id"]) for d in docs.values() for s in (d.generated_data or {}).get("sources", [])}
    paid: dict = {}
    if loan_ids:
        for p in db.execute(select(ActualPayment).where(ActualPayment.loan_id.in_(loan_ids),
                                                        ActualPayment.status == PaymentStatus.ACTIVE)).scalars():
            slot = paid.setdefault((p.loan_id, p.due_date), [0, None])
            slot[0] += p.actual_principal + p.actual_interest
            slot[1] = p.actual_paid_date if slot[1] is None else max(slot[1], p.actual_paid_date)
    from app.domain.calendar_service import load_holidays
    from app.engine.business_day import count_business_days
    today = as_of or date.today()
    holidays = load_holidays(db)
    for k, doc in docs.items():
        gd = doc.generated_data or {}
        done = [d for d in by_key.get(k, []) if d.status == DepositStatus.COMPLETED]
        waiting = [d for d in by_key.get(k, []) if d.status == DepositStatus.PENDING]
        if done:
            d = done[0]
            out[k] = {"pay_status": "PAID", "paid_date": d.deposit_date, "paid_time": d.deposit_time,
                      "pay_via": "MANUAL" if d.manual_done else ("AUTO" if d.auto_approved else "APPROVED")}
            continue
        lines = list(zip(gd.get("sources", []), gd.get("payments", [])))
        got = [paid.get((uuid.UUID(s["loan_id"]), doc.due_date), [0, None]) for s, _ in lines]
        need = [ln["total"] for _, ln in lines]
        last = max((g[1] for g in got if g[1]), default=None)
        if lines and all(g[0] >= n for g, n in zip(got, need)):
            out[k] = {"pay_status": "PAID", "paid_date": last, "paid_time": None, "pay_via": "PAYMENT"}
        elif waiting:
            out[k] = {"pay_status": "CHECKING", "paid_date": waiting[0].deposit_date, "paid_time": waiting[0].deposit_time, "pay_via": None}
        elif any(g[0] > 0 for g in got):
            out[k] = {"pay_status": "PARTIAL", "paid_date": last, "paid_time": None, "pay_via": "PAYMENT"}
        else:
            out[k] = {"pay_status": "OVERDUE" if today > doc.payment_deadline else "UNPAID", "paid_date": None, "paid_time": None, "pay_via": None}
        st = out[k]
        open_ = st["pay_status"] in ("UNPAID", "PARTIAL", "OVERDUE")
        st["overdue"] = open_ and today > doc.payment_deadline
        st["days_left"] = count_business_days(today, doc.payment_deadline, holidays) if today <= doc.payment_deadline else None
        st["due_soon"] = open_ and not st["overdue"] and st["days_left"] is not None and st["days_left"] <= DUE_SOON_DAYS
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
        pay = payment_states(db, docs, as_of=as_of)
        soon = sorted((_item(d, dorms[k[0]], names) | pay[k] for k, d in docs.items() if pay.get(k, {}).get("due_soon")),
                      key=lambda i: (i["payment_deadline"], i["dormitory_name"]))
        return {"scope": "FOUNDATION", "unreceived": {"count": len(pending), "items": pending},
                "due_soon_unpaid": {"count": len(soon), "items": soon, "days": DUE_SOON_DAYS}}
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
                   received: bool | None = None, dormitory_id: uuid.UUID | None = None, paid: str | None = None) -> dict:
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
    docs = {k: d for k, d in docs.items() if not ((date_from and k[1] < date_from) or (date_to and k[1] > date_to))}
    pay = payment_states(db, docs)
    items = []
    for (did, due), doc in docs.items():
        if received is not None and (doc.received_at is not None) != received:
            continue
        st = pay.get((did, due), {})
        # paid 필터: PAID(입금 완료) / CHECKING(확인 중) / UNPAID(미입금 — 일부납부·기한 경과 포함)
        if paid and not (st.get("pay_status") == paid or (paid == "UNPAID" and st.get("pay_status") in ("UNPAID", "OVERDUE", "PARTIAL"))):
            continue
        items.append(_item(doc, dorms[did], names) | st)
    items.sort(key=lambda i: (-i["due_date"].toordinal(), i["dormitory_name"]))
    return {"count": len(items), "unreceived": sum(1 for i in items if not i["received"]),
            "unpaid": sum(1 for i in items if i.get("pay_status") in ("UNPAID", "OVERDUE", "PARTIAL")), "items": items}
