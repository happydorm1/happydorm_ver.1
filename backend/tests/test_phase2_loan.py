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


def _get_or_create_admin(email: str, password: str) -> None:
    db = SessionLocal()
    existing = db.query(User).filter(User.email == email).first()
    if existing is None:
        admin = User(
            name="테스트관리자",
            email=email,
            role=UserRole.SYSTEM_ADMIN,
            hashed_password=hash_password(password),
        )
        db.add(admin)
        db.commit()
    db.close()


def _admin_headers():
    _get_or_create_admin("pytest-admin@example.com", "testpass123")
    r = client.post(
        "/api/v1/auth/login",
        data={"username": "pytest-admin@example.com", "password": "testpass123"},
    )
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_loan_creation_and_rate_history_rule():
    headers = _admin_headers()
    run_id = uuid.uuid4().hex[:8]

    # 기준정보 조회 (seed_reference로 사전 시드된 값 사용)
    funds = client.get("/api/v1/reference/funds", headers=headers).json()
    gongja_fund = next(f for f in funds if f["fund_name"] == "공공자금관리기금")

    cycles = client.get("/api/v1/reference/codes", params={"group_code": "REPAY_CYCLE"}, headers=headers).json()
    quarterly = next(c for c in cycles if c["code"] == "QUARTERLY")

    methods = client.get("/api/v1/reference/codes", params={"group_code": "REPAY_METHOD"}, headers=headers).json()
    equal_principal = next(c for c in methods if c["code"] == "EQUAL_PRINCIPAL")

    # SPC/기숙사 선행 생성
    r = client.post("/api/v1/spc", json={"spc_name": f"테스트SPC-{run_id}"}, headers=headers)
    spc_id = r.json()["id"]
    r = client.post(
        "/api/v1/dormitories",
        json={"dormitory_name": f"테스트기숙사-{run_id}", "school_name": "테스트대", "spc_id": spc_id},
        headers=headers,
    )
    dormitory_id = r.json()["id"]

    # 1단계에서 실제 엑셀로 검증한 Test A 시나리오(2015-08-21 공자, 1,768,000,000원)를 그대로 재현
    r = client.post(
        "/api/v1/loans",
        json={
            "dormitory_id": dormitory_id,
            "fund_id": gongja_fund["id"],
            "loan_code": f"2015-08-21_공자-{run_id}",
            "assigned_amount": 1_768_000_000,
            "assigned_date": "2015-08-21",
            "rate_type": "VARIABLE",
            "repay_cycle_code_id": quarterly["id"],
            "repay_method_code_id": equal_principal["id"],
            "principal_repay_start_date": "2022-11-30",
            "repay_period_months": 282,  # 23.5년
            "initial_rate": {
                "effective_start_date": "2015-08-21",
                "effective_end_date": "2022-02-28",
                "annual_rate": "2.24600",
                "rate_table_name": "2015년 3분기 예탁금리",
            },
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    loan_id = r.json()["id"]
    assert len(r.json()["rate_history"]) == 1

    # 두 번째 금리 이력 추가 (실제 검증된 2022-02-28 변경)
    r = client.post(
        f"/api/v1/loans/{loan_id}/rate-history",
        json={
            "effective_start_date": "2022-02-28",
            "effective_end_date": None,
            "annual_rate": "2.32000",
            "rate_table_name": "2022년 1분기 예탁금리",
        },
        headers=headers,
    )
    assert r.status_code == 201

    # 1단계 검증규칙 재현: 회차 종료일이 2021-12-31(변경 전) → 구 금리
    r = client.get(
        f"/api/v1/loans/{loan_id}/applicable-rate",
        params={"as_of_end_date": "2021-12-31"},
        headers=headers,
    )
    assert r.status_code == 200
    assert float(r.json()["annual_rate"]) == 2.246

    # 회차 종료일이 2022-02-28(변경 당일) → 신 금리 (검증된 규칙: 일할안분 없이 해당 회차 전체 신금리)
    r = client.get(
        f"/api/v1/loans/{loan_id}/applicable-rate",
        params={"as_of_end_date": "2022-02-28"},
        headers=headers,
    )
    assert r.status_code == 200
    assert float(r.json()["annual_rate"]) == 2.32

    # 금리 미등록 구간(차입금 시작 이전) 조회 시 422
    r = client.get(
        f"/api/v1/loans/{loan_id}/applicable-rate",
        params={"as_of_end_date": "2010-01-01"},
        headers=headers,
    )
    assert r.status_code == 422

    # 거치일자 역전 입력시 검증 오류(422) 확인
    r = client.post(
        "/api/v1/loans",
        json={
            "dormitory_id": dormitory_id,
            "fund_id": gongja_fund["id"],
            "loan_code": f"invalid-grace-{run_id}",
            "assigned_amount": 1_000_000,
            "assigned_date": "2020-01-01",
            "rate_type": "FIXED",
            "repay_cycle_code_id": quarterly["id"],
            "repay_method_code_id": equal_principal["id"],
            "grace_start_date": "2021-01-01",
            "grace_end_date": "2020-01-01",
            "principal_repay_start_date": "2021-01-01",
            "repay_period_months": 12,
            "initial_rate": {
                "effective_start_date": "2020-01-01",
                "annual_rate": "2.0",
            },
        },
        headers=headers,
    )
    assert r.status_code == 422
