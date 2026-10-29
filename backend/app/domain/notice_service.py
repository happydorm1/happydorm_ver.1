"""
안내공문 자동생성(§28~29) — 기숙사 + 상환예정일을 고르면 공문의 모든 숫자를 원리금 테이블에서 자동 연결한다.
수기 입력은 없다. (첨부2에서는 숫자를 손으로 옮겨 적어 이자 한 건이 원본과 7원 어긋나 있었다 — 이를 없애는 것이 목적)

한 기숙사에 차입금이 여러 건(재원별)일 수 있으므로 공문은 '기숙사 + 상환일' 단위로, 해당 상환일에
청구액이 있는 모든 차입금을 재원별 행으로 모아 합계까지 계산한다.
"""
import uuid
from collections import defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import protect_account, reveal_account
from app.domain.audit import record_audit
from app.domain.calendar_service import load_holidays, years_without_holidays
from app.domain.schedule_amounts import scheduled_by_due_date
from app.domain.version_utils import NoScheduleError, get_current_version
from app.engine.business_day import effective_due_date, is_business_day, non_business_reason
from app.models.document import DocumentTemplate, GeneratedDocument
from app.models.event import Acceleration, LoanEvent, LoanEventStatus
from app.models.loan import Loan
from app.models.notification import Notification
from app.models.organization import Dormitory, User, UserRole
from app.models.reference import Code, Fund, InterestMethod

IPMT_NOTE = "초록 음영표시한 융자 건의 경우, IPMT함수를 적용해 이자 산출함"
DAY_COUNT_NOTE = "초일 산입, 말일 불산입"


class NoticeError(Exception):
    pass


def _uses_ipmt(db: Session, loan: Loan, fund: Fund, due_date: date) -> bool:
    """이자를 IPMT(원리금균등)로 산출하는 차입금인가. 원금가속상환이 시작된 이후에는 단리로 전환되므로 제외."""
    method = db.get(Code, loan.repay_method_code_id)
    is_equal_payment = fund.interest_method == InterestMethod.PPMT_IPMT and not (method and method.code == "EQUAL_PRINCIPAL")
    if not is_equal_payment:
        return False
    accelerated = db.execute(
        select(Acceleration.id)
        .join(LoanEvent, LoanEvent.id == Acceleration.loan_event_id)
        .where(LoanEvent.loan_id == loan.id, LoanEvent.status == LoanEventStatus.APPLIED,
               Acceleration.start_due_date <= due_date)
    ).first()
    return accelerated is None


