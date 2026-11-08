"""
Phase 23: 입금 확인·승인 — 은행 입금내역을 발급한 안내공문과 대사(짝짓기)해 상환 실적으로 연결한다.

업무 기준(2026-11 확정)
- 대사 단위: 공문 한 건(기숙사의 분기 원리금 합계)
- 자동 승인: 공문 합계 2,000만 원 미만 + 입금액이 공문 합계와 1원까지 같음 + 납부기한 안 + 그 밖의 확인사항 없음
- 그 밖에는 '대기'로 접수하고 재단 자금담당자(시스템관리자 포함, SPC 담당자 아님)가 확인·승인
  · 2,000만 원 이상 / 금액 불일치 / 납부기한 경과(연체이자 정산 기준 확정 전) / 같은 금액 공문 여러 건 /
    대상 공문 없음(분할·합산 입금 포함) / 이미 납부 기록 있음 / 공문 발급 후 원리금 테이블 변경
- 완료되면 공문의 차입금별 원금·이자로 납부 기록(ActualPayment)을 만든다. 승인 취소 시 그 납부 기록도 취소한다.
- 은행 연동(펌뱅킹·가상계좌)이 생기면 '입금내역을 가져오는 부분'만 바꾸면 된다(add_deposits).
"""
import csv
import io
import re
import uuid
from datetime import date, datetime, timezone

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.audit import record_audit
from app.domain.schedule_amounts import scheduled_by_due_date
from app.domain.version_utils import NoScheduleError, get_current_version
from app.models.document import GeneratedDocument
from app.models.loan import Loan
from app.models.organization import SPC, Dormitory
from app.models.payment import ActualPayment, BankDeposit, DepositStatus, PaymentStatus

AUTO_APPROVE_LIMIT = 20_000_000   # 공문 합계가 이 금액 '미만'이면 자동 승인 대상

REASONS = {
    "OVER_LIMIT": "2,000만 원 이상 — 담당자 승인",
    "LATE": "납부기한 경과",
    "AMOUNT_MISMATCH": "공문 금액과 다름",
    "NO_MATCH": "대상 공문 없음(분할·합산 입금 등)",
    "MULTI_MATCH": "같은 금액 공문 여러 건",
    "ALREADY_PAID": "이미 납부 기록 있음",
    "SCHEDULE_CHANGED": "공문 발급 후 원리금 테이블 변경",
    "NOTICE_DONE": "다른 입금으로 이미 완료된 공문",
}


class DepositError(Exception):
    pass


# ---------- 입금내역 읽기 ----------
HEADS = {
    "date": ("입금일", "거래일", "거래일자", "거래일시", "입금일자", "일자"),
    "name": ("입금자명", "입금자", "보낸분", "의뢰인", "의뢰인명", "송금인"),
    "amount": ("입금액", "입금금액", "맡기신금액", "입금(원)", "입금"),
    "memo": ("적요", "메모", "비고", "내용", "거래내용"),
    # 있으면 중복 판단에 함께 쓴다 — 은행 거래내역의 거래시각·거래후 잔액은 거래마다 달라 같은 거래를 정확히 가려낸다
    "time": ("거래시각", "거래시간", "시각", "시간"),
    "balance": ("잔액", "거래후잔액", "거래후 잔액"),
    "txid": ("거래번호", "거래고유번호", "거래일련번호"),
}


def _norm(s) -> str:
    return re.sub(r"\s+", "", str(s or ""))


def _to_date(v) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v or "").strip()
    m = re.match(r"(\d{4})[.\-/년 ]\s*(\d{1,2})[.\-/월 ]\s*(\d{1,2})", s) or re.match(r"(\d{4})(\d{2})(\d{2})", s)
    if not m:
        raise ValueError(f"날짜를 읽을 수 없습니다: {s}")
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _to_int(v) -> int:
    if v is None or str(v).strip() == "":
        return 0
    if isinstance(v, (int, float)):
        return int(round(v))
    s = re.sub(r"[,원\s]", "", str(v))
    return int(round(float(s))) if s else 0


