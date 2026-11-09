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

가상계좌(CMS) 입금내역 반영(2026-11-09, 은행 샘플 확인)
- 은행 파일(.xls)의 가상계좌번호·고객KEY·입금인명(기재내역)·고객명으로 '어느 기숙사의 입금인지' 먼저 알아본다.
  기숙사명이 은행 CMS와 시스템이 달라도 가상계좌·고객KEY로 맞춘다(기숙사별 '입금 식별정보').
- 기숙사마다 납입 방식이 다르다: ① 원리금 총액 한 번에 ② 원금·이자를 나눠 입금.
  ②는 같은 기숙사의 대기 입금 여러 건 합계가 공문 합계와 1원까지 같으면 '분할 입금 묶음'으로 한 공문에 연결하고,
  묶음 전체를 한 건처럼 자동 승인 기준(2천만 원 미만·기한 내)에 따라 처리한다. 한쪽만 먼저 들어오면 '나머지 입금 대기'.
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
from app.core.crypto import decrypt_str
from app.models.payment import ActualPayment, BankDeposit, DepositStatus, PaymentStatus

AUTO_APPROVE_LIMIT = 20_000_000   # 공문 합계가 이 금액 '미만'이면 자동 승인 대상

SPLIT_MIN_REST = 10_000   # 공문 합계보다 1만 원 이상 적게 들어왔을 때만 분할 입금(나머지 대기)으로 본다
REASONS = {
    "OVER_LIMIT": "2,000만 원 이상 — 담당자 승인",
    "LATE": "납부기한 경과",
    "AMOUNT_MISMATCH": "공문 금액과 다름",
    "NO_MATCH": "대상 공문 없음(분할·합산 입금 등)",
    "MULTI_MATCH": "같은 금액 공문 여러 건",
    "ALREADY_PAID": "이미 납부 기록 있음",
    "SCHEDULE_CHANGED": "공문 발급 후 원리금 테이블 변경",
    "NOTICE_DONE": "다른 입금으로 이미 완료된 공문",
    "SPLIT_WAIT": "분할 입금 — 나머지 입금 기다리는 중",
}


class DepositError(Exception):
    pass


