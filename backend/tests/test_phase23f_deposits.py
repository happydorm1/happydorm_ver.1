"""
Phase 23: 입금 확인·승인 — 입금내역(업로드·직접입력) ↔ 안내공문 대사, 2,000만 원 미만·1원 일치·기한 내 자동 승인,
그 밖에는 대기 → 재단 자금담당자 승인. 완료 시 납부 기록 생성, 승인 취소 시 납부 기록 취소. SPC 담당자는 처리 불가.
"""
import io
import random
import uuid
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta
from openpyxl import load_workbook

from _helpers import admin_headers, client, make_loan, make_org, spc_manager_headers

ACCOUNT = {"bank_account": "가나은행 123-456-789012", "account_holder": "재단"}
TODAY = date.today()


def _eom(d: date) -> date:
    return (d.replace(day=1) + relativedelta(months=1)) - timedelta(days=1)


def _setup(h, amount):
    """오늘 이후 첫 상환일(다음 달 말)이 있는 차입금 + 그 상환일 공문 발급."""
    client.post("/api/v1/document-templates", json={"template_name": f"t-{uuid.uuid4().hex[:6]}", "config": ACCOUNT}, headers=h)
    spc, dorm = make_org(h)
    first = _eom(TODAY + relativedelta(months=1))
    start = (first - relativedelta(months=3)).replace(day=1)
    loan_id, dorm, _ = make_loan(h, dorm=dorm, start=start.isoformat(), first=first.isoformat(), months=60, amount=amount)
    r = client.post(f"/api/v1/dormitories/{dorm}/notices", json={"due_date": first.isoformat()}, headers=h)
    assert r.status_code == 201, r.text
    n = r.json()
    dname = next(d for d in client.get("/api/v1/dormitories", headers=h).json() if d["id"] == dorm)["dormitory_name"]
    return loan_id, dorm, spc, n, dname