def read_rows(content: bytes, file_name: str) -> tuple[list[dict], list[dict]]:
    """거래내역 파일(xlsx·csv) → 입금 행. 머리글 줄을 찾아 입금일·입금자·입금액(·적요) 열을 고른다. 출금(입금액 0)은 건너뜀."""
    if file_name.lower().endswith(".csv"):
        text = None
        for enc in ("utf-8-sig", "cp949"):
            try:
                text = content.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise DepositError("CSV 파일의 글자 인코딩을 읽을 수 없습니다(UTF-8 또는 EUC-KR로 저장하세요).")
        table = list(csv.reader(io.StringIO(text)))
    else:
        try:
            wb = load_workbook(io.BytesIO(content), data_only=True, read_only=True)
        except Exception:
            raise DepositError("엑셀(.xlsx) 또는 CSV 파일만 올릴 수 있습니다. 은행 파일이 .xls면 엑셀에서 .xlsx로 다시 저장하세요.")
        table = [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]

    head_i, cols = None, {}
    for i, row in enumerate(table[:30]):
        names = [_norm(c) for c in row]
        found = {}
        for key, aliases in HEADS.items():
            for j, n in enumerate(names):
                if n in aliases and key not in found:
                    found[key] = j
        if {"date", "name", "amount"} <= set(found):
            head_i, cols = i, found
            break
    if head_i is None:
        raise DepositError("머리글(입금일·입금자명·입금액)을 찾지 못했습니다. '입금내역 양식'에 맞춰 올리거나 열 이름을 확인하세요.")

    rows, errors = [], []
    for k, row in enumerate(table[head_i + 1:], start=head_i + 2):
        get = lambda key: row[cols[key]] if key in cols and cols[key] < len(row) else None  # noqa: E731
        if all(c in (None, "") for c in row):
            continue
        try:
            amount = _to_int(get("amount"))
        except ValueError:
            errors.append({"row": k, "message": f"입금액을 읽을 수 없습니다: {get('amount')}"})
            continue
        if amount <= 0:
            continue      # 출금·합계 행 등
        try:
            d = _to_date(get("date"))
        except ValueError as e:
            errors.append({"row": k, "message": str(e)})
            continue
        name = str(get("name") or "").strip()
        if not name:
            errors.append({"row": k, "message": "입금자명이 비어 있습니다."})
            continue
        memo = str(get("memo") or "").strip() or None
        raw_dt = get("date")
        t = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", str(get("time") or "")) or \
            (re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", str(raw_dt)) if not isinstance(raw_dt, (date, datetime)) else None)
        tm = f"{int(t.group(1)):02d}:{t.group(2)}:{t.group(3) or '00'}" if t else (
            raw_dt.strftime("%H:%M:%S") if isinstance(raw_dt, datetime) and (raw_dt.hour or raw_dt.minute or raw_dt.second) else "")
        extra = "|".join(x for x in (tm, _norm(get("balance")), _norm(get("txid"))) if x)
        rows.append({"row": k, "deposit_date": d, "depositor": name[:100], "amount": amount, "bank_memo": memo and memo[:200],
                     "extra_key": extra})
    return rows, errors


def build_template() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "입금내역"
    ws.append(["입금일", "입금자명", "입금액", "적요"])
    for c in ws[1]:
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="DCE6F1")
    ws.append(["2026-11-30", "대구행복기숙사", 46729827, "4분기 원리금"])
    for col, w in zip("ABCD", (14, 24, 16, 30)):
        ws.column_dimensions[col].width = w
    g = wb.create_sheet("안내")
    for line in ("은행 거래내역 중 입금 건을 '입금내역' 시트에 붙여 넣으세요(예시 줄은 지우세요).",
                 "열 이름이 입금일(거래일)·입금자명(보낸분)·입금액(맡기신금액)이면 은행 파일을 그대로 올려도 읽습니다.",
                 "입금액이 0인 줄(출금)은 건너뜁니다. 같은 거래(입금일·입금자·금액·적요)를 다시 올리면 중복으로 건너뜁니다."):
        g.append([line])
    g.column_dimensions["A"].width = 110
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------- 대사 ----------
def _strip_name(s: str) -> str:
    s = re.sub(r"\(주\)|㈜|주식회사|\(유\)|유한회사|\s|[()]", "", s or "")
    return s.lower()


