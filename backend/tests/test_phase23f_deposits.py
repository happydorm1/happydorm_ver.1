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


def test_issued_list_shows_payment_status():
    """발송완료 목록: 입금 전 → (대기 입금) 확인 중 → (승인) 입금 완료·입금일시, 입금 상태 필터."""
    h = admin_headers()
    loan_id, dorm, _, n, dname = _setup(h, 1_000_000_000 + random.randint(1, 9_999) * 1_000)
    total = n["generated_data"]["totals"]["total"]

    def item(**p):
        r = client.get("/api/v1/notices/issued", params={"dormitory_id": dorm, **p}, headers=h).json()
        return next((x for x in r["items"] if x["notice_id"] == n["id"]), None)

    assert item()["pay_status"] == "UNPAID" and item()["paid_date"] is None
    assert item(paid="UNPAID") is not None and item(paid="PAID") is None
    r = _upload(h, [(f"{TODAY.isoformat()} 14:05", dname, total, "상환", 999)], ("거래일시", "보낸분", "맡기신금액", "적요", "잔액"))
    assert r["pending"] == 1                                               # 2천만 원 이상 → 대기
    x = item()
    assert x["pay_status"] == "CHECKING" and x["paid_date"] == TODAY.isoformat() and x["paid_time"] == "14:05:00"
    dep = _find(h, dname)
    assert dep["deposit_time"] == "14:05:00"
    client.post(f"/api/v1/deposits/{dep['id']}/approve", json={}, headers=h)
    x = item(paid="PAID")
    assert x["pay_status"] == "PAID" and x["pay_via"] == "APPROVED" and x["paid_time"] == "14:05:00"


def test_due_soon_unpaid_dashboard_notification_and_auto_receipt():
    """납부기한 영업일 D-3 이내 미입금 → 대시보드 '납부기한 임박 미입금'·알림(UNPAID_D3, 1회). 입금 완료 → 공문 수신 자동 처리."""
    h = admin_headers()
    loan_id, dorm, _, n, dname = _setup(h, 100_000_000 + random.randint(1, 9_999) * 1_000)
    deadline = date.fromisoformat(n["payment_deadline"])
    near = deadline - timedelta(days=1)
    while near.weekday() >= 5:
        near -= timedelta(days=1)
    far = deadline - timedelta(days=40)

    def soon(as_of):
        d = client.get("/api/v1/dashboard", params={"as_of": as_of.isoformat(), "dormitory_id": dorm}, headers=h).json()
        return [x for x in d["notices"]["due_soon_unpaid"]["items"] if x["notice_id"] == n["id"]]

    assert soon(far) == []
    s = soon(near)
    assert s and s[0]["due_soon"] and s[0]["days_left"] <= 3 and s[0]["pay_status"] == "UNPAID"

    email = f"fund-{uuid.uuid4().hex[:6]}@example.com"               # 알림 받는 사람: 재단 자금담당자
    assert client.post("/api/v1/users", json={"name": "자금", "email": email, "role": "FOUNDATION_FUND_MANAGER",
                                              "password": "fundpass123"}, headers=h).status_code == 201
    fh = {"Authorization": "Bearer " + client.post("/api/v1/auth/login", data={"username": email, "password": "fundpass123"}).json()["access_token"]}
    r = client.post("/api/v1/notifications/scan", params={"as_of": near.isoformat(), "dormitory_id": dorm}, headers=h).json()
    assert r["created"]["unpaid_due_soon"] >= 1
    again = client.post("/api/v1/notifications/scan", params={"as_of": near.isoformat(), "dormitory_id": dorm}, headers=h).json()
    assert again["created"]["unpaid_due_soon"] == 0                       # 한 번만
    mine = client.get("/api/v1/notifications", headers=fh).json()
    items = mine["items"] if isinstance(mine, dict) else mine
    assert any(x["notification_type"] == "UNPAID_D3" and dname in x["title"] for x in items)

    # 입금 완료 → 공문 '입금 확인으로 수신', 임박 목록에서 빠짐 / 되돌리기 → 다시 미수신
    assert client.get(f"/api/v1/notices/{n['id']}", headers=h).json()["received_at"] is None
    total = n["generated_data"]["totals"]["total"]
    _dep(h, depositor=dname, amount=total)
    doc = client.get(f"/api/v1/notices/{n['id']}", headers=h).json()
    assert doc["received_at"] and doc["received_via"] == "DEPOSIT"
    assert soon(near) == []
    dep = _find(h, dname)
    client.post(f"/api/v1/deposits/{dep['id']}/revert", json={"reason": "t"}, headers=h)
    assert client.get(f"/api/v1/notices/{n['id']}", headers=h).json()["received_at"] is None


