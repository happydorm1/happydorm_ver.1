"""
Phase 6 (상환유예) End-to-End 테스트.
2단계에서 첨부3(실제 상환유예 사례)으로 역산·확정한 알고리즘을 실제 API+DB로 재현한다.
대상: 2014-12-18 주택도시기금, 5,517,000,000원, 6회차(2020-08-31) 원금+이자 동결,
      2021-08-31부터 분기(3개월) 10회 회수.
"""
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


def test_deferral_matches_real_attachment_3_case():
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
            "dormitory_id": dorm_id, "fund_id": fund["id"], "loan_code": f"DEFER-{run_id}",
            "assigned_amount": 5_517_000_000, "assigned_date": "2014-12-18", "rate_type": "FIXED",
            "repay_cycle_code_id": semiannual["id"], "repay_method_code_id": equal_payment["id"],
            "grace_start_date": "2014-12-18", "grace_end_date": "2017-08-31",
            "principal_repay_start_date": "2018-02-28", "repay_period_months": 324,
            "initial_rate": {"effective_start_date": "2014-12-18", "annual_rate": "2.00000"},
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    loan_id = r.json()["id"]

    r = client.post(f"/api/v1/loans/{loan_id}/schedule/generate", headers=headers)
    assert r.status_code == 201, r.text
    v1_rows = r.json()["rows"]

    # 동결 전 6회차 금액이 실제 엑셀과 일치하는지 먼저 확인
    row6 = next(rr for rr in v1_rows if rr["installment_no"] == 6)
    assert row6["principal_amount"] == 81_506_004
    assert row6["interest_amount"] == 51_214_162

    r = client.post(
        f"/api/v1/loans/{loan_id}/events/deferral",
        json={
            "event_date": "2020-08-31",
            "defer_target": "PRINCIPAL_INTEREST",
            "recovery_cycle_months": 3,
            "recovery_count": 10,
            "reason": "pytest 재현",
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    result = r.json()
    deferral = result["event"]["deferral"]

    assert deferral["frozen_principal_total"] == 81_506_004
    assert deferral["frozen_interest_total"] == 51_214_162
    assert deferral["recovery_start_date"] == "2021-08-31"

    expected_principal = [8_150_600] * 9 + [8_150_604]
    expected_interest = [5_121_416] * 9 + [5_121_418]
    expected_additional_interest = [82176, 40641, 40195, 41088, 41088, 40641, 40195, 41088, 41088, 40641]

    rows = deferral["recovery_rows"]
    assert len(rows) == 10
    for i, rr in enumerate(rows):
        assert rr["recovery_principal"] == expected_principal[i]
        assert rr["recovery_interest"] == expected_interest[i]
        assert rr["additional_interest_on_frozen_principal"] == expected_additional_interest[i]

    assert sum(rr["recovery_principal"] for rr in rows) == 81_506_004
    assert sum(rr["recovery_interest"] for rr in rows) == 51_214_162

    version = result["resulting_schedule"]
    assert version["status"] == "NORMAL"
    assert version["validation_errors"] is None
    assert version["rows"][-1]["balance_after"] == 0

    # 동결 회차는 0원, 이후 정규 회차는 영향받지 않음(원 스케줄과 동일)
    frozen = next(rr for rr in version["rows"] if rr["installment_no"] == 6)
    assert frozen["principal_amount"] == 0
    assert frozen["interest_amount"] == 0

    # 이전 버전(V1)은 삭제되지 않고 그대로 보존
    versions = client.get(f"/api/v1/loans/{loan_id}/schedule/versions", headers=headers).json()
    assert len(versions) == 2
    assert versions[0]["version_no"] == 1
    assert versions[1]["version_no"] == 2
    assert versions[1]["generated_reason"] == "상환유예"
