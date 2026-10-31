"""Phase 20 — 대시보드: 분기별 상환(예정)액(해당 연도 1~12월), 안내공문 발송예정·미수신(재단)·수신(SPC), 수신 기록."""
import uuid

from _helpers import admin_headers, client, make_loan, make_org, spc_manager_headers

ACCOUNT = {"bank_account": "가나은행 123-456-789012", "account_holder": "재단"}


def _template(h):
    r = client.post("/api/v1/document-templates", json={"template_name": f"t-{uuid.uuid4().hex[:6]}", "config": ACCOUNT}, headers=h)
    assert r.status_code == 201, r.text


def _dash(h, as_of, dorm):
    r = client.get("/api/v1/dashboard", params={"as_of": as_of, "dormitory_id": dorm}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _role_headers(h, role):
    email = f"{role.lower()}-{uuid.uuid4().hex[:6]}@example.com"
    assert client.post("/api/v1/users", json={"name": "x", "email": email, "role": role, "password": "rolepass123"},
                       headers=h).status_code == 201
    tok = client.post("/api/v1/auth/login", data={"username": email, "password": "rolepass123"}).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def test_quarterly_schedule_is_calendar_year_of_as_of():
    h = admin_headers()
    _, dorm = make_org(h)
    make_loan(h, dorm=dorm)            # 2023-02-28 배정, 2023-05-31부터 분기 원금균등 60개월, 1억
    d = _dash(h, "2024-06-15", dorm)
    q = d["quarterly_schedule"]
    assert [x["label"] for x in q] == ["1분기", "2분기", "3분기", "4분기"] and all(x["year"] == 2024 for x in q)
    assert [x["months"] for x in q] == ["1~3월", "4~6월", "7~9월", "10~12월"]
    assert all(x["principal"] == 5_000_000 for x in q)                      # 분기마다 1회 상환(2·5·8·11월)
    assert all(x["total"] == x["principal"] + x["interest"] and x["interest"] > 0 for x in q)
    assert sum(x["total"] for x in q) == d["kpis"]["this_year_scheduled"]  # 연간 합계와 일치(지난 분기 포함)


def test_notice_upcoming_unreceived_and_spc_receipt():
    h = admin_headers()
    _template(h)
    spc, dorm = make_org(h)
    make_loan(h, dorm=dorm)
    spc_h = spc_manager_headers(h, spc)

    before = _dash(h, "2023-05-20", dorm)
    up = before["alerts"]["repayment_upcoming"]["items"]
    assert up and up[0]["due_date"] == "2023-05-31" and up[0]["notice_issued"] is False
    assert before["notices"]["scope"] == "FOUNDATION" and before["kpis"]["notice_unreceived_count"] == 0

    nid = client.post(f"/api/v1/dormitories/{dorm}/notices", json={"due_date": "2023-05-31"}, headers=h).json()["id"]
    assert client.get(f"/api/v1/notices/{nid}", headers=h).status_code == 200      # 재단이 열어 봐도 수신 아님
    d = _dash(h, "2023-05-20", dorm)
    up = d["alerts"]["repayment_upcoming"]["items"][0]
    assert up["notice_issued"] is True and up["notice_received"] is False and up["notice_id"] == nid
    un = d["notices"]["unreceived"]
    assert un["count"] == 1 and un["items"][0]["notice_id"] == nid and un["items"][0]["total"] > 0
    assert d["kpis"]["notice_unreceived_count"] == 1

    # SPC 담당자: '안내공문 수신'에 자기 기숙사 공문, 미확인 1 → 내려받으면 수신
    s = _dash(spc_h, "2023-05-20", dorm)
    assert s["notices"]["scope"] == "SPC" and "notice_unreceived_count" not in s["kpis"]
    assert s["notices"]["received"]["unconfirmed"] == 1 and s["notices"]["received"]["items"][0]["received"] is False
    assert client.get(f"/api/v1/notices/{nid}/download", headers=spc_h).status_code == 200
    s = _dash(spc_h, "2023-05-20", dorm)
    item = s["notices"]["received"]["items"][0]
    assert item["received"] is True and item["received_via"] == "DOWNLOAD" and item["received_by_name"]

    d = _dash(h, "2023-05-20", dorm)
    assert d["notices"]["unreceived"]["count"] == 0
    assert d["alerts"]["repayment_upcoming"]["items"][0]["notice_received"] is True
    assert client.get(f"/api/v1/notices/{nid}", headers=h).json()["received_via"] == "DOWNLOAD"

    # 같은 상환일 재발급 → 새 발급본은 다시 미수신. SPC가 공문 알림을 읽으면 수신
    nid2 = client.post(f"/api/v1/dormitories/{dorm}/notices", json={"due_date": "2023-05-31"}, headers=h).json()["id"]
    d = _dash(h, "2023-05-20", dorm)
    assert d["notices"]["unreceived"]["count"] == 1 and d["notices"]["unreceived"]["items"][0]["notice_id"] == nid2
    n = next(x for x in client.get("/api/v1/notifications", headers=spc_h).json() if x["notification_type"] == "NOTICE_ISSUED")
    assert client.post(f"/api/v1/notifications/{n['id']}/read", headers=spc_h).status_code == 200
    assert _dash(h, "2023-05-20", dorm)["notices"]["unreceived"]["count"] == 0
    assert client.get(f"/api/v1/notices/{nid2}", headers=h).json()["received_via"] == "NOTIFICATION"

    # 납부기한이 지나면 미수신 목록에서 빠짐
    nid3 = client.post(f"/api/v1/dormitories/{dorm}/notices", json={"due_date": "2023-08-31"}, headers=h).json()["id"]
    assert _dash(h, "2023-08-01", dorm)["notices"]["unreceived"]["items"][0]["notice_id"] == nid3
    assert _dash(h, "2023-09-15", dorm)["notices"]["unreceived"]["count"] == 0


def test_other_roles_see_no_notice_lists_and_spc_sees_only_own():
    h = admin_headers()
    _template(h)
    spc_a, dorm_a = make_org(h)
    _, dorm_b = make_org(h)
    make_loan(h, dorm=dorm_a)
    make_loan(h, dorm=dorm_b)
    for dm in (dorm_a, dorm_b):
        assert client.post(f"/api/v1/dormitories/{dm}/notices", json={"due_date": "2023-05-31"}, headers=h).status_code == 201
    for role in ("VIEWER", "FOUNDATION_APPROVER"):
        d = client.get("/api/v1/dashboard", params={"as_of": "2023-05-20"}, headers=_role_headers(h, role)).json()
        assert d["notices"] == {"scope": "NONE"} and "notice_unreceived_count" not in d["kpis"]
    fund = client.get("/api/v1/dashboard", params={"as_of": "2023-05-20"}, headers=_role_headers(h, "FOUNDATION_FUND_MANAGER")).json()
    assert fund["notices"]["scope"] == "FOUNDATION"
    s = client.get("/api/v1/dashboard", params={"as_of": "2023-05-20"}, headers=spc_manager_headers(h, spc_a)).json()
    assert {i["dormitory_id"] for i in s["notices"]["received"]["items"]} == {dorm_a}


def test_quarterly_planned_vs_paid_by_due_date_quarter():
    h = admin_headers()
    loan_id, dorm, _ = make_loan(h)     # 분기 회차: 2·5·8·11월 말
    summ = client.get(f"/api/v1/loans/{loan_id}/payments/summary", params={"as_of": "2024-06-15"}, headers=h).json()
    feb = next(r for r in summ["rows"] if r["due_date"] == "2024-02-29")      # 같은 날 행이 여럿일 수 있어 회차 합계로
    pay = lambda due, paid, pr, it: client.post(f"/api/v1/loans/{loan_id}/payments", headers=h, json={  # noqa: E731
        "due_date": due, "actual_paid_date": paid, "actual_principal": pr, "actual_interest": it})
    assert pay("2024-02-29", "2024-04-02", feb["scheduled_principal"], feb["scheduled_interest"]).status_code in (200, 201)  # 늦게 냄
    assert pay("2024-05-31", "2024-05-31", 1_000_000, 0).status_code in (200, 201)                                      # 일부
    q = _dash(h, "2024-06-15", dorm)["quarterly_schedule"]
    assert q[0]["paid"]["total"] == feb["scheduled_total"]   # 4월에 냈어도 1분기 회차
    assert q[0]["paid"]["total"] == q[0]["planned"]["total"]
    assert q[1]["paid"] == {"principal": 1_000_000, "interest": 0, "total": 1_000_000}
    assert q[1]["planned"]["total"] > q[1]["paid"]["total"]
    assert q[2]["paid"] is None and q[3]["paid"] is None and q[2]["started"] is False   # 아직 시작 안 한 분기
    assert q[0]["total"] == q[0]["planned"]["total"]                                     # 호환 필드
    assert q[1]["planned_due_total"] == q[1]["planned"]["total"]                          # 5/31 회차 도래
    q2 = _dash(h, "2024-05-15", dorm)["quarterly_schedule"]
    assert q2[1]["planned_due_total"] == 0 and q2[1]["planned"]["total"] > 0              # 2분기 회차 아직 안 옴