# ---------- 가상계좌(CMS) 은행 파일 · 기숙사 식별 · 분할 입금(2026-11-09) ----------
CMS_HEAD = ("거래일자", "거래시간", "가상계좌번호(상대계좌)", "입금종류", "수납구분", "수납액", "출금액", "잔액",
            "기재내역(입금인명)", "고객KEY", "고객명", "거래번호", "비고", "취소원거래번호", "취소원거래일")


def _cms(h, rows):
    return _upload(h, rows, CMS_HEAD)


def _cms_row(d, tm, acct, amount, bal, payer, ckey, cname, txno):
    return (d, tm, acct, "펌뱅킹", "입금", f"{amount:,}", "0", f"{bal:,}", payer, ckey, cname, txno, "", "", "")


def test_read_bank_cms_xls_sample():
    """은행에서 내려받은 .xls(가상계좌 CMS) 그대로 읽기 — 수납액·기재내역(입금인명)·가상계좌·고객KEY·고객명·거래시간."""
    from pathlib import Path
    from app.domain.deposit_service import read_rows
    content = (Path(__file__).parent / "data" / "bank_cms_sample.xls").read_bytes()
    rows, errors = read_rows(content, "거래내역.xls")
    assert errors == [] and len(rows) == 3
    r0, r1, r2 = rows
    assert r0["amount"] == 112_894_030 and r0["depositor"] == "○○대학교-081" and r0["customer_key"] == "68"
    assert r0["customer_name"] == "○○대" and r0["virtual_account"] == "000-000000-00-000" and r0["deposit_time"] == "11:29:35"
    assert (r1["amount"], r2["amount"]) == (10_841_330, 50_000_000) and r1["customer_key"] == r2["customer_key"] == "14"
    assert r1["extra_key"] != r2["extra_key"]


def _ident(h, dorm, **body):
    r = client.put(f"/api/v1/deposits/dorm-idents/{dorm}", json=body, headers=h)
    assert r.status_code == 200, r.text


def test_split_principal_interest_grouped_by_customer_key():
    """원금·이자를 나눠 입금(같은 날 2건) — 고객KEY로 기숙사를 알아보고 합계가 공문과 같으면 묶음. 2천만 원 이상이라 대기 → 한 번 승인으로 둘 다 완료."""
    h = admin_headers()
    loan_id, dorm, _, n, dname = _setup(h, 1_000_000_000 + random.randint(1, 9_999) * 1_000)
    t = n["generated_data"]["totals"]
    ck = f"K{uuid.uuid4().hex[:6]}"
    _ident(h, dorm, customer_key=ck, payer_names=[])
    d = TODAY.isoformat()
    va = f"111-{random.randint(100000, 999999)}-11-111"
    r = _cms(h, [_cms_row(d, "13:42:08", va, t["interest"], 100, "★★교류센터-084", ck, "★★", "4"),
                 _cms_row(d, "13:47:06", va, t["principal"], 200, "★★교류센터-084", ck, "★★", "5")])
    assert r["added"] == 2 and r["grouped"] == 2 and r["pending"] == 2
    items = [x for x in client.get("/api/v1/deposits", headers=h).json()["items"] if x["customer_key"] == ck]
    assert len(items) == 2 and all(x["notice"]["id"] == n["id"] for x in items)
    assert all(x["dormitory"]["id"] == dorm for x in items)
    assert items[0]["group"]["count"] == 2 and items[0]["group"]["total"] == t["total"]
    assert all([c["code"] for c in x["hold_reasons"]] == ["OVER_LIMIT"] for x in items)
    assert items[0]["virtual_account"].endswith("1111") and "*" in items[0]["virtual_account"]
    ok = client.post(f"/api/v1/deposits/{items[0]['id']}/approve", json={}, headers=h)
    assert ok.status_code == 200, ok.text
    items = [x for x in client.get("/api/v1/deposits", headers=h).json()["items"] if x["customer_key"] == ck]
    assert all(x["status"] == "COMPLETED" for x in items) and sum(x["payment_count"] for x in items) == 1
    row = _paid(h, loan_id, n["due_date"])
    assert row["paid_total"] == t["total"] and row["status"] == "PAID"
    # 되돌리기 → 둘 다 대기, 납부 기록 취소
    client.post(f"/api/v1/deposits/{items[1]['id']}/revert", json={"reason": "t"}, headers=h)
    items = [x for x in client.get("/api/v1/deposits", headers=h).json()["items"] if x["customer_key"] == ck]
    assert all(x["status"] == "PENDING" for x in items)
    assert _paid(h, loan_id, n["due_date"])["paid_total"] == 0


