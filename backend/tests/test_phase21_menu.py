"""Phase 21 — 메뉴 재구성: 기숙사별 입금계좌, 안내공문 발송대상/발송완료, 자금담당자 차입금 삭제, 차입금 목록 필드."""
import uuid

from _helpers import admin_headers, client, make_loan, make_org, spc_manager_headers

ACCOUNT = {"bank_account": "공통은행 111-111-111111", "account_holder": "재단공통"}


def _role(h, role):
    email = f"{role.lower()}-{uuid.uuid4().hex[:6]}@example.com"
    assert client.post("/api/v1/users", json={"name": "x", "email": email, "role": role, "password": "rolepass123"},
                       headers=h).status_code == 201
    return {"Authorization": f"Bearer {client.post('/api/v1/auth/login', data={'username': email, 'password': 'rolepass123'}).json()['access_token']}"}


def test_dormitory_account_overrides_template_and_masks_for_readers():
    h = admin_headers()
    client.post("/api/v1/document-templates", json={"template_name": f"t-{uuid.uuid4().hex[:6]}", "config": ACCOUNT}, headers=h)
    spc, dorm = make_org(h)
    make_loan(h, dorm=dorm)
    url = f"/api/v1/dormitories/{dorm}/account"
    assert client.get(url, headers=h).json()["uses_common"] is True
    pv = client.get(f"/api/v1/dormitories/{dorm}/notices/preview", params={"due_date": "2023-05-31"}, headers=h).json()
    assert pv["account"]["account_holder"] == "재단공통" and pv["account_source"] == "TEMPLATE"

    assert client.put(url, json={"bank_account": "가람은행 222-333-444555"}, headers=h).status_code == 422   # 예금주 없음
    r = client.put(url, json={"bank_account": "가람은행 222-333-444555", "account_holder": "가람관(주)"}, headers=h)
    assert r.status_code == 200 and r.json()["uses_common"] is False and r.json()["bank_account"] == "가람은행 222-333-444555"
    pv = client.get(f"/api/v1/dormitories/{dorm}/notices/preview", params={"due_date": "2023-05-31"}, headers=h).json()
    assert pv["account"] == {"bank_account": "가람은행 222-333-444555", "account_holder": "가람관(주)"} and pv["account_source"] == "DORMITORY"
    n = client.post(f"/api/v1/dormitories/{dorm}/notices", json={"due_date": "2023-05-31"}, headers=h).json()
    assert n["generated_data"]["account"]["bank_account"] == "가람은행 222-333-444555"

    spc_h = spc_manager_headers(h, spc)
    masked = client.get(url, headers=spc_h).json()["bank_account"]
    assert masked.endswith("4555") and "333" not in masked                       # SPC는 끝 4자리만
    assert client.put(url, json={"bank_account": "x 1", "account_holder": "y"}, headers=spc_h).status_code == 403
    assert client.put(url, json={"bank_account": "", "account_holder": ""}, headers=h).json()["uses_common"] is True
    logs = str(client.get("/api/v1/admin/audit-logs", params={"table_name": "dormitory"}, headers=h).json())
    assert "기숙사 입금계좌 변경" in logs and "222-333" not in logs                  # 감사로그에도 가린 값만


def test_notice_targets_and_issued_lists():
    h = admin_headers()
    client.post("/api/v1/document-templates", json={"template_name": f"t-{uuid.uuid4().hex[:6]}", "config": ACCOUNT}, headers=h)
    spc, dorm = make_org(h)
    make_loan(h, dorm=dorm)
    make_loan(h, dorm=dorm)                       # 같은 기숙사 차입금 2건 → 한 줄로 합산
    t = client.get("/api/v1/notices/targets", params={"as_of": "2023-05-20", "dormitory_id": dorm}, headers=h).json()
    assert t["default_window"] is True and t["count"] == 1
    it = t["items"][0]
    assert it["due_date"] == "2023-05-31" and len(it["loans"]) == 2 and it["issued"] is False and it["business_days_left"] <= 15
    # 기간 지정: 2023년 전체 → 4회차(5·8·11월 + 2024-02는 제외)
    t2 = client.get("/api/v1/notices/targets", params={"as_of": "2023-05-20", "date_from": "2023-01-01", "date_to": "2023-12-31",
                                                     "dormitory_id": dorm}, headers=h).json()
    assert [x["due_date"] for x in t2["items"]] == ["2023-05-31", "2023-08-31", "2023-11-30"]
    # 발급하면 발송대상에서 빠지고 발송완료로
    nid = client.post(f"/api/v1/dormitories/{dorm}/notices", json={"due_date": "2023-05-31"}, headers=h).json()["id"]
    assert client.get("/api/v1/notices/targets", params={"as_of": "2023-05-20", "dormitory_id": dorm}, headers=h).json()["count"] == 0
    inc = client.get("/api/v1/notices/targets", params={"as_of": "2023-05-20", "dormitory_id": dorm, "include_issued": "true"}, headers=h).json()
    assert inc["items"][0]["issued"] is True and inc["items"][0]["notice_id"] == nid
    iss = client.get("/api/v1/notices/issued", params={"dormitory_id": dorm}, headers=h).json()
    assert iss["count"] == 1 and iss["unreceived"] == 1 and iss["items"][0]["notice_id"] == nid
    assert client.get("/api/v1/notices/issued", params={"dormitory_id": dorm, "received": "true"}, headers=h).json()["count"] == 0
    spc_h = spc_manager_headers(h, spc)
    client.get(f"/api/v1/notices/{nid}", headers=spc_h)                            # 열람 → 수신
    iss = client.get("/api/v1/notices/issued", params={"dormitory_id": dorm, "received": "true"}, headers=h).json()
    assert iss["count"] == 1 and iss["items"][0]["received_via"] == "VIEW"
    assert client.get("/api/v1/notices/targets", headers=spc_h).status_code == 403  # 발송대상은 재단만
    _, other = make_org(h)
    assert all(i["dormitory_id"] == dorm for i in client.get("/api/v1/notices/issued", headers=spc_h).json()["items"])


def test_fund_manager_can_delete_loan_but_not_restore():
    h = admin_headers()
    loan_id, dorm, _ = make_loan(h)
    fm = _role(h, "FOUNDATION_FUND_MANAGER")
    for role in ("VIEWER", "FOUNDATION_APPROVER"):
        assert client.post(f"/api/v1/admin/delete/loans/{loan_id}", json={"reason": "x"}, headers=_role(h, role)).status_code == 403
    r = client.post(f"/api/v1/admin/delete/loans/{loan_id}", json={"reason": "자금담당 삭제"}, headers=fm)
    assert r.status_code == 200, r.text
    assert client.post(f"/api/v1/admin/deletions/{r.json()['id']}/restore", headers=fm).status_code == 403
    assert client.post(f"/api/v1/admin/delete/dormitories/{dorm}", json={"reason": "x"}, headers=fm).status_code == 403   # 기숙사는 관리자만
    assert client.post(f"/api/v1/admin/deletions/{r.json()['id']}/restore", headers=h).status_code == 200


def test_loan_rows_have_master_fields():
    h = admin_headers()
    _, dorm, _ = make_loan(h)
    row = client.get("/api/v1/dashboard", params={"as_of": "2024-01-01", "dormitory_id": dorm}, headers=h).json()["loans"][0]
    assert row["repay_period_months"] == 60 and row["principal_repay_start_date"] == "2023-05-31"
    assert row["maturity_date"] >= "2027-11-30" and "project_type" in row and "grace_start_date" in row