def build_notice_data(
    db: Session, dormitory: Dormitory, due_date: date, template: DocumentTemplate, *,
    deadline_override: date | None = None, account_override: dict | None = None,
) -> dict:
    loans = db.execute(
        select(Loan).where(Loan.dormitory_id == dormitory.id, Loan.is_deleted.is_(False)).order_by(Loan.loan_code)
    ).scalars().all()

    items = []
    for loan in loans:
        try:
            version = get_current_version(db, loan.id)
        except NoScheduleError:
            continue
        amounts = scheduled_by_due_date(db, version).get(due_date)
        if not amounts or amounts["total"] <= 0:
            continue

        rows = sorted((r for r in version.rows if r.due_date == due_date), key=lambda r: r.seq_no)
        by_period: dict[tuple, list] = defaultdict(list)
        for r in rows:
            by_period[(r.interest_calc_start, r.interest_calc_end)].append(r)
        first_key = min(by_period, key=lambda k: k[0])
        last_key = max(by_period, key=lambda k: k[1])
        # 같은 기간의 행이 둘 이상이면 일부대환 등으로 트랙이 나뉜 것 → 잔액은 합산, 연도경계 분리행은 첫 구간 기준
        balance_before = sum(r.balance_before for r in by_period[first_key])
        balance_after = sum(r.balance_after for r in by_period[last_key])
        principal_row = next((r for r in reversed(rows) if r.principal_amount > 0), rows[-1])

        fund = db.get(Fund, loan.fund_id)
        items.append({
            "loan": loan, "fund": fund,
            "start": min(r.interest_calc_start for r in rows), "end": max(r.interest_calc_end for r in rows),
            "balance_before": balance_before, "balance_after": balance_after,
            "principal": amounts["principal"], "interest": amounts["interest"],
            "rate": float(principal_row.applied_rate), "version_no": version.version_no,
            "ipmt": _uses_ipmt(db, loan, fund, due_date),
        })

    if not items:
        raise NoticeError(f"{dormitory.dormitory_name}에는 {due_date}에 청구할 상환액이 있는 차입금이 없습니다.")

    items.sort(key=lambda x: (x["fund"].fund_name, x["loan"].loan_code))
    fund_counts: dict[str, int] = defaultdict(int)
    for it in items:
        fund_counts[it["fund"].fund_name] += 1
    for it in items:
        name = it["fund"].fund_name
        it["label"] = name if fund_counts[name] == 1 else f"{name} ({it['loan'].loan_code})"

    cfg = dict(reveal_account(template.config) or {})   # 양식의 계좌는 암호화 저장
    if account_override:
        cfg.update({k: v for k, v in account_override.items() if v})
    if not cfg.get("bank_account") or not cfg.get("account_holder"):
        raise NoticeError("공문 양식에 입금계좌(bank_account)와 예금주(account_holder)가 등록되어 있지 않습니다.")

    holidays = load_holidays(db)
    warnings = []
    # 확정 업무기준: 상환일이 휴일이면 다음 영업일로 납부기한을 지정한다. 담당자가 직접 지정(override)하면 그 값을 쓴다.
    auto_deadline = effective_due_date(due_date, holidays)
    deadline = deadline_override or auto_deadline
    adjusted = deadline_override is None and auto_deadline != due_date
    if adjusted:
        warnings.append(
            f"상환예정일 {due_date}이(가) 휴일({non_business_reason(due_date, holidays)})이어서 "
            f"납부기한을 다음 영업일 {auto_deadline}로 지정했습니다."
        )
    if deadline_override is not None and not is_business_day(deadline, holidays):
        warnings.append(
            f"직접 지정한 납부기한 {deadline}은(는) 영업일이 아닙니다({non_business_reason(deadline, holidays)}). 확인하세요."
        )
    missing = years_without_holidays(db, {deadline.year})
    if missing:
        warnings.append(f"{', '.join(map(str, missing))}년 공휴일이 등록되어 있지 않아 영업일 판단이 주말만 반영됩니다.")

    quarter = (due_date.month - 1) // 3 + 1
    title = f"{dormitory.dormitory_name} '{due_date.year % 100:02d}년 {quarter}분기 원리금 계산내역"

    notes = [IPMT_NOTE] if any(i["ipmt"] for i in items) else []

    def line(it):
        return it["label"]

    return {
        "title": title,
        "dormitory": {"id": str(dormitory.id), "name": dormitory.dormitory_name, "school": dormitory.school_name},
        "due_date": due_date.isoformat(),
        "payment_deadline": deadline.isoformat(),
        "payment_deadline_adjusted": adjusted,
        "account": {"bank_account": cfg["bank_account"], "account_holder": cfg["account_holder"]},
        "interest_periods": [
            {"label": line(i), "start": i["start"].isoformat(), "end": i["end"].isoformat(),
             "days": (i["end"] - i["start"]).days, "note": DAY_COUNT_NOTE, "ipmt": i["ipmt"]}
            for i in items
        ],
        "balances": [
            {"label": line(i), "assigned_amount": i["loan"].assigned_amount, "balance_before": i["balance_before"],
             "principal": i["principal"], "balance_after": i["balance_after"],
             "check": i["balance_before"] - i["principal"] == i["balance_after"], "ipmt": i["ipmt"]}
            for i in items
        ],
        "payments": [
            {"label": line(i), "balance_before": i["balance_before"], "rate": i["rate"], "principal": i["principal"],
             "interest": i["interest"], "total": i["principal"] + i["interest"], "ipmt": i["ipmt"]}
            for i in items
        ],
        "totals": {
            "balance_before": sum(i["balance_before"] for i in items),
            "principal": sum(i["principal"] for i in items),
            "interest": sum(i["interest"] for i in items),
            "total": sum(i["principal"] + i["interest"] for i in items),
        },
        "notes": notes,
        "warnings": warnings,
        "sources": [{"loan_id": str(i["loan"].id), "loan_code": i["loan"].loan_code, "schedule_version_no": i["version_no"]} for i in items],
    }


