"""
버전별 '상환예정일별 예정 금액' 집계와 버전 비교.

한 상환예정일에는 여러 행(연도경계 분리, 일부대환 두 트랙, 유예 회수 가산 등)이 있을 수 있으므로
실제로 납부해야 할 금액은 상환예정일 단위로 합산한다. 유예 회수 원금은 행의 principal_amount가
아니라 DeferralRecoveryRow에 따로 저장되어 있어 여기서 함께 더한다.
"""
import uuid
from collections import defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.event import DeferralRecoveryRow
from app.models.schedule import RepaymentScheduleVersion


class VersionNotFoundError(Exception):
    pass


def get_version_with_rows(db: Session, loan_id: uuid.UUID, version_no: int) -> RepaymentScheduleVersion:
    v = db.execute(
        select(RepaymentScheduleVersion)
        .options(selectinload(RepaymentScheduleVersion.rows))
        .where(RepaymentScheduleVersion.loan_id == loan_id, RepaymentScheduleVersion.version_no == version_no,
               RepaymentScheduleVersion.is_deleted.is_(False))
    ).scalar_one_or_none()
    if v is None:
        raise VersionNotFoundError(f"V{version_no} 버전을 찾을 수 없습니다.")
    return v


def scheduled_by_due_date(db: Session, version: RepaymentScheduleVersion) -> dict[date, dict[str, int]]:
    cache = db.info.get("read_cache")
    if cache is not None and ("amt", version.id) in cache:
        return cache[("amt", version.id)]
    result = _scheduled_by_due_date(db, version)
    if cache is not None:
        cache[("amt", version.id)] = result
    return result


def _scheduled_by_due_date(db: Session, version: RepaymentScheduleVersion) -> dict[date, dict[str, int]]:
    row_ids = [r.id for r in version.rows]
    recovery_by_row: dict[uuid.UUID, int] = defaultdict(int)
    cache = db.info.get("read_cache")
    if cache is not None and ("rec", version.id) in cache:
        recovery_by_row.update(cache[("rec", version.id)])
    elif row_ids:
        for rr in db.execute(select(DeferralRecoveryRow).where(DeferralRecoveryRow.schedule_row_id.in_(row_ids))).scalars():
            recovery_by_row[rr.schedule_row_id] += rr.recovery_principal

    out: dict[date, dict[str, int]] = defaultdict(lambda: {"principal": 0, "interest": 0, "total": 0})
    for r in version.rows:
        principal = r.principal_amount + recovery_by_row.get(r.id, 0)
        interest = r.interest_amount + r.unpaid_interest_amount
        slot = out[r.due_date]
        slot["principal"] += principal
        slot["interest"] += interest
        slot["total"] += principal + interest
    return dict(out)


def compare_versions(db: Session, loan_id: uuid.UUID, from_no: int, to_no: int) -> dict:
    va = get_version_with_rows(db, loan_id, from_no)
    vb = get_version_with_rows(db, loan_id, to_no)
    a, b = scheduled_by_due_date(db, va), scheduled_by_due_date(db, vb)

    changes = []
    for d in sorted(set(a) | set(b)):
        x = a.get(d)
        y = b.get(d)
        if x == y:
            continue
        if x is None:
            kind = "ADDED"
        elif y is None or (y["total"] == 0 and x["total"] != 0):
            kind = "REMOVED"
        else:
            kind = "CHANGED"
        zero = {"principal": 0, "interest": 0, "total": 0}
        x, y = x or zero, y or zero
        changes.append({
            "due_date": d, "change_type": kind, "from": x, "to": y,
            "delta_principal": y["principal"] - x["principal"],
            "delta_interest": y["interest"] - x["interest"],
            "delta_total": y["total"] - x["total"],
        })

    def summarize(v, amounts):
        dates = [d for d, m in amounts.items() if m["total"] > 0]
        last_row = max(v.rows, key=lambda r: r.seq_no) if v.rows else None
        return {
            "version_no": v.version_no, "generated_reason": v.generated_reason, "status": v.status.value,
            "row_count": len(v.rows),
            "total_principal": sum(m["principal"] for m in amounts.values()),
            "total_interest": sum(m["interest"] for m in amounts.values()),
            "last_payment_date": max(dates) if dates else None,
            "final_balance": last_row.balance_after if last_row else None,
        }

    sa, sb = summarize(va, a), summarize(vb, b)
    return {
        "from_version": sa, "to_version": sb,
        "diff": {
            "total_principal": sb["total_principal"] - sa["total_principal"],
            "total_interest": sb["total_interest"] - sa["total_interest"],
            "changed_due_dates": len(changes),
        },
        "changes": changes,
    }
