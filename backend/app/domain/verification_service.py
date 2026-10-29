"""
원리금 테이블 계산 검증(§37) — 9개 항목을 현재 버전(또는 지정 버전)에 대해 다시 확인한다.
계산 시점의 엔진 검증(1·2·5·6번, 실패 시 '검토 필요')에 더해, DB에 저장된 결과를 기준으로 금리·중도변수·버전 차이까지 본다.
결과: 항목별 PASS / FAIL / NA(해당 없음)와 근거. 하나라도 FAIL이면 overall = NEEDS_REVIEW(검토 필요).
"""
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.domain.schedule_amounts import scheduled_by_due_date
from app.models.event import DeferralRecoveryRow, LoanEvent, LoanEventStatus, LoanEventType, Refinancing
from app.models.loan import InterestRateHistory, Loan
from app.models.schedule import RepaymentScheduleVersion, RowType

TITLES = {
    1: "총 원금상환액 = 배정금액",
    2: "최종 잔여원금 = 0원",
    3: "원리금 = 원금 + 이자",
    4: "이자상환액 = 당기이자 + 미지급이자",
    5: "상환 후 잔액 = 상환 전 잔액 - 원금상환액",
    6: "이자계산일수 = 종료일 - 시작일",
    7: "적용금리 = 해당 기간 금리",
    8: "중도변수 적용 여부",
    9: "버전 간 계산 차이(지난 회차 불변)",
}


def _res(no, status, detail, items=None):
    return {"no": no, "title": TITLES[no], "status": status, "detail": detail, "items": (items or [])[:20]}