def _name_hit(depositor: str, words: list[str]) -> bool:
    d = _strip_name(depositor)
    for w in words:
        w = _strip_name(w)
        if len(w) >= 2 and (w in d or (len(d) >= 2 and d in w)):
            return True
    return False


def _open_notices(db: Session) -> list[GeneratedDocument]:
    """대사 대상 공문 — 삭제되지 않은 것 중 기숙사·상환일마다 가장 최근 발급본(재발급하면 이전 것은 대상에서 빠짐)."""
    docs = db.execute(select(GeneratedDocument).where(GeneratedDocument.is_deleted.is_(False))
                      .order_by(GeneratedDocument.created_at.desc())).scalars().all()
    seen, out = set(), []
    for d in docs:
        k = (d.dormitory_id, d.due_date)
        if k in seen:
            continue
        seen.add(k)
        out.append(d)
    return out


def _completed_notice_ids(db: Session, exclude: uuid.UUID | None = None) -> set:
    q = select(BankDeposit.notice_id).where(BankDeposit.status == DepositStatus.COMPLETED, BankDeposit.is_deleted.is_(False),
                                            BankDeposit.notice_id.is_not(None))
    if exclude:
        q = q.where(BankDeposit.id != exclude)
    return set(db.execute(q).scalars())


def _name_words(db: Session, doc: GeneratedDocument) -> list[str]:
    dorm = db.get(Dormitory, doc.dormitory_id)
    words = [dorm.dormitory_name, dorm.school_name] if dorm else []
    spc = db.get(SPC, dorm.spc_id) if dorm else None
    if spc:
        words.append(spc.spc_name)
    return [w for w in words if w]


def match_notice(db: Session, dep: BankDeposit) -> tuple[GeneratedDocument | None, list[str]]:
    """입금 → 공문. (공문, 대사 단계 사유) — 금액이 같은 공문을 찾고, 여러 건이면 입금자명으로 좁힌다."""
    done = _completed_notice_ids(db, exclude=dep.id)
    cands = [d for d in _open_notices(db) if d.id not in done and d.created_at.date() <= dep.deposit_date]
    same = [d for d in cands if d.generated_data["totals"]["total"] == dep.amount]
    if len(same) > 1:
        named = [d for d in same if _name_hit(dep.depositor, _name_words(db, d))]
        if len(named) == 1:
            return named[0], []
        return None, ["MULTI_MATCH"]
    if len(same) == 1:
        return same[0], []
    # 금액이 맞는 공문이 없으면 입금자명으로 후보를 하나 찾아 '금액 불일치'로 안내(분할 입금 등)
    named = [d for d in cands if _name_hit(dep.depositor, _name_words(db, d))]
    if named:
        named.sort(key=lambda d: abs((d.payment_deadline - dep.deposit_date).days))
        return named[0], ["AMOUNT_MISMATCH"]
    return None, ["NO_MATCH"]


def notice_checks(db: Session, dep: BankDeposit, doc: GeneratedDocument) -> list[str]:
    """공문이 정해진 입금의 확인사항(대기 사유)."""
    reasons = []
    total = doc.generated_data["totals"]["total"]
    if dep.amount != total:
        reasons.append("AMOUNT_MISMATCH")
    if total >= AUTO_APPROVE_LIMIT:
        reasons.append("OVER_LIMIT")
    if dep.deposit_date > doc.payment_deadline:
        reasons.append("LATE")
    if doc.id in _completed_notice_ids(db, exclude=dep.id):
        reasons.append("NOTICE_DONE")
    for src, line in zip(doc.generated_data["sources"], doc.generated_data["payments"]):
        loan_id = uuid.UUID(src["loan_id"])
        if db.execute(select(ActualPayment.id).where(ActualPayment.loan_id == loan_id, ActualPayment.due_date == doc.due_date,
                                                     ActualPayment.status == PaymentStatus.ACTIVE)).first():
            if "ALREADY_PAID" not in reasons:
                reasons.append("ALREADY_PAID")
        try:
            sched = scheduled_by_due_date(db, get_current_version(db, loan_id)).get(doc.due_date)
        except NoScheduleError:
            sched = None
        if sched is None or sched["principal"] != line["principal"] or sched["interest"] != line["interest"]:
            if "SCHEDULE_CHANGED" not in reasons:
                reasons.append("SCHEDULE_CHANGED")
    return reasons