def _dep(h, **body):
    r = client.post("/api/v1/deposits", json={"deposit_date": TODAY.isoformat(), **body}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _find(h, depositor):
    items = client.get("/api/v1/deposits", headers=h).json()["items"]
    return next(x for x in items if x["depositor"] == depositor)


def _paid(h, loan_id, due):
    rows = client.get(f"/api/v1/loans/{loan_id}/payments/summary", headers=h).json()["rows"]
    return next(r for r in rows if r["due_date"] == due)


def test_auto_approve_under_limit_exact_amount():
    h = admin_headers()
    amount = 100_000_000 + random.randint(1, 9_999) * 1_000
    loan_id, dorm, _, n, dname = _setup(h, amount)
    total = n["generated_data"]["totals"]["total"]
    assert total < 20_000_000
    res = _dep(h, depositor=f"{dname}(주)", amount=total)
    assert res["auto_completed"] == 1 and res["pending"] == 0
    d = _find(h, f"{dname}(주)")
    assert d["status"] == "COMPLETED" and d["auto_approved"] and d["notice"]["id"] == n["id"] and d["payment_count"] == 1
    row = _paid(h, loan_id, n["due_date"])
    assert row["paid_total"] == total and row["status"] == "PAID"

    # 같은 거래 다시 등록 → 중복
    assert client.post("/api/v1/deposits", json={"deposit_date": TODAY.isoformat(), "depositor": f"{dname}(주)", "amount": total},
                       headers=h).status_code == 409
    # 승인 취소 → 대기 + 납부 기록 취소(자동 재승인 안 함)
    r = client.post(f"/api/v1/deposits/{d['id']}/revert", json={"reason": "테스트 취소"}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "PENDING"
    assert _paid(h, loan_id, n["due_date"])["paid_total"] == 0


def test_one_won_difference_and_over_limit_go_pending_then_approve():
    h = admin_headers()
    # 1원 차이 → 대기(금액 불일치, 입금자명으로 공문 추천) → 승인 불가 → 수동 처리 완료
    loan_id, _, _, n, dname = _setup(h, 100_000_000 + random.randint(1, 9_999) * 1_000)
    total = n["generated_data"]["totals"]["total"]
    _dep(h, depositor=dname, amount=total - 1)
    d = _find(h, dname)
    assert d["status"] == "PENDING" and d["notice"]["id"] == n["id"]
    assert [x["code"] for x in d["hold_reasons"]] == ["AMOUNT_MISMATCH"]
    r = client.post(f"/api/v1/deposits/{d['id']}/approve", json={}, headers=h)
    assert r.status_code == 422 and "공문 금액과 다름" in r.text
    r = client.post(f"/api/v1/deposits/{d['id']}/manual-complete", json={"reason": "1원 부족 — 상환 실적 관리에서 직접 입력"}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "COMPLETED" and r.json()["manual_done"] is True
    assert _paid(h, loan_id, n["due_date"])["paid_total"] == 0          # 수동 처리는 납부 기록을 만들지 않음

    # 2,000만 원 이상 → 대기(담당자 승인) → 자금담당자 승인 → 완료·납부 기록
    loan2, _, spc2, n2, dname2 = _setup(h, 1_000_000_000 + random.randint(1, 9_999) * 1_000)
    total2 = n2["generated_data"]["totals"]["total"]
    assert total2 >= 20_000_000
    _dep(h, depositor=f"{dname2} 상환", amount=total2)
    d2 = _find(h, f"{dname2} 상환")
    assert d2["status"] == "PENDING" and [x["code"] for x in d2["hold_reasons"]] == ["OVER_LIMIT"]
    # SPC 담당자는 처리·조회 불가
    spc_h = spc_manager_headers(h, spc2)
    assert client.post(f"/api/v1/deposits/{d2['id']}/approve", json={}, headers=spc_h).status_code == 403
    assert client.get("/api/v1/deposits", headers=spc_h).status_code == 403
    r = client.post(f"/api/v1/deposits/{d2['id']}/approve", json={"note": "통장 확인"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "COMPLETED" and not r.json()["auto_approved"] and r.json()["decision_note"] == "통장 확인"
    assert _paid(h, loan2, n2["due_date"])["status"] == "PAID"


def test_upload_file_no_match_assign_and_exclude():
    h = admin_headers()
    loan_id, _, _, n, dname = _setup(h, 100_000_000 + random.randint(1, 9_999) * 1_000)
    total = n["generated_data"]["totals"]["total"]
    tpl = client.get("/api/v1/deposits/template", headers=h)
    assert tpl.status_code == 200
    wb = load_workbook(io.BytesIO(tpl.content))
    ws = wb["입금내역"]
    ws.delete_rows(2)
    who = f"모르는입금-{uuid.uuid4().hex[:4]}"
    ws.append([TODAY.isoformat(), who, 12_345, "기타"])                 # 대상 없음
    ws.append([TODAY.isoformat(), f"{who}-2", "1,234원", None])          # 대상 없음 → 제외할 것
    ws.append([TODAY.isoformat(), "출금", 0, None])                       # 출금 → 건너뜀
    ws.append(["2099-01-01", "미래", 1000, None])                          # 미래 → 오류
    buf = io.BytesIO()
    wb.save(buf)
    r = client.post("/api/v1/deposits/upload", files={"file": ("bank.xlsx", buf.getvalue())}, headers=h)
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["added"] == 2 and res["pending"] == 2 and len(res["errors"]) == 1
    d = _find(h, who)
    assert d["notice"] is None and [x["code"] for x in d["hold_reasons"]] == ["NO_MATCH"] and d["file_name"] == "bank.xlsx"
    # 공문 지정 → 확인사항 재계산(금액 다름) — 승인 불가
    opts = client.get(f"/api/v1/deposits/{d['id']}/notice-options", headers=h).json()
    assert any(o["id"] == n["id"] for o in opts)
    a = client.post(f"/api/v1/deposits/{d['id']}/assign", json={"notice_id": n["id"]}, headers=h).json()
    assert a["notice"]["id"] == n["id"] and [x["code"] for x in a["hold_reasons"]] == ["AMOUNT_MISMATCH"]
    # 제외(사유 필수)
    d2 = _find(h, f"{who}-2")
    assert d2["amount"] == 1_234
    assert client.post(f"/api/v1/deposits/{d2['id']}/exclude", json={"reason": ""}, headers=h).status_code == 422
    e = client.post(f"/api/v1/deposits/{d2['id']}/exclude", json={"reason": "임대료 입금"}, headers=h).json()
    assert e["status"] == "EXCLUDED"
    # 정상 금액 입금이 따로 오면 그 공문은 자동 승인되고, 앞의 대기 건에는 '이미 완료된 공문'이 표시
    _dep(h, depositor=dname, amount=total)
    assert _find(h, dname)["status"] == "COMPLETED"
    a2 = client.post(f"/api/v1/deposits/{d['id']}/assign", json={"notice_id": n["id"]}, headers=h).json()
    assert "NOTICE_DONE" in [x["code"] for x in a2["hold_reasons"]]


def _upload(h, rows, head=("입금일", "입금자명", "입금액", "적요")):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(list(head))
    for r in rows:
        ws.append(list(r))
    buf = io.BytesIO()
    wb.save(buf)
    r = client.post("/api/v1/deposits/upload", files={"file": ("daily.xlsx", buf.getvalue())}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_daily_overlapping_uploads_skip_only_already_registered():
    """매일 겹치는 기간을 올려도 같은 거래는 한 번만. 같은 날 같은 금액 두 번 입금(한 파일 두 줄)은 두 건으로."""
    h = admin_headers()
    who = f"매일-{uuid.uuid4().hex[:6]}"
    d1, d2 = (TODAY - timedelta(days=2)).isoformat(), (TODAY - timedelta(days=1)).isoformat()
    day1 = [(d1, who, 1_000, "a"), (d1, who, 1_000, "a")]                       # 같은 날 같은 금액 2건(서로 다른 거래)
    r = _upload(h, day1)
    assert r["added"] == 2 and r["duplicates"] == 0
    r = _upload(h, day1 + [(d2, who, 2_000, "b")])                               # 다음 날: 어제 것 + 오늘 것
    assert r["added"] == 1 and r["duplicates"] == 2
    r = _upload(h, day1 + [(d2, who, 2_000, "b"), (d1, who, 1_000, "a")])        # 은행에 같은 거래가 3건이었다면 3번째만 새로
    assert r["added"] == 1 and r["duplicates"] == 3
    assert sum(1 for x in client.get("/api/v1/deposits", headers=h).json()["items"] if x["depositor"] == who) == 4

    # 거래시각·잔액 열이 있으면 그것까지 보고 판단
    who2 = f"잔액-{uuid.uuid4().hex[:6]}"
    head = ("거래일시", "보낸분", "맡기신금액", "적요", "잔액")
    rows = [(f"{d1} 09:00:01", who2, 5_000, "x", 105_000), (f"{d1} 15:30:00", who2, 5_000, "x", 110_000)]
    assert _upload(h, rows, head)["added"] == 2
    r = _upload(h, rows, head)
    assert r["added"] == 0 and r["duplicates"] == 2
