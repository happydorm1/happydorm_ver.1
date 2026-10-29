"""Phase 9~ 테스트 공용 헬퍼."""
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


def admin_headers():
    db = SessionLocal()
    email = "pytest-admin@example.com"
    if not db.query(User).filter(User.email == email).first():
        db.add(User(name="테스트관리자", email=email, role=UserRole.SYSTEM_ADMIN, hashed_password=hash_password("testpass123")))
        db.commit()
    db.close()
    r = client.post("/api/v1/auth/login", data={"username": email, "password": "testpass123"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def refs(h):
    funds = {f["fund_name"]: f for f in client.get("/api/v1/reference/funds", headers=h).json()}
    cyc = {c["code"]: c for c in client.get("/api/v1/reference/codes", params={"group_code": "REPAY_CYCLE"}, headers=h).json()}
    mth = {c["code"]: c for c in client.get("/api/v1/reference/codes", params={"group_code": "REPAY_METHOD"}, headers=h).json()}
    return funds, cyc, mth


def make_org(h):
    run = uuid.uuid4().hex[:8]
    spc = client.post("/api/v1/spc", json={"spc_name": f"S-{run}"}, headers=h).json()["id"]
    dorm = client.post("/api/v1/dormitories", json={"dormitory_name": f"D-{run}", "school_name": "t", "spc_id": spc}, headers=h).json()["id"]
    return spc, dorm


def make_loan(h, *, dorm=None, fund="공공자금관리기금", cycle="QUARTERLY", method="EQUAL_PRINCIPAL",
              start="2023-02-28", first="2023-05-31", months=60, amount=100_000_000, rate="3.0", generate=True):
    funds, cyc, mth = refs(h)
    if dorm is None:
        _, dorm = make_org(h)
    run = uuid.uuid4().hex[:8]
    r = client.post("/api/v1/loans", json={
        "dormitory_id": dorm, "fund_id": funds[fund]["id"], "loan_code": f"T-{run}", "assigned_amount": amount,
        "assigned_date": start, "rate_type": "VARIABLE",
        "repay_cycle_code_id": cyc[cycle]["id"], "repay_method_code_id": mth[method]["id"],
        "principal_repay_start_date": first, "repay_period_months": months,
        "initial_rate": {"effective_start_date": start, "annual_rate": rate}}, headers=h)
    assert r.status_code == 201, r.text
    loan_id = r.json()["id"]
    if generate:
        assert client.post(f"/api/v1/loans/{loan_id}/schedule/generate", headers=h).status_code == 201
    return loan_id, dorm, funds


def spc_manager_headers(h, spc_id):
    email = f"spc-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/api/v1/users", json={"name": "SPC담당", "email": email, "role": "SPC_MANAGER",
                                            "spc_id": spc_id, "password": "spcpass123"}, headers=h)
    assert r.status_code == 201, r.text
    r = client.post("/api/v1/auth/login", data={"username": email, "password": "spcpass123"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}