def get_active_template(db: Session, template_id: uuid.UUID | None) -> DocumentTemplate:
    if template_id is not None:
        t = db.get(DocumentTemplate, template_id)
        if t is None or t.is_deleted or not t.is_active:
            raise NoticeError("공문 양식을 찾을 수 없습니다.")
        return t
    t = db.execute(
        select(DocumentTemplate).where(DocumentTemplate.is_deleted.is_(False), DocumentTemplate.is_active.is_(True),
                                       DocumentTemplate.template_type == "REPAYMENT_NOTICE")
        .order_by(DocumentTemplate.created_at.desc())
    ).scalars().first()
    if t is None:
        raise NoticeError("활성 공문 양식이 없습니다. 먼저 양식(입금계좌 포함)을 등록하세요.")
    return t


def issue_notice(
    db: Session, dormitory: Dormitory, due_date: date, template: DocumentTemplate, *,
    deadline_override: date | None, account_override: dict | None, user_id: uuid.UUID | None,
) -> GeneratedDocument:
    data = build_notice_data(db, dormitory, due_date, template, deadline_override=deadline_override,
                             account_override=account_override)
    doc = GeneratedDocument(
        dormitory_id=dormitory.id, template_id=template.id, due_date=due_date,
        payment_deadline=date.fromisoformat(data["payment_deadline"]), title=data["title"],
        generated_data={**data, "account": protect_account(data["account"])}, created_by=user_id,   # 스냅샷의 계좌도 암호화
    )
    db.add(doc)
    db.flush()
    for src in data["sources"]:  # 공문에 포함된 차입금마다 발급 이력을 남긴다(차입금별 변경이력에서 조회 가능)
        record_audit(db, table_name="generated_document", record_id=doc.id, loan_id=uuid.UUID(src["loan_id"]),
                     action="CREATE", user_id=user_id, field="title", before=None,
                     after=f"{data['title']} / 납부기한 {data['payment_deadline']}")
    notified = notify_notice_issued(db, dormitory, doc, data)
    db.commit()
    db.refresh(doc)
    doc.notified_count = notified          # 응답용(저장 칼럼 아님)
    return doc


def notify_notice_issued(db: Session, dormitory: Dormitory, doc: GeneratedDocument, data: dict) -> int:
    """공문 발급 → 그 기숙사 SPC 담당자에게 시스템 알림(알림함). 같은 상환일 공문을 다시 발급하면 기존 알림을 새 내용으로 갱신."""
    if not data.get("sources"):
        return 0
    users = db.execute(select(User).where(User.role == UserRole.SPC_MANAGER, User.spc_id == dormitory.spc_id,
                                          User.is_active.is_(True), User.is_deleted.is_(False))).scalars().all()
    loan_id = uuid.UUID(data["sources"][0]["loan_id"])
    t = data["totals"]
    title = f"[안내공문] {doc.title}"
    body = (f"상환예정일 {doc.due_date} · 납부기한 {doc.payment_deadline} · 납부금액 {t['total']:,}원"
            f"(원금 {t['principal']:,} / 이자 {t['interest']:,}). 안내공문 메뉴의 발급 이력에서 Excel·PDF로 내려받을 수 있습니다.")
    payload = {"notice_id": str(doc.id), "dormitory_id": str(dormitory.id), "due_date": str(doc.due_date),
               "payment_deadline": str(doc.payment_deadline), "total": t["total"]}
    for u in users:
        n = db.execute(select(Notification).where(Notification.recipient_user_id == u.id, Notification.loan_id == loan_id,
                                                  Notification.notification_type == "NOTICE_ISSUED",
                                                  Notification.reference_date == doc.due_date)).scalar_one_or_none()
        if n is None:
            db.add(Notification(recipient_user_id=u.id, loan_id=loan_id, dormitory_id=dormitory.id,
                                notification_type="NOTICE_ISSUED", reference_date=doc.due_date, trigger_date=date.today(),
                                title=title, body=body, payload=payload, created_by=doc.created_by))
        else:
            n.title, n.body, n.payload, n.trigger_date = title, body, payload, date.today()
            n.is_read, n.read_at = False, None
    return len(users)
