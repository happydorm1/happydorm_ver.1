"""Phase 9: 버전 비교 / 이벤트 이력 / 이벤트 취소 / 변경이력(AuditLog)"""
from _helpers import admin_headers, client, make_loan, make_org, spc_manager_headers


def _events(h, loan_id):
    return client.get(f"/api/v1/loans/{loan_id}/events", headers=h).json()


def test_compare_versions_and_sequential_cancel_restores_original():
    h = admin_headers()
    loan_id, _, _ = make_loan(h)
    assert client.post(f"/api/v1/loans/{loan_id}/events/rate-change",
                       json={"effective_start_date": "2024-08-31", "annual_rate": "3.5"}, headers=h).status_code == 201
    assert client.post(f"/api/v1/loans/{loan_id}/events/early-repayment",
                       json={"event_date": "2024-11-30", "repay_type": "PARTIAL", "repay_amount": 10_000_000},
                       headers=h).status_code == 201

    ev = _events(h, loan_id)
    assert [(e["event_type"], e["status"], e["resulting_version_no"]) for e in ev] == [
        ("RATE_CHANGE", "APPLIED", 2), ("EARLY_REPAYMENT", "APPLIED", 3)]
    assert ev[1]["detail"]["repay_amount"] == 10_000_000

    c12 = client.get(f"/api/v1/loans/{loan_id}/schedule/compare", params={"from_version": 1, "to_version": 2}, headers=h).json()
    assert c12["diff"]["total_principal"] == 0           # 금리변동은 원금 합계를 바꾸지 않는다
    assert c12["diff"]["total_interest"] > 0             # 금리 인상 → 이자 증가
    assert c12["changes"][0]["due_date"] == "2024-08-31"  # 변경일 이전 회차는 차이 없음
    assert all(ch["due_date"] >= "2024-08-31" for ch in c12["changes"])

    c23 = client.get(f"/api/v1/loans/{loan_id}/schedule/compare", params={"from_version": 2, "to_version": 3}, headers=h).json()
    assert c23["diff"]["total_principal"] == 0           # 조기상환도 원금 총계는 보존(시점만 이동)
    assert c23["diff"]["total_interest"] < 0             # 원금이 일찍 줄어 이자 감소

    # 최신이 아닌 이벤트 취소는 거부
    r = client.post(f"/api/v1/loans/{loan_id}/events/{ev[0]['id']}/cancel", json={"reason": "순서위반"}, headers=h)
    assert r.status_code == 422

    # 최신부터 순서대로 취소 → V1과 동일해짐
    r = client.post(f"/api/v1/loans/{loan_id}/events/{ev[1]['id']}/cancel", json={"reason": "입력오류"}, headers=h)
    assert r.status_code == 200 and r.json()["version_no"] == 4
    r = client.post(f"/api/v1/loans/{loan_id}/events/{ev[0]['id']}/cancel", json={"reason": "입력오류"}, headers=h)
    assert r.status_code == 200 and r.json()["version_no"] == 5 and r.json()["is_current"] is True
    r = client.post(f"/api/v1/loans/{loan_id}/events/{ev[0]['id']}/cancel", json={"reason": "중복"}, headers=h)
    assert r.status_code == 422

    c15 = client.get(f"/api/v1/loans/{loan_id}/schedule/compare", params={"from_version": 1, "to_version": 5}, headers=h).json()
    assert c15["diff"] == {"total_principal": 0, "total_interest": 0, "changed_due_dates": 0}

    # 삭제 없이 이력 보존: 모든 버전/이벤트가 남고, 취소된 금리이력은 취소 표시
    versions = client.get(f"/api/v1/loans/{loan_id}/schedule/versions", headers=h).json()
    assert [v["version_no"] for v in versions] == [1, 2, 3, 4, 5]
    assert [v["is_current"] for v in versions] == [False, False, False, False, True]
    assert {e["status"] for e in _events(h, loan_id)} == {"CANCELLED"}
    loan = client.get(f"/api/v1/loans/{loan_id}", headers=h).json()
    cancelled = [x for x in loan["rate_history"] if x["is_deleted"]]
    active = [x for x in loan["rate_history"] if not x["is_deleted"]]
    assert len(cancelled) == 1 and float(cancelled[0]["annual_rate"]) == 3.5
    assert len(active) == 1 and active[0]["effective_end_date"] is None  # 3.0% 구간 복원


