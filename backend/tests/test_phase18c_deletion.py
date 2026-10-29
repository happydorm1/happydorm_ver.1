"""Phase 18c — 관리자 삭제(숨김)·복구: 기숙사(소속 차입금 포함)·차입금·원리금 테이블·안내공문."""
import uuid

from _helpers import admin_headers, client, make_loan, make_org, spc_manager_headers

ACCOUNT = {"bank_account": "가나은행 123-456-789012", "account_holder": "재단"}


def _fund_headers(h):
    email = f"fund-{uuid.uuid4().hex[:8]}@example.com"
    assert client.post("/api/v1/users", json={"name": "자금", "email": email, "role": "FOUNDATION_FUND_MANAGER",
                                              "password": "fundpass123"}, headers=h).status_code == 201
    tok = client.post("/api/v1/auth/login", data={"username": email, "password": "fundpass123"}).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def _codes(h):
    return {l["loan_code"] for l in client.get("/api/v1/loans", headers=h).json()}


def _deletions(h, **p):
    return client.get("/api/v1/admin/deletions", params=p, headers=h).json()


def test_delete_loan_reuse_code_and_restore_conflict():
    h = admin_headers()
    loan_id, dorm, _ = make_loan(h)
    code = client.get(f"/api/v1/loans/{loan_id}", headers=h).json()["loan_code"]

    # 관리자만, 사유 필수
    assert client.post(f"/api/v1/admin/delete/loans/{loan_id}", json={"reason": "x"}, headers=_fund_headers(h)).status_code == 403
    assert client.post(f"/api/v1/admin/delete/loans/{loan_id}", json={"reason": ""}, headers=h).status_code == 422

    rec = client.post(f"/api/v1/admin/delete/loans/{loan_id}", json={"reason": "테스트 데이터"}, headers=h)
    assert rec.status_code == 200, rec.text
    rec = rec.json()
    assert rec["target_type"] == "LOAN" and rec["deleted_by_name"]
    assert code not in _codes(h)
    assert client.get(f"/api/v1/loans/{loan_id}", headers=h).status_code == 404
    assert client.get(f"/api/v1/loans/{loan_id}/schedule", headers=h).status_code == 404
    dash = client.get("/api/v1/dashboard", params={"as_of": "2024-01-01"}, headers=h).text
    assert loan_id not in dash

    # 같은 코드로 다시 등록 가능 → 그 사이에는 복구가 막힘(코드 충돌)
    l2 = client.get("/api/v1/reference/funds", headers=h).json()
    funds = {f["fund_name"]: f["id"] for f in l2}
    cyc = {c["code"]: c["id"] for c in client.get("/api/v1/reference/codes", params={"group_code": "REPAY_CYCLE"}, headers=h).json()}
    mth = {c["code"]: c["id"] for c in client.get("/api/v1/reference/codes", params={"group_code": "REPAY_METHOD"}, headers=h).json()}
    new = client.post("/api/v1/loans", json={"dormitory_id": dorm, "fund_id": funds["공공자금관리기금"], "loan_code": code,
                                             "assigned_amount": 100_000_000, "assigned_date": "2023-02-28", "rate_type": "FIXED",
                                             "repay_cycle_code_id": cyc["QUARTERLY"], "repay_method_code_id": mth["EQUAL_PRINCIPAL"],
                                             "principal_repay_start_date": "2023-05-31", "repay_period_months": 60,
                                             "initial_rate": {"effective_start_date": "2023-02-28", "annual_rate": "3.0"}}, headers=h)
    assert new.status_code == 201, new.text
    blocked = client.post(f"/api/v1/admin/deletions/{rec['id']}/restore", headers=h)
    assert blocked.status_code == 409 and code in blocked.json()["detail"]

    # 새로 만든 것을 지우면 원래 것을 복구할 수 있고, 코드도 원래대로
    assert client.post(f"/api/v1/admin/delete/loans/{new.json()['id']}", json={"reason": "중복"}, headers=h).status_code == 200
    ok = client.post(f"/api/v1/admin/deletions/{rec['id']}/restore", headers=h)
    assert ok.status_code == 200 and ok.json()["restored_at"] and ok.json()["restored_by_name"]
    assert client.get(f"/api/v1/loans/{loan_id}", headers=h).json()["loan_code"] == code
    assert client.get(f"/api/v1/loans/{loan_id}/schedule", headers=h).status_code == 200
    assert client.post(f"/api/v1/admin/deletions/{rec['id']}/restore", headers=h).status_code == 409   # 두 번 복구 불가

    acts = {(a["action"]) for a in client.get(f"/api/v1/loans/{loan_id}/audit-logs", headers=h).json()}
    assert {"DELETE", "RESTORE"} <= acts
    assert rec["id"] not in {d["id"] for d in _deletions(h)}                       # 기본 목록은 복구 안 된 것만
    assert rec["id"] in {d["id"] for d in _deletions(h, include_restored="true")}


