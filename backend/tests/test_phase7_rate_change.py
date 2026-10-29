import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.main import app  # noqa: E402
from app.models.organization import User, UserRole  # noqa: E402

client = TestClient(app)


def _admin_headers():
    db = SessionLocal()
    email = "pytest-admin@example.com"
    if not db.query(User).filter(User.email == email).first():
        db.add(User(name="테스트관리자", email=email, role=UserRole.SYSTEM_ADMIN,
                     hashed_password=hash_password("testpass123")))
        db.commit()
    db.close()
    r = client.post("/api/v1/auth/login", data={"username": email, "password": "testpass123"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _setup_loan(headers, run_id, **overrides):
    r = client.post("/api/v1/spc", json={"spc_name": f"SPC-{run_id}"}, headers=headers)
    spc_id = r.json()["id"]
    r = client.post(
        "/api/v1/dormitories",
        json={"dormitory_name": f"기숙사-{run_id}", "school_name": "테스트대", "spc_id": spc_id},
        headers=headers,
    )
    dorm_id = r.json()["id"]

    funds = client.get("/api/v1/reference/funds", headers=headers).json()
    fund = next(f for f in funds if f["fund_name"] == "공공자금관리기금")
    cycles = client.get("/api/v1/reference/codes", params={"group_code": "REPAY_CYCLE"}, headers=headers).json()
    quarterly = next(c for c in cycles if c["code"] == "QUARTERLY")
    methods = client.get("/api/v1/reference/codes", params={"group_code": "REPAY_METHOD"}, headers=headers).json()
    equal_principal = next(c for c in methods if c["code"] == "EQUAL_PRINCIPAL")

    payload = {
        "dormitory_id": dorm_id, "fund_id": fund["id"], "loan_code": f"RC-{run_id}",
        "assigned_amount": 100_000_000, "assigned_date": "2023-02-28", "rate_type": "VARIABLE",
        "repay_cycle_code_id": quarterly["id"], "repay_method_code_id": equal_principal["id"],
        "principal_repay_start_date": "2023-05-31", "repay_period_months": 60,
        "initial_rate": {"effective_start_date": "2023-02-28", "annual_rate": "3.00000"},
    }
    payload.update(overrides)
    r = client.post("/api/v1/loans", json=payload, headers=headers)
    assert r.status_code == 201, r.text
    loan_id = r.json()["id"]
    r = client.post(f"/api/v1/loans/{loan_id}/schedule/generate", headers=headers)
    assert r.status_code == 201, r.text
    return loan_id, r.json()


def test_rate_change_only_affects_future_installments():
    headers = _admin_headers()
    run_id = uuid.uuid4().hex[:8]
    loan_id, v1 = _setup_loan(headers, run_id)

    r = client.post(
        f"/api/v1/loans/{loan_id}/events/rate-change",
        json={"effective_start_date": "2024-08-31", "annual_rate": "3.50000", "rate_table_name": "변경테스트"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    v2 = r.json()

    assert v2["status"] == "NORMAL"
    assert v2["validation_errors"] is None
    assert sum(row["principal_amount"] for row in v2["rows"]) == 100_000_000
    assert v2["rows"][-1]["balance_after"] == 0

    # 변경일 이전 회차는 완전히 동일(불변)
    for a, b in zip(v1["rows"][:5], v2["rows"][:5]):
        assert a["principal_amount"] == b["principal_amount"]
        assert a["interest_amount"] == b["interest_amount"]
        assert a["applied_rate"] == b["applied_rate"] == 3.0

    # 변경일 이후 회차는 신금리(3.5%) 적용, 수동계산과 일치
    changed = [row for row in v2["rows"] if row["due_date"] >= "2024-08-31" and row["installment_no"] == 6]
    assert len(changed) == 1
    row6 = changed[0]
    assert row6["applied_rate"] == 3.5
    expected_interest = round(75_000_000 * 3.5 / 100 * 92 / 365)  # 잔액 7500만, 92일(5/31~8/31)
    assert row6["interest_amount"] == expected_interest

    versions = client.get(f"/api/v1/loans/{loan_id}/schedule/versions", headers=headers).json()
    assert [v["version_no"] for v in versions] == [1, 2]
    assert versions[1]["generated_reason"] == "금리변동"


def test_rate_change_during_grace_period():
    headers = _admin_headers()
    run_id = uuid.uuid4().hex[:8]
    loan_id, v1 = _setup_loan(
        headers, run_id,
        grace_start_date="2023-02-28", grace_end_date="2025-02-28",
        principal_repay_start_date="2025-05-31",
    )

    r = client.post(
        f"/api/v1/loans/{loan_id}/events/rate-change",
        json={"effective_start_date": "2024-02-28", "annual_rate": "3.20000"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    v2 = r.json()
    assert v2["status"] == "NORMAL"
    assert sum(row["principal_amount"] for row in v2["rows"]) == 100_000_000
    assert v2["rows"][-1]["balance_after"] == 0


def test_rate_change_rejected_for_equal_payment_method():
    """주택도시기금(PPMT/IPMT, 고정금리) 방식은 금리변동 재계산 대상이 아님을 확인."""
    headers = _admin_headers()
    run_id = uuid.uuid4().hex[:8]

    r = client.post("/api/v1/spc", json={"spc_name": f"SPC-{run_id}"}, headers=headers)
    spc_id = r.json()["id"]
    r = client.post(
        "/api/v1/dormitories",
        json={"dormitory_name": f"기숙사-{run_id}", "school_name": "테스트대", "spc_id": spc_id},
        headers=headers,
    )
    dorm_id = r.json()["id"]

    funds = client.get("/api/v1/reference/funds", headers=headers).json()
    fund = next(f for f in funds if f["fund_name"] == "주택도시기금")
    cycles = client.get("/api/v1/reference/codes", params={"group_code": "REPAY_CYCLE"}, headers=headers).json()
    semiannual = next(c for c in cycles if c["code"] == "SEMIANNUAL")
    methods = client.get("/api/v1/reference/codes", params={"group_code": "REPAY_METHOD"}, headers=headers).json()
    equal_payment = next(c for c in methods if c["code"] == "EQUAL_PAYMENT")

    r = client.post(
        "/api/v1/loans",
        json={
            "dormitory_id": dorm_id, "fund_id": fund["id"], "loan_code": f"RC-PPMT-{run_id}",
            "assigned_amount": 100_000_000, "assigned_date": "2023-02-28", "rate_type": "FIXED",
            "repay_cycle_code_id": semiannual["id"], "repay_method_code_id": equal_payment["id"],
            "principal_repay_start_date": "2023-08-31", "repay_period_months": 60,
            "initial_rate": {"effective_start_date": "2023-02-28", "annual_rate": "2.00000"},
        },
        headers=headers,
    )
    loan_id = r.json()["id"]
    client.post(f"/api/v1/loans/{loan_id}/schedule/generate", headers=headers)

    r = client.post(
        f"/api/v1/loans/{loan_id}/events/rate-change",
        json={"effective_start_date": "2024-08-31", "annual_rate": "2.50000"},
        headers=headers,
    )
    assert r.status_code == 422
