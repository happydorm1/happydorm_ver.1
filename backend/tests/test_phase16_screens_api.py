"""Phase 16 업무 화면용 API: 전체 이벤트 이력(취소 가능 표시), 재원·코드 사용중지, 내 정보의 결재 모드"""
from _helpers import admin_headers, client, make_loan, make_org, spc_manager_headers


def test_all_events_list_marks_latest_cancellable_and_scopes_by_spc():
    h = admin_headers()
    spc, dorm = make_org(h)
    loan, _, _ = make_loan(h, dorm=dorm)
    client.post(f"/api/v1/loans/{loan}/events/rate-change", json={"effective_start_date": "2024-08-31", "annual_rate": "3.3"}, headers=h).raise_for_status()
    client.post(f"/api/v1/loans/{loan}/events/early-repayment", json={"event_date": "2024-11-15", "repay_type": "PARTIAL",
                                                                      "repay_amount": 5_000_000}, headers=h).raise_for_status()
    rows = client.get("/api/v1/events", params={"loan_id": loan}, headers=h).json()
    assert [r["event_type"] for r in rows] == ["EARLY_REPAYMENT", "RATE_CHANGE"]            # 최근 등록 순
    assert [r["cancellable"] for r in rows] == [True, False]                                # 가장 최근 것만 취소 가능
    assert rows[0]["dormitory_name"] and rows[0]["resulting_version_no"] == 3 and rows[0]["created_by_name"]
    assert client.get("/api/v1/events", params={"loan_id": loan, "event_type": "RATE_CHANGE"}, headers=h).json()[0]["event_type"] == "RATE_CHANGE"
    other_spc, _ = make_org(h)
    assert client.get("/api/v1/events", params={"loan_id": loan}, headers=spc_manager_headers(h, other_spc)).status_code == 403


def test_fund_and_code_deactivate_keeps_record_and_audits():
    h = admin_headers()
    import uuid
    f = client.post("/api/v1/reference/funds", json={"fund_name": f"테스트기금-{uuid.uuid4().hex[:6]}", "interest_method": "SIMPLE"}, headers=h).json()
    assert client.post(f"/api/v1/reference/funds/{f['id']}/active", params={"value": "false"}, headers=h).json()["is_active"] is False
    assert f["id"] not in [x["id"] for x in client.get("/api/v1/reference/funds", headers=h).json()]
    assert f["id"] in [x["id"] for x in client.get("/api/v1/reference/funds", params={"include_inactive": "true"}, headers=h).json()]
    c = client.get("/api/v1/reference/codes", params={"group_code": "GRACE_TYPE"}, headers=h).json()[-1]
    client.post(f"/api/v1/reference/codes/{c['id']}/active", params={"value": "false"}, headers=h).raise_for_status()
    client.post(f"/api/v1/reference/codes/{c['id']}/active", params={"value": "true"}, headers=h).raise_for_status()
    audit = client.get("/api/v1/admin/audit-logs", params={"table_name": "fund"}, headers=h).json()
    assert any(a["record_id"] == f["id"] and a["after"] == "False" for a in audit)
    assert client.get("/api/v1/auth/me", headers=h).json()["event_approval_required"] is False