def test_delete_schedule_then_regenerate_and_restore():
    h = admin_headers()
    loan_id, _, _ = make_loan(h)
    v1 = client.get(f"/api/v1/loans/{loan_id}/schedule", headers=h).json()
    assert v1["version_no"] == 1

    rec = client.post(f"/api/v1/admin/delete/loans/{loan_id}/schedule", json={"reason": "조건 수정 후 재생성"}, headers=h).json()
    assert rec["target_type"] == "SCHEDULE"
    assert client.get(f"/api/v1/loans/{loan_id}/schedule", headers=h).status_code == 404
    assert client.get(f"/api/v1/loans/{loan_id}/schedule/versions", headers=h).json() == []
    # 테이블이 없으니 계약조건 수정 가능 → 다시 생성(번호는 이어서)
    up = client.put(f"/api/v1/loans/{loan_id}", json={"repay_period_months": 48, "change_reason": "재생성"}, headers=h)
    assert up.status_code == 200, up.text
    g = client.post(f"/api/v1/loans/{loan_id}/schedule/generate", headers=h)
    assert g.status_code == 201 and g.json()["version_no"] == 2 and g.json()["generated_reason"] == "최초생성"
    assert [v["version_no"] for v in client.get(f"/api/v1/loans/{loan_id}/schedule/versions", headers=h).json()] == [2]
    assert client.get(f"/api/v1/loans/{loan_id}/schedule/verification", headers=h).json()["version_no"] == 2

    # 지금 쓰는 테이블이 있으면 옛 테이블 복구 불가
    assert client.post(f"/api/v1/admin/deletions/{rec['id']}/restore", headers=h).status_code == 409

    # 적용된 이벤트가 있으면 삭제 불가
    assert client.post(f"/api/v1/loans/{loan_id}/events/rate-change",
                       json={"effective_start_date": "2024-08-31", "annual_rate": "3.5"}, headers=h).status_code == 201
    r = client.post(f"/api/v1/admin/delete/loans/{loan_id}/schedule", json={"reason": "x"}, headers=h)
    assert r.status_code == 409 and "이벤트" in r.json()["detail"]


def test_delete_notice_and_dormitory_cascade_restore():
    h = admin_headers()
    client.post("/api/v1/document-templates", json={"template_name": f"t-{uuid.uuid4().hex[:6]}", "config": ACCOUNT}, headers=h)
    spc, dorm = make_org(h)
    loan_id, _, _ = make_loan(h, dorm=dorm)
    spc_h = spc_manager_headers(h, spc)
    nid = client.post(f"/api/v1/dormitories/{dorm}/notices", json={"due_date": "2023-05-31"}, headers=h).json()["id"]

    def spc_notice_alerts():
        return [n for n in client.get("/api/v1/notifications", headers=spc_h).json() if n["notification_type"] == "NOTICE_ISSUED"]

    assert len(spc_notice_alerts()) == 1
    nrec = client.post(f"/api/v1/admin/delete/notices/{nid}", json={"reason": "잘못 발급"}, headers=h).json()
    assert client.get(f"/api/v1/dormitories/{dorm}/notices", headers=h).json() == []
    assert client.get(f"/api/v1/notices/{nid}", headers=h).status_code == 404
    assert spc_notice_alerts() == []
    assert client.post(f"/api/v1/admin/deletions/{nrec['id']}/restore", headers=h).status_code == 200
    assert len(client.get(f"/api/v1/dormitories/{dorm}/notices", headers=h).json()) == 1 and len(spc_notice_alerts()) == 1

    # 기숙사 삭제 → 소속 차입금·공문·알림 모두 안 보임
    overview = {d["id"]: d for d in client.get("/api/v1/admin/dormitories", headers=h).json()}
    assert overview[dorm]["loan_count"] == 1
    drec = client.post(f"/api/v1/admin/delete/dormitories/{dorm}", json={"reason": "가상 기숙사 정리"}, headers=h).json()
    assert drec["target_type"] == "DORMITORY" and len(drec["detail"]["loans"]) == 1
    assert dorm not in {d["id"] for d in client.get("/api/v1/dormitories", headers=h).json()}
    assert client.get(f"/api/v1/loans/{loan_id}", headers=h).status_code == 404
    assert client.get(f"/api/v1/dormitories/{dorm}/notices", headers=h).status_code == 404
    assert dorm not in client.get("/api/v1/dashboard/filter-options", headers=h).text
    assert spc_notice_alerts() == []

    # 소속 차입금만 따로 복구하려 하면 기숙사 먼저 복구하라고 안내 — (차입금 단독 삭제 기록이 없으므로 기숙사 기록으로 복구)
    assert client.post(f"/api/v1/admin/deletions/{drec['id']}/restore", headers=h).status_code == 200
    assert client.get(f"/api/v1/loans/{loan_id}", headers=h).status_code == 200
    assert len(client.get(f"/api/v1/dormitories/{dorm}/notices", headers=h).json()) == 1

    # 기숙사가 지워진 상태에서는 그 기숙사의 차입금 단독 복구 불가
    lrec = client.post(f"/api/v1/admin/delete/loans/{loan_id}", json={"reason": "a"}, headers=h).json()
    drec2 = client.post(f"/api/v1/admin/delete/dormitories/{dorm}", json={"reason": "b"}, headers=h).json()
    assert drec2["detail"]["loans"] == []                                    # 이미 지워진 차입금은 함께 묶지 않음
    r = client.post(f"/api/v1/admin/deletions/{lrec['id']}/restore", headers=h)
    assert r.status_code == 409 and "기숙사를 먼저" in r.json()["detail"]