def _create_payments(db: Session, dep: BankDeposit, doc: GeneratedDocument, user_id) -> list[str]:
    ids = []
    for src, line in zip(doc.generated_data["sources"], doc.generated_data["payments"]):
        if line["principal"] + line["interest"] <= 0:
            continue
        p = ActualPayment(loan_id=uuid.UUID(src["loan_id"]), due_date=doc.due_date, actual_paid_date=dep.deposit_date,
                          actual_principal=line["principal"], actual_interest=line["interest"],
                          remark=f"입금 확인({dep.depositor} {dep.deposit_date})", created_by=user_id)
        db.add(p)
        db.flush()
        ids.append(str(p.id))
    return ids


def _complete(db: Session, dep: BankDeposit, doc: GeneratedDocument, user_id, *, auto: bool, note: str | None) -> None:
    dep.payment_ids = _create_payments(db, dep, doc, user_id)
    dep.status = DepositStatus.COMPLETED
    dep.auto_approved = auto
    dep.manual_done = False
    dep.decided_by = user_id
    dep.decided_at = datetime.now(timezone.utc)
    dep.decision_note = note
    for src in doc.generated_data["sources"]:
        record_audit(db, table_name="bank_deposit", record_id=dep.id, loan_id=uuid.UUID(src["loan_id"]),
                     action="AUTO_APPR" if auto else "APPROVE", user_id=user_id, field="status",
                     before="PENDING", after="COMPLETED",
                     reason=f"{doc.title} · 입금 {dep.amount:,}원({dep.depositor}, {dep.deposit_date})" + (f" · {note}" if note else ""))


def evaluate(db: Session, dep: BankDeposit, user_id, *, allow_auto: bool = True) -> None:
    """공문 대사 → 자동 승인 또는 대기 사유 기록."""
    doc = db.get(GeneratedDocument, dep.notice_id) if dep.notice_id else None
    reasons: list[str] = []
    if doc is None or doc.is_deleted:
        doc, reasons = match_notice(db, dep)
    dep.notice_id = doc.id if doc else None
    if doc is not None:
        reasons = sorted(set(reasons) | set(notice_checks(db, dep, doc)), key=list(REASONS).index)
    dep.hold_reasons = reasons
    if doc is not None and not reasons and allow_auto:
        _complete(db, dep, doc, user_id, auto=True, note="자동 승인(2,000만 원 미만·금액 일치·납부기한 내)")


def _batch_start(db: Session) -> datetime:
    """이번 업로드가 시작된 시각 — 같은 업로드에서 방금 넣은 줄은 '이미 등록된 것'으로 세지 않는다."""
    if "deposit_batch_start" not in db.info:
        db.info["deposit_batch_start"] = datetime.now(timezone.utc)
    return db.info["deposit_batch_start"]