def verify_version(db: Session, loan: Loan, version: RepaymentScheduleVersion) -> dict:
    rows = sorted(version.rows, key=lambda r: r.seq_no)
    real = [r for r in rows if r.row_type != RowType.YEAR_END_SPLIT_MEMO]
    amounts = scheduled_by_due_date(db, version)
    out = []

    total_p = sum(m["principal"] for m in amounts.values())
    out.append(_res(1, "PASS" if total_p == loan.assigned_amount else "FAIL",
                    f"원금 합계 {total_p:,}원 / 배정금액 {loan.assigned_amount:,}원"))
    final = rows[-1].balance_after if rows else None
    out.append(_res(2, "PASS" if final == 0 else "FAIL", f"최종 잔여원금 {final:,}원" if final is not None else "회차 없음"))

    bad3 = [d.isoformat() for d, m in amounts.items() if m["total"] != m["principal"] + m["interest"]]
    neg = [r.due_date.isoformat() for r in rows if min(r.principal_amount, r.interest_amount, r.unpaid_interest_amount, r.balance_after) < 0]
    out.append(_res(3, "PASS" if not (bad3 or neg) else "FAIL",
                    "상환일별 원리금 = 원금 + 이자, 음수 금액 없음" if not (bad3 or neg) else "불일치·음수 금액 회차 있음", bad3 + neg))
    bad4 = [d.isoformat() for d in amounts
            if amounts[d]["interest"] != sum(r.interest_amount + r.unpaid_interest_amount for r in rows if r.due_date == d)]
    unpaid = sum(r.unpaid_interest_amount for r in rows)
    out.append(_res(4, "PASS" if not bad4 else "FAIL", f"미지급이자 포함 회차 {sum(1 for r in rows if r.unpaid_interest_amount)}개, "
                                                      f"미지급이자 합계 {unpaid:,}원", bad4))
    rec: dict = {}
    if rows:
        for rr in db.execute(select(DeferralRecoveryRow).where(DeferralRecoveryRow.schedule_row_id.in_([r.id for r in rows]))).scalars():
            rec[rr.schedule_row_id] = rec.get(rr.schedule_row_id, 0) + rr.recovery_principal
    bad5 = [r.due_date.isoformat() for r in real if r.balance_before - r.principal_amount - rec.get(r.id, 0) != r.balance_after]
    out.append(_res(5, "PASS" if not bad5 else "FAIL",
                    f"{len(real)}개 회차 확인" + (f"(상환유예 회수원금 {sum(rec.values()):,}원 포함)" if rec else ""), bad5))
    bad6 = [r.due_date.isoformat() for r in rows if r.days != (r.interest_calc_end - r.interest_calc_start).days]
    out.append(_res(6, "PASS" if not bad6 else "FAIL", f"{len(rows)}개 행 확인", bad6))

    hist = db.execute(select(InterestRateHistory).where(InterestRateHistory.loan_id == loan.id, InterestRateHistory.is_deleted.is_(False))
                      ).scalars().all()
    blended = db.execute(select(Refinancing.id).join(LoanEvent, LoanEvent.id == Refinancing.loan_event_id)
                         .where(LoanEvent.loan_id == loan.id, LoanEvent.status == LoanEventStatus.APPLIED,
                                Refinancing.refi_type == "PARTIAL")).first() is not None
    if not version.is_current:
        out.append(_res(7, "NA", f"과거 버전(V{version.version_no}) — 금리 이력은 현재 기준이라 지난 버전에는 적용하지 않음"))
    elif blended:
        out.append(_res(7, "NA", "일부대환 차입금: 전환분(신금리)·잔류분(구금리)이 섞여 회차 금리를 이력 하나와 비교할 수 없음"))
    else:
        bad7 = []
        # 대환일에 끝나는 이자기간은 옛 금리로 계산한다(첨부7 확인) — 그 경계 행만 '시작일 < 종료일' 기준으로 본다
        refi_dates = {e.event_date for e in db.execute(select(LoanEvent).where(
            LoanEvent.loan_id == loan.id, LoanEvent.status == LoanEventStatus.APPLIED,
            LoanEvent.event_type == LoanEventType.REFINANCING)).scalars()}
        for r in real:
            strict = r.interest_calc_end in refi_dates
            cand = [h for h in hist if (h.effective_start_date < r.interest_calc_end if strict else h.effective_start_date <= r.interest_calc_end)]
            h = max(cand, key=lambda x: x.effective_start_date) if cand else None
            if h is None or Decimal(str(r.applied_rate)) != Decimal(str(h.annual_rate)):
                bad7.append(f"{r.due_date} {float(r.applied_rate)}% ≠ 이력 {float(h.annual_rate) if h else '없음'}%")
        out.append(_res(7, "PASS" if not bad7 else "FAIL",
                        f"금리 이력 {len(hist)}건 기준 {len(real)}개 회차 확인" + ("" if not bad7 else " — 금리변동 이벤트 미적용 가능"), bad7))

    events = db.execute(select(LoanEvent).where(LoanEvent.loan_id == loan.id, LoanEvent.status == LoanEventStatus.APPLIED)
                        .order_by(LoanEvent.created_at)).scalars().all()
    nos = {v.id: v.version_no for v in db.execute(select(RepaymentScheduleVersion).where(RepaymentScheduleVersion.loan_id == loan.id)).scalars()}
    missing = [f"{e.event_type.value} {e.event_date}" for e in events if e.resulting_version_id not in nos]
    later = [f"{e.event_type.value} {e.event_date}" for e in events
             if e.resulting_version_id in nos and nos[e.resulting_version_id] > version.version_no]
    st8 = "FAIL" if missing else "PASS"
    d8 = (f"적용된 중도변수 {len(events)}건 모두 원리금 테이블 버전으로 반영" if events else "적용된 중도변수 없음")
    if later and version.is_current is False:
        d8 += f" (이 버전 이후 반영 {len(later)}건)"
    out.append(_res(8, st8, d8, missing))

    prev = db.get(RepaymentScheduleVersion, version.previous_version_id) if version.previous_version_id else None
    if prev is None:
        out.append(_res(9, "NA", f"V{version.version_no}: 이전 버전 없음({version.generated_reason})"))
    else:
        a, b = scheduled_by_due_date(db, prev), scheduled_by_due_date(db, version)
        changed = sorted(d for d in set(a) | set(b) if a.get(d) != b.get(d))
        ev = next((e for e in db.execute(select(LoanEvent).where(LoanEvent.resulting_version_id == version.id)).scalars()), None)
        summary = (f"V{prev.version_no}→V{version.version_no}({version.generated_reason}): 바뀐 상환일 {len(changed)}개"
                   + (f", 첫 변경 {changed[0]}" if changed else "")
                   + f", 원금 {sum(m['principal'] for m in b.values()) - sum(m['principal'] for m in a.values()):+,}원"
                   + f", 이자 {sum(m['interest'] for m in b.values()) - sum(m['interest'] for m in a.values()):+,}원")
        if ev is None:
            out.append(_res(9, "PASS", summary + " (이벤트 취소·재생성 버전)"))
        else:
            broken = [d.isoformat() for d in changed if d < ev.event_date]
            out.append(_res(9, "PASS" if not broken else "FAIL",
                            summary + f" — 이벤트일 {ev.event_date} 이전 회차 " + ("변경 없음" if not broken else f"{len(broken)}개 변경됨"), broken))

    fails = [c["no"] for c in out if c["status"] == "FAIL"]
    return {"loan_id": loan.id, "loan_code": loan.loan_code, "version_no": version.version_no, "version_status": version.status.value,
            "overall": "NORMAL" if not fails else "NEEDS_REVIEW", "failed": fails, "checks": out}


def verify_loan(db: Session, loan_id: uuid.UUID, version_no: int | None = None) -> dict | None:
    loan = db.get(Loan, loan_id)
    q = select(RepaymentScheduleVersion).options(selectinload(RepaymentScheduleVersion.rows)).where(
        RepaymentScheduleVersion.loan_id == loan_id, RepaymentScheduleVersion.is_deleted.is_(False))
    q = q.where(RepaymentScheduleVersion.version_no == version_no) if version_no else q.where(RepaymentScheduleVersion.is_current.is_(True))
    v = db.execute(q).scalar_one_or_none()
    return verify_version(db, loan, v) if (loan and v) else None
