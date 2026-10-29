"""이벤트(가속상환/조기상환/대환 등)가 새 원리금 테이블 버전을 만들 때 공통으로 쓰는 헬퍼."""
import uuid
from contextlib import contextmanager

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.schedule import (
    RepaymentScheduleRow,
    RepaymentScheduleVersion,
    ScheduleVersionStatus,
)


class NoScheduleError(Exception):
    pass


@contextmanager
def read_cache(db: Session, loan_ids=None):
    """
    조회 전용 묶음(대시보드·알림 요약·팝업) 동안 현재 버전과 상환일별 금액을 한 번만 읽도록 하는 캐시.
    loan_ids를 주면 현재 버전 전부를 쿼리 2번(버전 + 회차)으로 미리 읽는다. 블록이 끝나면 캐시는 사라진다(쓰기 작업에는 쓰지 않음).
    """
    owner = "read_cache" not in db.info
    if owner:
        db.info["read_cache"] = {}
    cache = db.info["read_cache"]
    try:
        if loan_ids:
            todo = [i for i in loan_ids if ("cv", i) not in cache]
            if todo:
                for v in db.execute(select(RepaymentScheduleVersion).options(selectinload(RepaymentScheduleVersion.rows))
                                    .where(RepaymentScheduleVersion.loan_id.in_(todo), RepaymentScheduleVersion.is_current.is_(True))).scalars():
                    cache[("cv", v.loan_id)] = v
                for i in todo:
                    cache.setdefault(("cv", i), None)
                _prefetch_related(db, cache, todo)
        yield cache
    finally:
        if owner:
            db.info.pop("read_cache", None)


def get_current_version(db: Session, loan_id: uuid.UUID) -> RepaymentScheduleVersion:
    cache = db.info.get("read_cache")
    if cache is not None and ("cv", loan_id) in cache:
        if cache[("cv", loan_id)] is None:
            raise NoScheduleError("원리금 테이블이 없습니다. 먼저 스케줄을 생성하세요.")
        return cache[("cv", loan_id)]
    version = db.execute(
        select(RepaymentScheduleVersion)
        .options(selectinload(RepaymentScheduleVersion.rows))
        .where(RepaymentScheduleVersion.loan_id == loan_id, RepaymentScheduleVersion.is_current.is_(True))
    ).scalar_one_or_none()
    if cache is not None:
        cache[("cv", loan_id)] = version
    if version is None:
        raise NoScheduleError("원리금 테이블이 없습니다. 먼저 스케줄을 생성하세요.")
    return version


def start_next_version(
    db: Session, current: RepaymentScheduleVersion, reason: str, created_by: uuid.UUID | None
) -> RepaymentScheduleVersion:
    """기존 버전은 보존(is_current만 해제)하고 V+1을 만든다(26/43번 원칙)."""
    new_version = RepaymentScheduleVersion(
        loan_id=current.loan_id,
        version_no=current.version_no + 1,
        generated_reason=reason,
        previous_version_id=current.id,
        is_current=True,
        status=ScheduleVersionStatus.NORMAL,
        created_by=created_by,
    )
    db.add(new_version)
    current.is_current = False
    db.flush()
    return new_version


def copy_row(old: RepaymentScheduleRow, version_id: uuid.UUID, seq_no: int, created_by) -> RepaymentScheduleRow:
    return RepaymentScheduleRow(
        version_id=version_id,
        seq_no=seq_no,
        due_date=old.due_date,
        interest_calc_start=old.interest_calc_start,
        interest_calc_end=old.interest_calc_end,
        days=old.days,
        installment_no=old.installment_no,
        balance_before=old.balance_before,
        principal_amount=old.principal_amount,
        interest_amount=old.interest_amount,
        unpaid_interest_amount=old.unpaid_interest_amount,
        balance_after=old.balance_after,
        applied_rate=old.applied_rate,
        row_type=old.row_type,
        memo_interest=old.memo_interest,
        remark=old.remark,
        created_by=created_by,
    )


def _prefetch_related(db: Session, cache: dict, loan_ids: list) -> None:
    """상환유예 회수원금·금리이력·실제 납부를 차입금 전체에 대해 한 번씩만 읽어 캐시에 넣는다."""
    from collections import defaultdict

    from app.models.event import DeferralRecoveryRow
    from app.models.loan import InterestRateHistory
    from app.models.payment import ActualPayment, PaymentStatus

    versions = [cache[("cv", i)] for i in loan_ids if cache.get(("cv", i)) is not None]
    row_to_version = {r.id: v.id for v in versions for r in v.rows}
    rec = defaultdict(dict)
    if row_to_version:
        for rr in db.execute(select(DeferralRecoveryRow).where(DeferralRecoveryRow.schedule_row_id.in_(list(row_to_version)))).scalars():
            d = rec[row_to_version[rr.schedule_row_id]]
            d[rr.schedule_row_id] = d.get(rr.schedule_row_id, 0) + rr.recovery_principal
    for v in versions:
        cache[("rec", v.id)] = dict(rec.get(v.id, {}))
    hist = defaultdict(list)
    for h in db.execute(select(InterestRateHistory).where(InterestRateHistory.loan_id.in_(loan_ids), InterestRateHistory.is_deleted.is_(False))
                        .order_by(InterestRateHistory.effective_start_date)).scalars():
        hist[h.loan_id].append(h)
    pay = defaultdict(list)
    for p in db.execute(select(ActualPayment).where(ActualPayment.loan_id.in_(loan_ids), ActualPayment.status == PaymentStatus.ACTIVE)).scalars():
        pay[p.loan_id].append(p)
    for i in loan_ids:
        cache[("hist", i)] = hist.get(i, [])
        cache[("pay", i)] = pay.get(i, [])