def add_deposits(db: Session, rows: list[dict], *, source: str, file_name: str | None, user_id) -> dict:
    """
    입금 등록. 매일 겹치는 기간을 올려도 같은 거래는 한 번만 들어간다.
    중복 판단 열쇠 = 입금일·입금자·금액·적요(+ 파일에 있으면 거래시각·거래후 잔액·거래번호).
    한 파일 안에 열쇠가 같은 줄이 여러 개면 '서로 다른 거래'로 보고(같은 날 같은 금액을 두 번 보낸 경우),
    이미 등록된 개수만큼만 건너뛰고 나머지는 등록한다.
    """
    today = date.today()
    db.info["deposit_batch_start"] = datetime.now(timezone.utc)
    added, dups, errors = [], 0, []
    seen_in_file: dict[str, int] = {}
    for r in rows:
        if r["deposit_date"] > today:
            errors.append({"row": r.get("row"), "message": f"미래 날짜의 입금입니다: {r['deposit_date']}"})
            continue
        key = f"{r['deposit_date']}|{_norm(r['depositor'])}|{r['amount']}|{_norm(r.get('bank_memo'))}"
        if r.get("extra_key"):
            key += f"|{r['extra_key']}"
        seen_in_file[key] = seen_in_file.get(key, 0) + 1
        already = db.execute(select(func.count()).select_from(BankDeposit).where(
            BankDeposit.dup_key == key, BankDeposit.is_deleted.is_(False), BankDeposit.created_at < _batch_start(db))).scalar()
        if seen_in_file[key] <= already:
            dups += 1
            continue
        dep = BankDeposit(deposit_date=r["deposit_date"], depositor=r["depositor"], amount=r["amount"], bank_memo=r.get("bank_memo"),
                          source=source, file_name=file_name, dup_key=key, status=DepositStatus.PENDING, created_by=user_id)
        db.add(dep)
        db.flush()
        evaluate(db, dep, user_id)
        db.flush()
        added.append(dep)
    db.info.pop("deposit_batch_start", None)
    db.commit()
    return {
        "added": len(added), "duplicates": dups, "errors": errors,
        "auto_completed": sum(1 for d in added if d.status == DepositStatus.COMPLETED),
        "pending": sum(1 for d in added if d.status == DepositStatus.PENDING),
    }


# ---------- 담당자 처리 ----------
def _get(db: Session, deposit_id: uuid.UUID) -> BankDeposit:
    dep = db.get(BankDeposit, deposit_id)
    if dep is None or dep.is_deleted:
        raise DepositError("입금내역을 찾을 수 없습니다.")
    return dep


def assign(db: Session, deposit_id: uuid.UUID, notice_id: uuid.UUID | None, user_id) -> BankDeposit:
    """대기 건의 공문을 지정(또는 해제)하고 확인사항을 다시 계산 — 지정만으로 완료되지는 않는다(승인 필요)."""
    dep = _get(db, deposit_id)
    if dep.status != DepositStatus.PENDING:
        raise DepositError("대기 상태에서만 공문을 바꿀 수 있습니다.")
    if notice_id is not None:
        doc = db.get(GeneratedDocument, notice_id)
        if doc is None or doc.is_deleted:
            raise DepositError("공문을 찾을 수 없습니다.")
        dep.notice_id = doc.id
        dep.hold_reasons = notice_checks(db, dep, doc)
    else:
        dep.notice_id = None
        dep.hold_reasons = ["NO_MATCH"]
    record_audit(db, table_name="bank_deposit", record_id=dep.id, loan_id=None, action="ASSIGN", user_id=user_id,
                 field="notice_id", after=str(notice_id) if notice_id else None)
    db.commit()
    db.refresh(dep)
    return dep


def approve(db: Session, deposit_id: uuid.UUID, user_id, note: str | None) -> BankDeposit:
    dep = _get(db, deposit_id)
    if dep.status != DepositStatus.PENDING:
        raise DepositError("대기 상태인 입금만 승인할 수 있습니다.")
    doc = db.get(GeneratedDocument, dep.notice_id) if dep.notice_id else None
    if doc is None or doc.is_deleted:
        raise DepositError("연결된 공문이 없습니다. 먼저 공문을 지정하세요(분할·합산 입금은 '수동 처리 완료').")
    reasons = notice_checks(db, dep, doc)
    blocking = [r for r in reasons if r in ("AMOUNT_MISMATCH", "NOTICE_DONE", "ALREADY_PAID", "SCHEDULE_CHANGED")]
    if blocking:
        raise DepositError("승인할 수 없습니다 — " + ", ".join(REASONS[r] for r in blocking)
                           + ". 금액·납부 기록을 확인해 '수동 처리 완료'로 처리하세요.")
    _complete(db, dep, doc, user_id, auto=False, note=note)
    db.commit()
    db.refresh(dep)
    return dep


