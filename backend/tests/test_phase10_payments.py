"""Phase 10: 실제 납부관리 — 예정 vs 실제 비교, 미납/일부납부/초과납부, 취소, 권한"""
from datetime import date

from _helpers import admin_headers, client, make_loan, make_org, spc_manager_headers


def _summary(h, loan_id, as_of):
    r = client.get(f"/api/v1/loans/{loan_id}/payments/summary", params={"as_of": as_of}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _row(summary, due):
    return next(x for x in summary["rows"] if x["due_date"] == due)


def _pay(h, loan_id, due, principal, interest, paid="2024-06-10", note=None):
    return client.post(f"/api/v1/loans/{loan_id}/payments", json={
        "due_date": due, "actual_paid_date": paid, "actual_principal": principal,
        "actual_interest": interest, "note": note}, headers=h)


def test_scheduled_amounts_and_payment_statuses():
    h = admin_headers()
    loan_id, _, _ = make_loan(h)   # 1억원, 분기, 원금균등 20회(회당 500만원), 연 3%
    s = _summary(h, loan_id, "2023-07-01")

    first = _row(s, "2023-05-31")
    expected_interest = round(100_000_000 * (date(2023, 5, 31) - date(2023, 2, 28)).days * 3.0 / 100 / 365)
    assert first["scheduled_principal"] == 5_000_000
    assert first["scheduled_interest"] == expected_interest            # 직접 계산한 값과 일치
    assert first["scheduled_total"] == 5_000_000 + expected_interest
    assert first["status"] == "UNPAID" and first["is_overdue"] is True  # 기한 경과 + 납부 없음
    assert _row(s, "2023-08-31")["status"] == "PENDING"                 # 아직 기한 전
    assert s["totals"]["overdue_count"] == 1 and s["totals"]["overdue_amount"] == first["scheduled_total"]

    # 일부납부 → PARTIAL(부족액 표시)
    r = _pay(h, loan_id, "2023-05-31", 5_000_000, 0, paid="2023-06-05")
    assert r.status_code == 201, r.text
    assert r.json()["actual_total"] == 5_000_000
    row = _row(_summary(h, loan_id, "2023-07-01"), "2023-05-31")
    assert row["status"] == "PARTIAL" and row["outstanding"] == expected_interest
    assert row["difference"] == -expected_interest and row["is_overdue"] is True

    # 나머지를 추가 납부(분할납부) → PAID, 2건 누적, 지연일수 기록
    assert _pay(h, loan_id, "2023-05-31", 0, expected_interest, paid="2023-06-09").status_code == 201
    row = _row(_summary(h, loan_id, "2023-07-01"), "2023-05-31")
    assert row["status"] == "PAID" and row["payment_count"] == 2 and row["outstanding"] == 0
    assert row["is_overdue"] is False and row["days_after_due"] == 9    # 6/9 - 5/31

    # 초과납부 → OVERPAID
    assert _pay(h, loan_id, "2023-08-31", 6_000_000, 2_000_000, paid="2023-08-31").status_code == 201
    over = _row(_summary(h, loan_id, "2024-01-01"), "2023-08-31")
    assert over["status"] == "OVERPAID" and over["difference"] > 0

    s = _summary(h, loan_id, "2023-09-30")
    assert s["totals"]["collection_rate"] is not None and 0 < s["totals"]["collection_rate"] <= 1


def test_cancel_payment_keeps_history_and_reverts_status():
    h = admin_headers()
    loan_id, _, _ = make_loan(h)
    r = _pay(h, loan_id, "2023-05-31", 5_000_000, 756_164, paid="2023-05-31")
    assert r.status_code == 201
    pid = r.json()["id"]
    assert _row(_summary(h, loan_id, "2023-07-01"), "2023-05-31")["status"] == "PAID"

    assert client.post(f"/api/v1/loans/{loan_id}/payments/{pid}/cancel", json={"reason": "금액 오입력"}, headers=h).status_code == 200
    assert _row(_summary(h, loan_id, "2023-07-01"), "2023-05-31")["status"] == "UNPAID"   # 합계에서 제외
    assert client.post(f"/api/v1/loans/{loan_id}/payments/{pid}/cancel", json={"reason": "중복"}, headers=h).status_code == 422

    # 삭제가 아니라 취소 이력으로 남는다
    assert client.get(f"/api/v1/loans/{loan_id}/payments", headers=h).json() == []
    allp = client.get(f"/api/v1/loans/{loan_id}/payments", params={"include_cancelled": True}, headers=h).json()
    assert len(allp) == 1 and allp[0]["status"] == "CANCELLED" and allp[0]["cancel_reason"] == "금액 오입력"

    logs = client.get(f"/api/v1/loans/{loan_id}/audit-logs", params={"table_name": "actual_payment"}, headers=h).json()
    assert {x["action"] for x in logs} == {"CREATE", "CANCEL"}   # 실제납부액 변경이력(§42)


def test_payment_input_validation():
    h = admin_headers()
    loan_id, _, _ = make_loan(h)
    assert _pay(h, loan_id, "2023-05-30", 1_000, 0).status_code == 422                  # 예정에 없는 상환일
    assert _pay(h, loan_id, "2023-05-31", 0, 0).status_code == 422                      # 0원
    assert _pay(h, loan_id, "2023-05-31", 1_000, 0, paid="2999-01-01").status_code == 422  # 미래 납부일
    assert client.post(f"/api/v1/loans/{loan_id}/payments", json={
        "due_date": "2023-05-31", "actual_paid_date": "2023-06-01", "actual_principal": -5}, headers=h).status_code == 422
    # 원리금 테이블이 없는 차입금
    no_sched, _, _ = make_loan(h, generate=False)
    assert _pay(h, no_sched, "2023-05-31", 1_000, 0).status_code == 422


def test_payments_survive_version_changes_and_flag_unmatched():
    h = admin_headers()
    loan_id, _, _ = make_loan(h)
    # 조기상환(일부) 이벤트는 2024-04-15에 별도 정산 행(원금 1천만원 + 경과이자)을 만든다
    assert client.post(f"/api/v1/loans/{loan_id}/events/early-repayment", json={
        "event_date": "2024-04-15", "repay_type": "PARTIAL", "repay_amount": 10_000_000}, headers=h).status_code == 201
    s = _summary(h, loan_id, "2024-05-01")
    ev_row = _row(s, "2024-04-15")
    assert ev_row["scheduled_principal"] == 10_000_000
    assert _pay(h, loan_id, "2024-04-15", ev_row["scheduled_principal"], ev_row["scheduled_interest"], paid="2024-04-15").status_code == 201
    assert _row(_summary(h, loan_id, "2024-05-01"), "2024-04-15")["status"] == "PAID"

    # 이벤트를 취소하면 그 상환일은 현재 일정에서 사라진다 → 납부이력은 삭제되지 않고 '불일치 납부'로 표시
    evs = client.get(f"/api/v1/loans/{loan_id}/events", headers=h).json()
    assert client.post(f"/api/v1/loans/{loan_id}/events/{evs[-1]['id']}/cancel", json={"reason": "철회"}, headers=h).status_code == 200
    s = _summary(h, loan_id, "2024-05-01")
    assert [u["due_date"] for u in s["unmatched_payments"]] == ["2024-04-15"]
    assert len(client.get(f"/api/v1/loans/{loan_id}/payments", headers=h).json()) == 1


def test_deferral_recovery_principal_is_included_in_scheduled_amount():
    h = admin_headers()
    loan_id, _, _ = make_loan(h)
    assert client.post(f"/api/v1/loans/{loan_id}/events/deferral", json={
        "event_date": "2024-02-29", "defer_target": "PRINCIPAL_INTEREST",
        "recovery_cycle_months": 3, "recovery_count": 4}, headers=h).status_code == 201
    s = _summary(h, loan_id, "2026-01-01")
    assert "2024-02-29" not in [r["due_date"] for r in s["rows"]]   # 동결된 상환일은 예정액 0원이라 납부 대상에서 빠진다
    assert _pay(h, loan_id, "2024-02-29", 1_000, 0).status_code == 422
    # 회수 첫 회차(동결일 + 1년): 정규 원금 500만원 + 회수 원금(500만원 ÷ 4)이 함께 청구된다
    rec = _row(s, "2025-02-28")
    assert rec["scheduled_principal"] == 5_000_000 + 1_250_000
    # 총 예정 원금은 배정금액과 같다(동결분이 사라지지 않고 회수분으로 이동)
    assert sum(r["scheduled_principal"] for r in s["rows"]) == 100_000_000


def test_overdue_list_and_permissions():
    h = admin_headers()
    spc_a, dorm_a = make_org(h)
    spc_b, dorm_b = make_org(h)
    loan_a, _, _ = make_loan(h, dorm=dorm_a)
    loan_b, _, _ = make_loan(h, dorm=dorm_b)
    spc_h = spc_manager_headers(h, spc_a)

    # 관리자: 전체 미납 목록에 두 차입금이 모두 나온다
    allr = client.get("/api/v1/payments/overdue", params={"as_of": "2023-07-01"}, headers=h).json()
    codes = {i["loan_id"] for i in allr["items"]}
    assert {loan_a, loan_b} <= codes and allr["overdue_count"] == len(allr["items"])

    # SPC 담당자: 자기 SPC 차입금만
    mine = client.get("/api/v1/payments/overdue", params={"as_of": "2023-07-01"}, headers=spc_h).json()
    assert {i["loan_id"] for i in mine["items"]} == {loan_a}

    # 납부 입력은 재단 담당자만, 타 SPC 조회는 차단
    assert _pay(spc_h, loan_a, "2023-05-31", 5_000_000, 756_164, paid="2023-05-31").status_code == 403
    assert client.get(f"/api/v1/loans/{loan_b}/payments/summary", headers=spc_h).status_code == 403
    assert client.get(f"/api/v1/loans/{loan_a}/payments/summary", params={"as_of": "2023-07-01"}, headers=spc_h).status_code == 200
