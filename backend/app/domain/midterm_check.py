"""
중도변수 미처리 판정(업무기준 확정 2026-10-28: 미처리 시 알림 팝업).

'미처리' = 중도에 생긴 변화가 원리금 테이블(현재 버전)에 아직 반영되지 않은 상태. 아래 네 가지를 본다.
  RATE_NOT_REGISTERED  변동금리 구간이 끝났는데(변경일 경과) 다음 금리가 등록되지 않음
  RATE_NOT_APPLIED     금리 이력은 등록됐지만 앞으로 도래할 회차가 아직 예전 금리로 계산돼 있음(금리변동 이벤트 미적용)
  OVERPAID             예정액보다 더 낸 회차가 있음 → 조기상환 이벤트 등록 여부 확인
  PAYMENT_UNMATCHED    이벤트로 상환일이 바뀌어, 기존 납부가 현재 원리금 테이블의 어느 회차와도 연결되지 않음
  APPROVAL_PENDING     (결재 모드) 기안된 이벤트가 아직 승인·반려되지 않음
이미 지난 회차는 재계산 대상이 아니므로(과거 회차 불변 원칙) RATE_NOT_APPLIED는 기준일 이후 회차만 본다.
"""
from datetime import date
from decimal import Decimal

from app.models.loan import Loan, RateType
from app.models.schedule import RowType

LABELS = {
    "RATE_NOT_REGISTERED": "금리변동 미등록",
    "RATE_NOT_APPLIED": "금리변동 미반영",
    "OVERPAID": "조기상환 미등록 의심",
    "PAYMENT_UNMATCHED": "납부 회차 연결 필요",
    "APPROVAL_PENDING": "결재 대기",
}


def _rate_on(hist, d: date):
    """rate_service.get_applicable_rate와 같은 규칙(이자계산종료일 기준, 시작일이 가장 늦은 이력)."""
    cand = [h for h in hist if h.effective_start_date <= d]
    return max(cand, key=lambda h: h.effective_start_date) if cand else None


def find_midterm_pending(loan: Loan, version, hist, summary: dict | None, as_of: date, base: dict,
                         *, blended_rates: bool = False, pending_requests=()) -> list[dict]:
    """hist: 삭제되지 않은 금리 이력(시작일 순). summary: payment_summary 결과(없으면 납부 관련 점검 생략).
    blended_rates: 일부 대환 적용 차입금 — 전환분(신금리)·잔여분(구금리)이 섞여 회차 금리가 이력과 다를 수 있어 RATE_NOT_APPLIED 점검 제외."""
    out = []

    def add(kind: str, ref: str, detail: str, **extra):
        out.append({**base, "kind": kind, "label": LABELS[kind], "detail": detail,
                    "key": f"MIDTERM:{kind}:{loan.id}:{ref}", **extra})

    current = _rate_on(hist, as_of)
    if loan.rate_type == RateType.VARIABLE and current and current.effective_end_date and current.effective_end_date <= as_of:
        nxt = [h for h in hist if h.effective_start_date > current.effective_start_date]
        if not nxt:
            add("RATE_NOT_REGISTERED", current.effective_end_date.isoformat(),
                f"금리 변경일 {current.effective_end_date} 경과 — 새 금리가 등록되지 않았습니다(현재 {float(current.annual_rate)}% 유지 중).",
                change_date=current.effective_end_date)

    if version is not None and hist and not blended_rates:
        for r in sorted(version.rows, key=lambda r: r.seq_no):
            if r.due_date < as_of or r.row_type == RowType.YEAR_END_SPLIT_MEMO:
                continue
            h = _rate_on(hist, r.interest_calc_end)
            if h is not None and Decimal(str(r.applied_rate)) != Decimal(str(h.annual_rate)):
                add("RATE_NOT_APPLIED", h.effective_start_date.isoformat(),
                    f"{h.effective_start_date}부터 {float(h.annual_rate)}% 등록 — 원리금 테이블 V{version.version_no}의 "
                    f"{r.due_date} 회차는 {float(r.applied_rate)}%로 계산돼 있습니다. 금리변동 이벤트를 적용하세요.",
                    due_date=r.due_date)
                break

    if summary:
        for row in summary["rows"]:
            if row["status"] == "OVERPAID":
                add("OVERPAID", row["due_date"].isoformat(),
                    f"{row['due_date']} 회차 예정액보다 {row['difference']:,}원 더 납부 — 조기상환이면 조기상환 이벤트를 등록하세요.",
                    due_date=row["due_date"], amount=row["difference"])
        for u in summary.get("unmatched_payments", []):
            add("PAYMENT_UNMATCHED", u["due_date"].isoformat(),
                f"{u['due_date']} 납부 {u['paid_total']:,}원이 현재 원리금 테이블 회차와 맞지 않습니다(이벤트로 상환일 변경). 납부 회차를 정정하세요.",
                due_date=u["due_date"], amount=u["paid_total"])
    for r in pending_requests:
        from app.domain.event_dispatch import LABELS as EV
        add("APPROVAL_PENDING", str(r.id), f"{EV.get(r.event_type, r.event_type)} 기안({r.created_at:%Y-%m-%d})이 결재 대기 중입니다. "
                                           "승인해야 원리금 테이블에 반영됩니다.", request_id=r.id)
    return out
