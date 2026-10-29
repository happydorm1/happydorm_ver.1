"""업무기준 확정(2026-10-28): 중도변수 미처리 판정·알림 팝업, 관리자 공휴일 연동 재안내"""
from datetime import date, datetime, timedelta, timezone

from _helpers import admin_headers, client, make_loan, make_org, spc_manager_headers

from app.core.config import settings
from app.core.database import SessionLocal
from app.domain.popup_service import popup_items
from app.models.notification import PopupAck
from app.models.organization import User


def _midterm(h, dorm, as_of):
    r = client.get("/api/v1/dashboard", params={"as_of": as_of, "dormitory_id": dorm}, headers=h)
    assert r.status_code == 200, r.text
    na = r.json()["alerts"]["needs_attention"]
    assert na["not_covered"] == []
    return na["midterm_pending"]


def _mine(items, loan_id):
    return [i for i in items if i.get("loan_id") == loan_id or str(i.get("loan_id")) == loan_id]


def test_rate_registered_but_not_applied_until_rate_change_event():
    h = admin_headers()
    loan, dorm, _ = make_loan(h)                                            # 변동, 원금균등, 3.0%(종료일 없음)
    assert _midterm(h, dorm, "2025-06-01") == []
    # 담당자가 새 금리를 '이력'으로만 등록 → 원리금 테이블은 아직 3.0%
    assert client.post(f"/api/v1/loans/{loan}/rate-history", json={"effective_start_date": "2025-08-31", "annual_rate": "3.5"},
                       headers=h).status_code == 201
    m = _midterm(h, dorm, "2025-06-01")
    assert [x["kind"] for x in m] == ["RATE_NOT_APPLIED"] and "3.5%" in m[0]["detail"]
    # 금리변동 이벤트 적용 → 해소(같은 시작일 이력은 중복되지 않게 대체)
    assert client.post(f"/api/v1/loans/{loan}/events/rate-change", json={"effective_start_date": "2025-08-31", "annual_rate": "3.5"},
                       headers=h).status_code == 201
    assert _midterm(h, dorm, "2025-06-01") == []
    hist = [x for x in client.get(f"/api/v1/loans/{loan}", headers=h).json().get("rate_history", [])
            if x["effective_start_date"] == "2025-08-31"]
    assert len([x for x in hist if not x["is_deleted"]]) == 1 and len(hist) == 2     # 먼저 넣은 이력은 사용중지(삭제 아님)


def test_rate_period_ended_without_new_rate_and_overpaid():
    h = admin_headers()
    _, dorm = make_org(h)
    r = client.post("/api/v1/loans", json={**_loan_body(h, dorm), "initial_rate": {
        "effective_start_date": "2023-02-28", "effective_end_date": "2024-01-31", "annual_rate": "3.0"}}, headers=h)
    loan = r.json()["id"]
    assert client.post(f"/api/v1/loans/{loan}/schedule/generate", headers=h).status_code == 201
    assert [x["kind"] for x in _midterm(h, dorm, "2024-01-15")] == []        # 변경일 전 → 금리변동 예정(D-30)일 뿐
    m = _midterm(h, dorm, "2024-03-01")
    assert [x["kind"] for x in m] == ["RATE_NOT_REGISTERED"] and "2024-01-31" in m[0]["detail"]
    assert client.post(f"/api/v1/loans/{loan}/events/rate-change", json={"effective_start_date": "2024-01-31", "annual_rate": "3.2"},
                       headers=h).status_code == 201
    assert _midterm(h, dorm, "2024-03-01") == []

    # 예정액보다 더 낸 회차 → 조기상환 미등록 의심
    row = client.get(f"/api/v1/loans/{loan}/payments/summary", params={"as_of": "2024-03-01"}, headers=h).json()["rows"][0]
    assert client.post(f"/api/v1/loans/{loan}/payments", json={
        "due_date": row["due_date"], "actual_paid_date": row["payment_deadline"],
        "actual_principal": row["scheduled_principal"] + 30_000_000, "actual_interest": row["scheduled_interest"]}, headers=h).status_code == 201
    m = _midterm(h, dorm, "2024-03-01")
    assert [x["kind"] for x in m] == ["OVERPAID"] and m[0]["amount"] == 30_000_000


