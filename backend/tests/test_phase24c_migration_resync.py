"""
이관 납부기록 다시 맞추기 — '기준일 이전 납부완료'로 이관한 뒤 기준일 이전의 상환유예를 이벤트로 등록하면
지난 회차가 일부납부·미납·초과납부로 보이던 문제(2026-11-22 대구 행복기숙사 이관 사례) 재현과 해결.
"""
import sys
import uuid
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_phase6_deferral import _admin_headers, client  # noqa: E402


def _loan(h):
    run = uuid.uuid4().hex[:8]
    spc_id = client.post("/api/v1/spc", json={"spc_name": f"SPC-{run}"}, headers=h).json()["id"]
    dorm_id = client.post("/api/v1/dormitories", json={"dormitory_name": f"대구-{run}", "school_name": "테스트대", "spc_id": spc_id}, headers=h).json()["id"]
    fund = next(f for f in client.get("/api/v1/reference/funds", headers=h).json() if f["fund_name"] == "주택도시기금")
    code = lambda g, c: next(x for x in client.get("/api/v1/reference/codes", params={"group_code": g}, headers=h).json() if x["code"] == c)  # noqa: E731
    r = client.post("/api/v1/loans", json={
        "dormitory_id": dorm_id, "fund_id": fund["id"], "loan_code": f"2019-03-13_주택-{run}",
        "assigned_amount": 1_036_307_500, "assigned_date": "2019-03-13", "rate_type": "FIXED",
        "repay_cycle_code_id": code("REPAY_CYCLE", "SEMIANNUAL")["id"], "repay_method_code_id": code("REPAY_METHOD", "EQUAL_PAYMENT")["id"],
        "grace_start_date": "2019-03-13", "grace_end_date": "2022-02-28", "principal_repay_start_date": "2022-08-31",
        "repay_period_months": 324, "initial_rate": {"effective_start_date": "2019-03-13", "annual_rate": "1.57100"},
    }, headers=h)
    assert r.status_code == 201, r.text
    lid = r.json()["id"]
    client.post(f"/api/v1/loans/{lid}/schedule/generate", headers=h)
    return lid


def _not_paid(h, lid, cutoff):
    rows = client.get(f"/api/v1/loans/{lid}/payments/summary", params={"as_of": "2026-11-22"}, headers=h).json()["rows"]
    return [(x["due_date"], x["status"]) for x in rows if x["due_date"] <= cutoff and x["status"] != "PAID"]


def test_resync_migration_paid_after_deferral():
    from app.core.database import SessionLocal
    from app.domain.migration_service import AUTO_PAID_PREFIX
    from app.domain.schedule_amounts import scheduled_by_due_date
    from app.domain.version_utils import get_current_version
    from app.models.organization import MigrationBatch
    from app.models.payment import ActualPayment
    h = _admin_headers()
    lid, cutoff = _loan(h), date(2026, 11, 20)
    unpaid_on_purpose = date(2023, 2, 28)       # 납부예외(미납)로 이관한 회차 — 다시 맞추기에서도 그대로 미납이어야 함
    with SessionLocal() as db:                  # 이관의 '기준일 이전 회차 납부완료'와 똑같이 기록
        sched = scheduled_by_due_date(db, get_current_version(db, uuid.UUID(lid)))
        for d, x in sched.items():
            if d <= cutoff and x["total"] > 0 and d != unpaid_on_purpose:
                db.add(ActualPayment(loan_id=uuid.UUID(lid), due_date=d, actual_paid_date=d, actual_principal=x["principal"],
                                     actual_interest=x["interest"], remark=f"{AUTO_PAID_PREFIX} {cutoff} 이전 예정대로 납부)"))
        b = MigrationBatch(file_name="pytest.xlsx", cutoff_date=cutoff, mark_paid=True, summary={}, loan_ids=[lid], dormitory_ids=[])
        db.add(b); db.commit(); bid = str(b.id)
    assert _not_paid(h, lid, "2026-11-20") == [("2023-02-28", "UNPAID")]

    r = client.post(f"/api/v1/loans/{lid}/events/deferral", json={"event_date": "2024-02-29", "defer_target": "PRINCIPAL",
                    "recovery_cycle_months": 3, "recovery_count": 10, "reason": "pytest"}, headers=h)
    assert r.status_code == 201, r.text
    bad = dict(_not_paid(h, lid, "2026-11-20"))
    assert bad["2024-02-29"] == "OVERPAID" and bad["2024-08-31"] == "PARTIAL" and bad["2025-05-31"] == "UNPAID"
    assert bad["2024-05-31"] == bad["2024-11-30"] == "UNPAID"     # 유예 후 1년 분기 이자 전용 행(2026-11-28 기준)
    stale = client.get("/api/v1/payments/overdue", params={"as_of": "2026-11-22"}, headers=h).json()["migration_stale"]
    assert any(s["batch_id"] == bid and s["count"] == 11 for s in stale)

    pv = client.post(f"/api/v1/admin/migration/batches/{bid}/resync-paid", params={"apply": "false"}, headers=h).json()
    assert pv["total"] == 11 and _not_paid(h, lid, "2026-11-20") != [("2023-02-28", "UNPAID")]   # 미리보기는 바꾸지 않음
    ap = client.post(f"/api/v1/admin/migration/batches/{bid}/resync-paid", params={"apply": "true"}, headers=h).json()
    assert (ap["fixed"], ap["added"]) == (6, 5)
    assert _not_paid(h, lid, "2026-11-20") == [("2023-02-28", "UNPAID")]           # 일부러 미납으로 둔 회차만 남음
    assert client.post(f"/api/v1/admin/migration/batches/{bid}/resync-paid", params={"apply": "false"}, headers=h).json()["total"] == 0
    stale = client.get("/api/v1/payments/overdue", params={"as_of": "2026-11-22"}, headers=h).json()["migration_stale"]
    assert not any(s["batch_id"] == bid for s in stale)