def complete_manual(db: Session, deposit_id: uuid.UUID, user_id, reason: str) -> BankDeposit:
    """분할·합산·금액 불일치 등 — 납부 기록은 상환 실적 관리에서 직접 입력하고, 입금은 완료로 닫는다."""
    dep = _get(db, deposit_id)
    if dep.status != DepositStatus.PENDING:
        raise DepositError("대기 상태인 입금만 처리할 수 있습니다.")
    dep.status, dep.manual_done, dep.auto_approved = DepositStatus.COMPLETED, True, False
    dep.decided_by, dep.decided_at, dep.decision_note, dep.payment_ids = user_id, datetime.now(timezone.utc), reason, []
    record_audit(db, table_name="bank_deposit", record_id=dep.id, loan_id=None, action="MANUAL", user_id=user_id,
                 field="status", before="PENDING", after="COMPLETED", reason=reason)
    db.commit()
    db.refresh(dep)
    return dep


def exclude(db: Session, deposit_id: uuid.UUID, user_id, reason: str) -> BankDeposit:
    dep = _get(db, deposit_id)
    if dep.status != DepositStatus.PENDING:
        raise DepositError("대기 상태인 입금만 제외할 수 있습니다.")
    dep.status = DepositStatus.EXCLUDED
    dep.decided_by, dep.decided_at, dep.decision_note = user_id, datetime.now(timezone.utc), reason
    record_audit(db, table_name="bank_deposit", record_id=dep.id, loan_id=None, action="EXCLUDE", user_id=user_id,
                 field="status", before="PENDING", after="EXCLUDED", reason=reason)
    db.commit()
    db.refresh(dep)
    return dep


def revert(db: Session, deposit_id: uuid.UUID, user_id, reason: str) -> BankDeposit:
    """완료(자동 승인 포함)·제외 → 대기로 되돌림. 승인으로 만든 납부 기록은 취소(삭제하지 않음)."""
    dep = _get(db, deposit_id)
    if dep.status == DepositStatus.PENDING:
        raise DepositError("이미 대기 상태입니다.")
    now = datetime.now(timezone.utc)
    for pid in dep.payment_ids or []:
        p = db.get(ActualPayment, uuid.UUID(pid))
        if p is not None and p.status == PaymentStatus.ACTIVE:
            record_audit(db, table_name="actual_payment", record_id=p.id, loan_id=p.loan_id, action="CANCEL", user_id=user_id,
                         field="status", before="ACTIVE", after="CANCELLED", reason=f"입금 승인 취소: {reason}")
            p.status, p.cancel_reason, p.cancelled_at, p.cancelled_by = PaymentStatus.CANCELLED, f"입금 승인 취소: {reason}", now, user_id
    before = dep.status.value
    dep.status, dep.auto_approved, dep.manual_done, dep.payment_ids = DepositStatus.PENDING, False, False, []
    dep.decided_by, dep.decided_at, dep.decision_note = None, None, None
    record_audit(db, table_name="bank_deposit", record_id=dep.id, loan_id=None, action="REVERT", user_id=user_id,
                 field="status", before=before, after="PENDING", reason=reason)
    db.flush()
    evaluate(db, dep, user_id, allow_auto=False)    # 사유만 다시 계산 — 되돌린 건이 곧바로 자동 승인되지 않게
    db.commit()
    db.refresh(dep)
    return dep


def notice_options(db: Session, deposit_id: uuid.UUID) -> list[GeneratedDocument]:
    """공문 지정 후보 — 아직 다른 입금으로 완료되지 않은 최근 발급본(납부기한이 입금일에 가까운 순)."""
    dep = _get(db, deposit_id)
    done = _completed_notice_ids(db, exclude=dep.id)
    docs = [d for d in _open_notices(db) if d.id not in done]
    docs.sort(key=lambda d: (not _name_hit(dep.depositor, _name_words(db, d)), abs((d.payment_deadline - dep.deposit_date).days)))
    return docs[:30]