# ---------- 입금내역 읽기 ----------
HEADS = {
    "date": ("입금일", "거래일", "거래일자", "거래일시", "입금일자", "일자"),
    "name": ("입금자명", "입금자", "보낸분", "의뢰인", "의뢰인명", "송금인", "기재내역(입금인명)", "입금인명", "기재내역"),
    "amount": ("입금액", "입금금액", "맡기신금액", "입금(원)", "입금", "수납액"),
    "memo": ("적요", "메모", "비고", "내용", "거래내용"),
    # 있으면 중복 판단에 함께 쓴다 — 은행 거래내역의 거래시각·거래후 잔액은 거래마다 달라 같은 거래를 정확히 가려낸다
    "time": ("거래시각", "거래시간", "시각", "시간"),
    "balance": ("잔액", "거래후잔액", "거래후 잔액"),
    "txid": ("거래번호", "거래고유번호", "거래일련번호"),
    # 가상계좌(CMS) 은행 파일
    "account": ("가상계좌번호(상대계좌)", "가상계좌번호", "가상계좌", "상대계좌"),
    "ckey": ("고객KEY", "고객키", "고객번호"),
    "cname": ("고객명",),
    "kind": ("수납구분",),
    "cancel": ("취소원거래번호",),
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
    elif file_name.lower().endswith(".xls") or content[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        try:
            import xlrd   # 은행이 내려주는 예전 엑셀(.xls)
        except ImportError:
            raise DepositError("이 서버에는 .xls 읽기 도구(xlrd)가 없습니다. 업데이트 안내의 'pip install' 단계를 실행하거나, 엑셀에서 .xlsx로 다시 저장해 올리세요.")
        try:
            sh = xlrd.open_workbook(file_contents=content).sheet_by_index(0)
        except Exception:
            raise DepositError("엑셀(.xls) 파일을 읽을 수 없습니다. 엑셀에서 열어 .xlsx로 다시 저장해 올리세요.")
        table = [[sh.cell_value(r, c) for c in range(sh.ncols)] for r in range(sh.nrows)]
    else:
        try:
            wb = load_workbook(io.BytesIO(content), data_only=True, read_only=True)
        except Exception:
            raise DepositError("엑셀(.xlsx·.xls) 또는 CSV 파일만 올릴 수 있습니다.")
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
        if str(get("kind") or "").strip() not in ("", "입금"):
            continue      # 수납구분이 입금이 아닌 줄
        if str(get("cancel") or "").strip():
            errors.append({"row": k, "message": f"취소 거래입니다(원거래번호 {get('cancel')}) — 원거래 입금이 등록돼 있으면 '제외'로 처리하세요."})
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
        acct = str(get("account") or "").strip() or None
        ckey = str(get("ckey") or "").strip()
        ckey = (ckey[:-2] if ckey.endswith(".0") else ckey) or None      # 엑셀 숫자 '68.0' → '68'
        rows.append({"row": k, "deposit_date": d, "depositor": name[:100], "amount": amount, "bank_memo": memo and memo[:200],
                     "extra_key": extra, "deposit_time": tm or None, "virtual_account": acct, "customer_key": ckey,
                     "customer_name": (str(get("cname") or "").strip() or None)})
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
    s = re.sub(r"-\d+$", "", s)            # CMS 입금인명 끝의 '-081' 같은 고객번호
    return s.lower()


def _name_hit(depositor: str, words: list[str]) -> bool:
    d = _strip_name(depositor)
    for w in words:
        w = _strip_name(w)
        if len(w) >= 2 and len(d) >= 2 and (w in d or d in w):
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


def _completed_notice_ids(db: Session, exclude: set | None = None) -> set:
    q = select(BankDeposit.notice_id).where(BankDeposit.status == DepositStatus.COMPLETED, BankDeposit.is_deleted.is_(False),
                                            BankDeposit.notice_id.is_not(None))
    if exclude:
        q = q.where(BankDeposit.id.not_in(exclude))
    return set(db.execute(q).scalars())


def _dorm_words(db: Session, dorm: Dormitory | None) -> list[str]:
    if dorm is None:
        return []
    words = [dorm.dormitory_name, dorm.school_name, *(dorm.bank_payer_names or [])]
    spc = db.get(SPC, dorm.spc_id) if dorm.spc_id else None
    if spc:
        words.append(spc.spc_name)
    return [w for w in words if w]


def _name_words(db: Session, doc: GeneratedDocument) -> list[str]:
    return _dorm_words(db, db.get(Dormitory, doc.dormitory_id))


def _acct(v: str | None) -> str:
    return re.sub(r"\D", "", v or "")


def identify_dormitory(db: Session, dep: BankDeposit) -> Dormitory | None:
    """
    입금 → 기숙사. 가상계좌번호 → 고객KEY → 등록한 입금인명(별칭) → 기숙사·학교·SPC 이름 순으로 찾고, 한 곳으로 정해질 때만 돌려준다.
    """
    dorms = db.execute(select(Dormitory).where(Dormitory.is_deleted.is_(False))).scalars().all()
    va = _acct(decrypt_str(dep.virtual_account))
    if va:
        hit = [d for d in dorms if d.bank_virtual_account and _acct(decrypt_str(d.bank_virtual_account)) == va]
        if len(hit) == 1:
            return hit[0]
    if dep.customer_key:
        hit = [d for d in dorms if d.bank_customer_key and d.bank_customer_key.strip() == dep.customer_key.strip()]
        if len(hit) == 1:
            return hit[0]
    names = [n for n in (dep.depositor, dep.customer_name) if n]
    # 등록한 입금인명은 '정확히 같음'으로만 본다(끝의 -081 같은 번호·띄어쓰기는 무시)
    exact = [d for d in dorms if any(_strip_name(a) and _strip_name(a) in (_strip_name(n) for n in names) for a in (d.bank_payer_names or []))]
    if len(exact) == 1:
        return exact[0]
    fuzzy = [d for d in dorms if any(_name_hit(n, [w for w in _dorm_words(db, d) if w not in (d.bank_payer_names or [])]) for n in names)]
    return fuzzy[0] if len(fuzzy) == 1 else None


def _members(db: Session, dep: BankDeposit) -> list[BankDeposit]:
    """분할 입금 묶음이면 묶음 전체, 아니면 자기 자신."""
    if not dep.group_id:
        return [dep]
    return list(db.execute(select(BankDeposit).where(BankDeposit.group_id == dep.group_id, BankDeposit.is_deleted.is_(False))
                           .order_by(BankDeposit.deposit_date, BankDeposit.deposit_time, BankDeposit.created_at)).scalars())


def _find_group(db: Session, dep: BankDeposit, cands: list[GeneratedDocument]):
    """같은 기숙사의 대기 입금(묶이지 않은 것) 중 이 입금을 포함해 합계가 공문 합계와 같은 조합(2~4건)."""
    from itertools import combinations
    if not dep.dormitory_id:
        return None, None
    pool = db.execute(select(BankDeposit).where(
        BankDeposit.dormitory_id == dep.dormitory_id, BankDeposit.status == DepositStatus.PENDING,
        BankDeposit.group_id.is_(None), BankDeposit.is_deleted.is_(False), BankDeposit.id != dep.id)
        .order_by(BankDeposit.deposit_date.desc())).scalars().all()[:8]
    for doc in sorted(cands, key=lambda d: abs((d.payment_deadline - dep.deposit_date).days)):
        total = doc.generated_data["totals"]["total"]
        others = [o for o in pool if o.deposit_date >= doc.created_at.date()]
        for n in range(1, min(3, len(others)) + 1):
            for combo in combinations(others, n):
                if dep.amount + sum(o.amount for o in combo) == total:
                    return doc, [dep, *combo]
    return None, None


def match_notice(db: Session, dep: BankDeposit) -> tuple[GeneratedDocument | None, list[str], list[BankDeposit] | None]:
    """입금 → (공문, 대사 단계 사유, 분할 묶음). 기숙사를 알면 그 기숙사 공문만 본다."""
    done = _completed_notice_ids(db, exclude={dep.id})
    cands = [d for d in _open_notices(db) if d.id not in done and d.created_at.date() <= dep.deposit_date]
    if dep.dormitory_id:
        cands = [d for d in cands if d.dormitory_id == dep.dormitory_id]
    same = [d for d in cands if d.generated_data["totals"]["total"] == dep.amount]
    if len(same) > 1:
        named = [d for d in same if _name_hit(dep.depositor, _name_words(db, d))]
        if len(named) == 1:
            return named[0], [], None
        return None, ["MULTI_MATCH"], None
    if len(same) == 1:
        return same[0], [], None
    # ② 원금·이자 나눠 입금 — 같은 기숙사 대기 입금과 합쳐 공문 합계가 되면 묶음
    doc, group = _find_group(db, dep, cands)
    if doc is not None:
        return doc, [], group
    if dep.dormitory_id:
        near = sorted(cands, key=lambda d: abs((d.payment_deadline - dep.deposit_date).days))
        for d in near:      # 원금만·이자만 먼저 들어온 경우
            t = d.generated_data["totals"]
            # 몇 원~몇천 원 모자란 건 계산 착오로 보고 '공문 금액과 다름'으로(나머지를 기다리지 않음)
            if dep.amount in (t["principal"], t["interest"]) or dep.amount <= t["total"] - SPLIT_MIN_REST:
                return d, ["SPLIT_WAIT"], None
        if near:
            return near[0], ["AMOUNT_MISMATCH"], None
        return None, ["NO_MATCH"], None
    named = [d for d in cands if _name_hit(dep.depositor, _name_words(db, d))]
    if named:
        named.sort(key=lambda d: abs((d.payment_deadline - dep.deposit_date).days))
        return named[0], ["AMOUNT_MISMATCH"], None
    return None, ["NO_MATCH"], None


def notice_checks(db: Session, deps: list[BankDeposit], doc: GeneratedDocument) -> list[str]:
    """공문이 정해진 입금(또는 분할 묶음)의 확인사항(대기 사유) — 금액은 합계, 날짜는 마지막 입금일로 본다."""
    reasons = []
    total = doc.generated_data["totals"]["total"]
    amount = sum(d.amount for d in deps)
    last = max(d.deposit_date for d in deps)
    if amount != total:
        reasons.append("AMOUNT_MISMATCH")
    if total >= AUTO_APPROVE_LIMIT:
        reasons.append("OVER_LIMIT")
    if last > doc.payment_deadline:
        reasons.append("LATE")
    if doc.id in _completed_notice_ids(db, exclude={d.id for d in deps}):
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


def _create_payments(db: Session, deps: list[BankDeposit], doc: GeneratedDocument, user_id) -> list[str]:
    ids = []
    last = max(deps, key=lambda d: (d.deposit_date, d.deposit_time or ""))
    who = ", ".join(sorted({d.depositor for d in deps}))
    for src, line in zip(doc.generated_data["sources"], doc.generated_data["payments"]):
        if line["principal"] + line["interest"] <= 0:
            continue
        p = ActualPayment(loan_id=uuid.UUID(src["loan_id"]), due_date=doc.due_date, actual_paid_date=last.deposit_date,
                          actual_principal=line["principal"], actual_interest=line["interest"],
                          remark=f"입금 확인({who} {last.deposit_date}{f' · 분할 {len(deps)}건' if len(deps) > 1 else ''})",
                          created_by=user_id)
        db.add(p)
        db.flush()
        ids.append(str(p.id))
    return ids


def _mark_received_by_deposit(doc: GeneratedDocument | None) -> None:
    """입금이 확인되면 공문을 받은 것으로 본다 — 미수신이면 '입금 확인으로 수신'(received_via=DEPOSIT)."""
    if doc is not None and doc.received_at is None:
        doc.received_at = datetime.now(timezone.utc)
        doc.received_via = "DEPOSIT"
        doc.received_by = None


def _unmark_received_by_deposit(db: Session, doc: GeneratedDocument | None, dep_ids: set) -> None:
    """되돌리기: 입금으로 수신 처리했던 공문은 다른 완료 입금이 없으면 다시 미수신으로."""
    if doc is None or doc.received_via != "DEPOSIT":
        return
    other = db.execute(select(BankDeposit.id).where(BankDeposit.notice_id == doc.id, BankDeposit.id.not_in(dep_ids),
                                                    BankDeposit.status == DepositStatus.COMPLETED,
                                                    BankDeposit.is_deleted.is_(False))).first()
    if not other:
        doc.received_at = None
        doc.received_via = None


def _complete(db: Session, deps: list[BankDeposit], doc: GeneratedDocument, user_id, *, auto: bool, note: str | None) -> None:
    _mark_received_by_deposit(doc)
    ids = _create_payments(db, deps, doc, user_id)
    now = datetime.now(timezone.utc)
    for i, dep in enumerate(deps):
        dep.payment_ids = ids if i == 0 else []      # 납부 기록은 묶음의 첫 입금에만 연결(되돌리기 때 한 번만 취소)
        dep.status, dep.auto_approved, dep.manual_done = DepositStatus.COMPLETED, auto, False
        dep.decided_by, dep.decided_at, dep.decision_note = user_id, now, note
        dep.notice_id = doc.id
    amount = sum(d.amount for d in deps)
    detail = " + ".join(f"{d.amount:,}" for d in deps) if len(deps) > 1 else f"{amount:,}"
    for src in doc.generated_data["sources"]:
        record_audit(db, table_name="bank_deposit", record_id=deps[0].id, loan_id=uuid.UUID(src["loan_id"]),
                     action="AUTO_APPR" if auto else "APPROVE", user_id=user_id, field="status",
                     before="PENDING", after="COMPLETED",
                     reason=f"{doc.title} · 입금 {detail}원({deps[0].depositor}, {max(d.deposit_date for d in deps)})"
                            + (f" · {note}" if note else ""))


def _learn_ident(db: Session, deps: list[BankDeposit], doc: GeneratedDocument) -> None:
    """담당자가 공문을 지정·승인하면 그 입금의 가상계좌·고객KEY·입금인명을 기숙사 입금 식별정보에 기억(비어 있을 때만)."""
    from app.core.crypto import encrypt_str
    dorm = db.get(Dormitory, doc.dormitory_id)
    if dorm is None:
        return
    others = db.execute(select(Dormitory).where(Dormitory.is_deleted.is_(False), Dormitory.id != dorm.id)).scalars().all()
    for d in deps:
        va = _acct(decrypt_str(d.virtual_account))
        # 다른 기숙사에 이미 등록된 번호는 기억하지 않는다(어느 기숙사인지 헷갈리지 않게)
        if va and not dorm.bank_virtual_account and not any(o.bank_virtual_account and _acct(decrypt_str(o.bank_virtual_account)) == va for o in others):
            dorm.bank_virtual_account = encrypt_str(decrypt_str(d.virtual_account))
        if d.customer_key and not dorm.bank_customer_key and not any(o.bank_customer_key == d.customer_key for o in others):
            dorm.bank_customer_key = d.customer_key
        names = list(dorm.bank_payer_names or [])
        for n in (d.depositor, d.customer_name):
            if n and n not in names and len(names) < 10:
                names.append(n)
        dorm.bank_payer_names = names
        d.dormitory_id = dorm.id


def evaluate(db: Session, dep: BankDeposit, user_id, *, allow_auto: bool = True) -> None:
    """기숙사 알아보기 → 공문 대사(한 건 또는 분할 묶음) → 자동 승인 또는 대기 사유 기록."""
    if dep.dormitory_id is None:
        dorm = identify_dormitory(db, dep)
        dep.dormitory_id = dorm.id if dorm else None
    doc = db.get(GeneratedDocument, dep.notice_id) if dep.notice_id else None
    reasons: list[str] = []
    group = None
    if doc is None or doc.is_deleted:
        doc, reasons, group = match_notice(db, dep)
    members = group or _members(db, dep)
    if group:
        gid = uuid.uuid4()
        for m in group:
            m.group_id = gid
    for m in members:
        m.notice_id = doc.id if doc else None
    if doc is not None and "SPLIT_WAIT" not in reasons:
        reasons = sorted(set(reasons) | set(notice_checks(db, members, doc)), key=list(REASONS).index)
    for m in members:
        m.hold_reasons = reasons
    if doc is not None and not reasons and allow_auto:
        _complete(db, members, doc, user_id, auto=True,
                  note="자동 승인(2,000만 원 미만·금액 일치·납부기한 내" + (f"·분할 입금 {len(members)}건 합계" if len(members) > 1 else "") + ")")


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
    from app.core.crypto import encrypt_str
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
        dep = BankDeposit(deposit_date=r["deposit_date"], deposit_time=r.get("deposit_time"), depositor=r["depositor"],
                          amount=r["amount"], bank_memo=r.get("bank_memo"), source=source, file_name=file_name, dup_key=key,
                          virtual_account=encrypt_str(r["virtual_account"]) if r.get("virtual_account") else None,
                          customer_key=r.get("customer_key"), customer_name=r.get("customer_name"),
                          status=DepositStatus.PENDING, created_by=user_id)
        db.add(dep)
        db.flush()
        added.append(dep)
    # 입금일 순으로 대사 — 분할 입금은 뒤에 들어온 건이 앞의 대기 건과 묶인다
    for dep in sorted(added, key=lambda d: (d.deposit_date, d.deposit_time or "")):
        if dep.status == DepositStatus.PENDING and dep.group_id is None:
            evaluate(db, dep, user_id)
            db.flush()
    db.info.pop("deposit_batch_start", None)
    db.commit()
    return {
        "added": len(added), "duplicates": dups, "errors": errors,
        "auto_completed": sum(1 for d in added if d.status == DepositStatus.COMPLETED),
        "pending": sum(1 for d in added if d.status == DepositStatus.PENDING),
        "grouped": sum(1 for d in added if d.group_id),
    }


# ---------- 담당자 처리 ----------
def _get(db: Session, deposit_id: uuid.UUID) -> BankDeposit:
    dep = db.get(BankDeposit, deposit_id)
    if dep is None or dep.is_deleted:
        raise DepositError("입금내역을 찾을 수 없습니다.")
    return dep


def _ungroup(db: Session, dep: BankDeposit) -> None:
    """분할 묶음 풀기 — 나머지 입금은 다시 대사 대기로."""
    if not dep.group_id:
        return
    for m in _members(db, dep):
        m.group_id = None
        if m.id != dep.id and m.status == DepositStatus.PENDING:
            m.notice_id, m.hold_reasons = None, ["NO_MATCH"] if not m.dormitory_id else ["SPLIT_WAIT"]


def assign(db: Session, deposit_id: uuid.UUID, notice_id: uuid.UUID | None, user_id) -> BankDeposit:
    """대기 건의 공문을 지정(또는 해제)하고 확인사항을 다시 계산 — 지정만으로 완료되지는 않는다(승인 필요).
    같은 기숙사의 대기 입금과 합쳐 공문 합계가 되면 분할 묶음으로 잇는다."""
    dep = _get(db, deposit_id)
    if dep.status != DepositStatus.PENDING:
        raise DepositError("대기 상태에서만 공문을 바꿀 수 있습니다.")
    _ungroup(db, dep)
    if notice_id is not None:
        doc = db.get(GeneratedDocument, notice_id)
        if doc is None or doc.is_deleted:
            raise DepositError("공문을 찾을 수 없습니다.")
        _learn_ident(db, [dep], doc)
        db.flush()
        members = [dep]
        if dep.amount != doc.generated_data["totals"]["total"]:
            _, group = _find_group(db, dep, [doc])
            if group:
                gid = uuid.uuid4()
                for m in group:
                    m.group_id = gid
                members = group
        for m in members:
            m.notice_id = doc.id
            m.hold_reasons = notice_checks(db, members, doc)
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
    members = _members(db, dep)
    reasons = notice_checks(db, members, doc)
    blocking = [r for r in reasons if r in ("AMOUNT_MISMATCH", "NOTICE_DONE", "ALREADY_PAID", "SCHEDULE_CHANGED")]
    if blocking:
        raise DepositError("승인할 수 없습니다 — " + ", ".join(REASONS[r] for r in blocking)
                           + ". 금액·납부 기록을 확인해 '수동 처리 완료'로 처리하세요.")
    _learn_ident(db, members, doc)
    _complete(db, members, doc, user_id, auto=False, note=note)
    db.commit()
    db.refresh(dep)
    return dep


def complete_manual(db: Session, deposit_id: uuid.UUID, user_id, reason: str) -> BankDeposit:
    """분할·합산·금액 불일치 등 — 납부 기록은 상환 실적 관리에서 직접 입력하고, 입금은 완료로 닫는다(묶음이면 묶음 전체)."""
    dep = _get(db, deposit_id)
    if dep.status != DepositStatus.PENDING:
        raise DepositError("대기 상태인 입금만 처리할 수 있습니다.")
    _mark_received_by_deposit(db.get(GeneratedDocument, dep.notice_id) if dep.notice_id else None)
    now = datetime.now(timezone.utc)
    for m in _members(db, dep):
        m.status, m.manual_done, m.auto_approved = DepositStatus.COMPLETED, True, False
        m.decided_by, m.decided_at, m.decision_note, m.payment_ids = user_id, now, reason, []
    record_audit(db, table_name="bank_deposit", record_id=dep.id, loan_id=None, action="MANUAL", user_id=user_id,
                 field="status", before="PENDING", after="COMPLETED", reason=reason)
    db.commit()
    db.refresh(dep)
    return dep


def exclude(db: Session, deposit_id: uuid.UUID, user_id, reason: str) -> BankDeposit:
    """제외 — 분할 묶음이면 이 입금만 묶음에서 빼고 제외한다."""
    dep = _get(db, deposit_id)
    if dep.status != DepositStatus.PENDING:
        raise DepositError("대기 상태인 입금만 제외할 수 있습니다.")
    _ungroup(db, dep)
    dep.status = DepositStatus.EXCLUDED
    dep.decided_by, dep.decided_at, dep.decision_note = user_id, datetime.now(timezone.utc), reason
    record_audit(db, table_name="bank_deposit", record_id=dep.id, loan_id=None, action="EXCLUDE", user_id=user_id,
                 field="status", before="PENDING", after="EXCLUDED", reason=reason)
    db.commit()
    db.refresh(dep)
    return dep


def revert(db: Session, deposit_id: uuid.UUID, user_id, reason: str) -> BankDeposit:
    """완료(자동 승인 포함)·제외 → 대기로 되돌림(묶음이면 묶음 전체). 승인으로 만든 납부 기록은 취소(삭제하지 않음)."""
    dep = _get(db, deposit_id)
    if dep.status == DepositStatus.PENDING:
        raise DepositError("이미 대기 상태입니다.")
    now = datetime.now(timezone.utc)
    members = [m for m in _members(db, dep) if m.status != DepositStatus.PENDING]
    before = dep.status.value
    if dep.status == DepositStatus.COMPLETED and dep.notice_id:
        _unmark_received_by_deposit(db, db.get(GeneratedDocument, dep.notice_id), {m.id for m in members})
    for m in members:
        for pid in m.payment_ids or []:
            p = db.get(ActualPayment, uuid.UUID(pid))
            if p is not None and p.status == PaymentStatus.ACTIVE:
                record_audit(db, table_name="actual_payment", record_id=p.id, loan_id=p.loan_id, action="CANCEL", user_id=user_id,
                             field="status", before="ACTIVE", after="CANCELLED", reason=f"입금 승인 취소: {reason}")
                p.status, p.cancel_reason, p.cancelled_at, p.cancelled_by = PaymentStatus.CANCELLED, f"입금 승인 취소: {reason}", now, user_id
        m.status, m.auto_approved, m.manual_done, m.payment_ids = DepositStatus.PENDING, False, False, []
        m.decided_by, m.decided_at, m.decision_note = None, None, None
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
    done = _completed_notice_ids(db, exclude={m.id for m in _members(db, dep)})
    docs = [d for d in _open_notices(db) if d.id not in done]
    docs.sort(key=lambda d: (d.dormitory_id != dep.dormitory_id, not _name_hit(dep.depositor, _name_words(db, d)),
                             abs((d.payment_deadline - dep.deposit_date).days)))
    return docs[:30]


# ---------- 기숙사 입금 식별정보 ----------
def mask_account(v: str | None) -> str | None:
    """가상계좌 표시용 — 끝 4자리만 보이게."""
    raw = decrypt_str(v) if v else None
    if not raw:
        return None
    digits = _acct(raw)
    return ("*" * max(len(digits) - 4, 0)) + digits[-4:] if digits else raw