def test_deferral_compare_counts_recovery_principal_and_cancel():
    h = admin_headers()
    loan_id, _, _ = make_loan(h)
    r = client.post(f"/api/v1/loans/{loan_id}/events/deferral", json={
        "event_date": "2024-02-29", "defer_target": "PRINCIPAL_INTEREST",
        "recovery_cycle_months": 3, "recovery_count": 4}, headers=h)
    assert r.status_code == 201, r.text
    cmp = client.get(f"/api/v1/loans/{loan_id}/schedule/compare", params={"from_version": 1, "to_version": 2}, headers=h).json()
    # 동결된 원금(500만원)은 회수회차로 옮겨질 뿐 총원금은 그대로
    assert cmp["diff"]["total_principal"] == 0
    frozen = next(c for c in cmp["changes"] if c["due_date"] == "2024-02-29")
    assert frozen["change_type"] == "REMOVED" and frozen["to"]["total"] == 0
    assert any(c["delta_principal"] > 0 for c in cmp["changes"])  # 회수 회차의 원금 증가

    ev = _events(h, loan_id)[0]
    assert ev["event_type"] == "DEFERRAL" and ev["detail"]["recovery_count"] == 4
    assert client.post(f"/api/v1/loans/{loan_id}/events/{ev['id']}/cancel", json={"reason": "유예 철회"}, headers=h).status_code == 200
    back = client.get(f"/api/v1/loans/{loan_id}/schedule/compare", params={"from_version": 1, "to_version": 3}, headers=h).json()
    assert back["diff"]["changed_due_dates"] == 0


def test_full_refinancing_cancel_restores_fund_and_rate():
    h = admin_headers()
    loan_id, _, funds = make_loan(h)
    r = client.post(f"/api/v1/loans/{loan_id}/events/refinancing", json={
        "refi_type": "FULL", "event_date": "2024-05-31", "new_fund_id": funds["주택도시기금"]["id"],
        "new_annual_rate": "2.5"}, headers=h)
    assert r.status_code == 201, r.text
    assert client.get(f"/api/v1/loans/{loan_id}", headers=h).json()["fund_id"] == funds["주택도시기금"]["id"]

    ev = _events(h, loan_id)[0]
    r = client.post(f"/api/v1/loans/{loan_id}/events/{ev['id']}/cancel", json={"reason": "대환 철회"}, headers=h)
    assert r.status_code == 200, r.text
    loan = client.get(f"/api/v1/loans/{loan_id}", headers=h).json()
    assert loan["fund_id"] == funds["공공자금관리기금"]["id"]
    assert [x["is_deleted"] for x in loan["rate_history"]] == [False, True]
    cmp = client.get(f"/api/v1/loans/{loan_id}/schedule/compare", params={"from_version": 1, "to_version": 3}, headers=h).json()
    assert cmp["diff"]["changed_due_dates"] == 0


def test_loan_update_is_audited_and_contract_terms_protected():
    h = admin_headers()
    loan_id, _, _ = make_loan(h, generate=False)
    # 원리금 테이블이 없을 때는 수정 가능 + 변경 전/후/사유 기록
    r = client.put(f"/api/v1/loans/{loan_id}", json={"assigned_amount": 120_000_000, "change_reason": "배정금액 정정"}, headers=h)
    assert r.status_code == 200 and r.json()["assigned_amount"] == 120_000_000
    logs = client.get(f"/api/v1/loans/{loan_id}/audit-logs", params={"table_name": "loan"}, headers=h).json()
    upd = next(x for x in logs if x["action"] == "UPDATE")
    assert (upd["changed_field"], upd["before_value"], upd["after_value"], upd["change_reason"]) == (
        "assigned_amount", "100000000", "120000000", "배정금액 정정")
    assert any(x["action"] == "CREATE" for x in logs)  # 등록 이력은 자동 기록

    # 원리금 테이블이 생기면 계약조건 직접 수정은 거부(이벤트로만 변경)
    assert client.post(f"/api/v1/loans/{loan_id}/schedule/generate", headers=h).status_code == 201
    r = client.put(f"/api/v1/loans/{loan_id}", json={"assigned_amount": 90_000_000}, headers=h)
    assert r.status_code == 422
    assert client.get(f"/api/v1/loans/{loan_id}", headers=h).json()["assigned_amount"] == 120_000_000
    # 상태 변경 같은 비계약 항목은 허용
    assert client.put(f"/api/v1/loans/{loan_id}", json={"status": "CLOSED"}, headers=h).status_code == 200


def test_history_endpoints_respect_roles():
    h = admin_headers()
    spc_a, dorm_a = make_org(h)
    spc_b, dorm_b = make_org(h)
    loan_a, _, _ = make_loan(h, dorm=dorm_a)
    loan_b, _, _ = make_loan(h, dorm=dorm_b)
    spc_h = spc_manager_headers(h, spc_a)

    assert client.get(f"/api/v1/loans/{loan_a}/events", headers=spc_h).status_code == 200       # 자기 SPC: 이력 조회 가능
    assert client.get(f"/api/v1/loans/{loan_b}/events", headers=spc_h).status_code == 403       # 타 SPC 차단
    assert client.get(f"/api/v1/loans/{loan_a}/audit-logs", headers=spc_h).status_code == 403    # 변경이력은 재단 담당자만
    assert client.get(f"/api/v1/loans/{loan_a}/schedule/compare", params={"from_version": 1, "to_version": 1},
                      headers=spc_h).status_code == 200
    assert client.get(f"/api/v1/loans/{loan_b}/schedule/compare", params={"from_version": 1, "to_version": 1},
                      headers=spc_h).status_code == 403
    assert client.get(f"/api/v1/loans/{loan_a}/schedule/compare", params={"from_version": 1, "to_version": 9},
                      headers=h).status_code == 404