def test_split_on_different_days_waits_then_auto_approves():
    """원금만 먼저(나머지 대기) → 다음 날 이자 입금 업로드 → 묶여서 자동 승인(2천만 원 미만). 입금인명은 시스템 기숙사명과 다름."""
    h = admin_headers()
    loan_id, dorm, _, n, dname = _setup(h, 100_000_000 + random.randint(1, 9_999) * 1_000)
    t = n["generated_data"]["totals"]
    acct = f"222-{random.randint(100000, 999999)}-22-222"
    _ident(h, dorm, virtual_account=acct, payer_names=[])
    payer = f"R{uuid.uuid4().hex[:10]}-{random.randint(100, 999)}"
    r = _cms(h, [_cms_row(TODAY.isoformat(), "09:00:00", acct, t["principal"], 1, payer, "", "", "1")])
    assert r["pending"] == 1
    x = _find(h, payer)
    assert x["dormitory"]["id"] == dorm and x["notice"]["id"] == n["id"] and [c["code"] for c in x["hold_reasons"]] == ["SPLIT_WAIT"]
    r = _cms(h, [_cms_row(TODAY.isoformat(), "15:00:00", acct, t["interest"], 2, payer, "", "", "2")])
    assert r["auto_completed"] == 1
    items = [i for i in client.get("/api/v1/deposits", headers=h).json()["items"] if i["depositor"] == payer]
    assert len(items) == 2 and all(i["status"] == "COMPLETED" and i["auto_approved"] for i in items)
    assert _paid(h, loan_id, n["due_date"])["paid_total"] == t["total"]


def test_assign_learns_customer_key_for_next_time():
    """처음 보는 고객KEY·입금인명 → 대상 공문 없음 → 담당자가 공문 지정 → 기숙사 식별정보에 기억 → 다음 입금은 자동으로 기숙사를 알아봄."""
    h = admin_headers()
    loan_id, dorm, _, n, dname = _setup(h, 100_000_000 + random.randint(1, 9_999) * 1_000)
    t = n["generated_data"]["totals"]
    ck = f"L{uuid.uuid4().hex[:6]}"
    payer = f"P{uuid.uuid4().hex[:10]}"
    _cms(h, [_cms_row(TODAY.isoformat(), "10:00:00", "", 777, 1, payer, ck, "", "1")])
    x = _find(h, payer)
    assert x["dormitory"] is None
    client.post(f"/api/v1/deposits/{x['id']}/assign", json={"notice_id": n["id"]}, headers=h)
    ident = next(i for i in client.get("/api/v1/deposits/dorm-idents", headers=h).json() if i["id"] == dorm)
    assert ident["customer_key"] == ck and payer in ident["payer_names"]
    payer2 = f"Q{uuid.uuid4().hex[:10]}"
    r = _cms(h, [_cms_row(TODAY.isoformat(), "11:00:00", "", t["total"], 2, payer2, ck, "", "2")])
    assert r["auto_completed"] == 1
    assert _find(h, payer2)["dormitory"]["id"] == dorm
    # 같은 고객KEY를 다른 기숙사에 넣으면 막음
    _, dorm2, _, _, _ = _setup(h, 100_000_000 + random.randint(1, 9_999) * 1_000)
    bad = client.put(f"/api/v1/deposits/dorm-idents/{dorm2}", json={"customer_key": ck}, headers=h)
    assert bad.status_code == 422 and "이미 등록" in bad.text