def _loan_body(h, dorm):
    from _helpers import refs
    import uuid
    funds, cyc, mth = refs(h)
    return {"dormitory_id": dorm, "fund_id": funds["공공자금관리기금"]["id"], "loan_code": f"MP-{uuid.uuid4().hex[:8]}",
            "assigned_amount": 100_000_000, "assigned_date": "2023-02-28", "rate_type": "VARIABLE",
            "repay_cycle_code_id": cyc["QUARTERLY"]["id"], "repay_method_code_id": mth["EQUAL_PRINCIPAL"]["id"],
            "principal_repay_start_date": "2023-05-31", "repay_period_months": 60}


def test_popup_roles_ack_and_reminders(monkeypatch):
    h = admin_headers()
    spc, dorm = make_org(h)
    loan, _, _ = make_loan(h, dorm=dorm)
    client.post(f"/api/v1/loans/{loan}/rate-history", json={"effective_start_date": "2027-08-31", "annual_rate": "3.9"}, headers=h)
    monkeypatch.setattr(settings, "KASI_SERVICE_KEY", None)
    db = SessionLocal()                                                      # 이전 실행의 '확인' 기록 정리(테스트 반복 실행용)
    db.query(PopupAck).filter(PopupAck.user_id == db.query(User.id).filter(User.email == "pytest-admin@example.com").scalar_subquery()).delete(synchronize_session=False)
    db.commit()
    db.close()

    p = client.get("/api/v1/popup", params={"as_of": "2026-10-28"}, headers=h).json()
    mine = _mine(p["items"], loan)
    assert [x["kind"] for x in mine] == ["RATE_NOT_APPLIED"] and mine[0]["group"] == "MIDTERM"
    sys_items = [x for x in p["items"] if x["group"] == "SYSTEM"]
    assert any(x["kind"] == "HOLIDAY_KEY_MISSING" for x in sys_items)        # 관리자에게 공휴일 연동 미설정 안내

    # SPC 담당자: 중도변수 처리 주체가 아니므로 팝업 없음 / 자금담당: 중도변수만(시스템 안내 없음)
    assert client.get("/api/v1/popup", params={"as_of": "2026-10-28"}, headers=spc_manager_headers(h, spc)).json()["items"] == []

    # 확인 → 숨김. 중도변수는 다음 날, 시스템 안내는 7일 뒤 미해결이면 다시
    keys = [mine[0]["key"]] + [x["key"] for x in sys_items]
    r = client.post("/api/v1/popup/ack", json={"keys": keys}, headers=h)
    assert r.status_code == 200 and len(r.json()["acknowledged"]) == len(keys)
    p2 = client.get("/api/v1/popup", params={"as_of": "2026-10-28"}, headers=h).json()
    assert _mine(p2["items"], loan) == [] and not [x for x in p2["items"] if x["group"] == "SYSTEM"]
    assert p2["hidden_count"] >= len(keys)

    db = SessionLocal()
    admin = db.query(User).filter(User.email == "pytest-admin@example.com").one()
    later = datetime.now(timezone.utc) + timedelta(days=2)
    again = popup_items(db, admin, date(2026, 10, 28), now=later)["items"]
    assert _mine(again, loan) and not [x for x in again if x["group"] == "SYSTEM"]   # 중도변수는 다시, 시스템은 아직 숨김
    again = popup_items(db, admin, date(2026, 10, 28), now=datetime.now(timezone.utc) + timedelta(days=8))["items"]
    assert any(x["kind"] == "HOLIDAY_KEY_MISSING" for x in again)          # 7일 지나도 미해결 → 재안내
    monkeypatch.setattr(settings, "KASI_SERVICE_KEY", "SET")
    assert not any(x["kind"] == "HOLIDAY_KEY_MISSING" for x in popup_items(db, admin, date(2026, 10, 28))["items"])  # 해결되면 사라짐
    db.close()

    assert client.post("/api/v1/popup/ack", json={"keys": []}, headers=h).status_code == 422
    assert client.get("/api/v1/popup").status_code == 401
