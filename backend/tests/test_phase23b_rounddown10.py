"""
이자 끝수처리 '절사(일의 자리)' — 대구 행복기숙사 2023-12-22 주택 수작업 테이블 재현.
300,000,000원 · 1.571% · 주택도시기금 반기 원리금균등 · 거치 2023-12-22~2026-08-31 · 2027-02-28부터 54회.
이자: 거치 = 단리(일수), 상환 = IPMT — 모두 10원 미만 버림. 원금: PPMT 반올림, 마지막 회차 자동보정.
"""
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engine.schedule_engine import calc_simple_interest, round_interest  # noqa: E402
from tests.test_phase6_deferral import _admin_headers, client  # noqa: E402

# 상환예정일: (원금, 이자) — 수작업 엑셀 H·I열
EXPECTED = {
    "2024-02-29": (0, 890_950), "2024-08-31": (0, 2_375_860), "2025-02-28": (0, 2_337_130),
    "2025-08-31": (0, 2_375_860), "2026-02-28": (0, 2_337_130), "2026-08-31": (0, 2_375_860),
    "2027-02-28": (4_481_822, 2_356_500), "2027-08-31": (4_517_026, 2_321_290), "2028-02-29": (4_552_508, 2_285_810),
    "2040-02-29": (5_492_929, 1_345_390), "2053-02-28": (6_732_144, 106_170), "2053-08-31": (6_785_029, 53_290),
}


def test_round_interest_units():
    assert round_interest(2_375_868.49, "ROUND") == 2_375_868
    assert round_interest(2_375_868.49, "ROUNDDOWN") == 2_375_868
    assert round_interest(2_375_868.49, "ROUNDDOWN10") == 2_375_860
    assert round_interest(2_356_499.9999999995, "ROUNDDOWN10") == 2_356_500     # 부동소수 오차
    assert calc_simple_interest(300_000_000, 69, 1.571, "ROUNDDOWN10") == 890_950


def test_rounddown10_matches_manual_table():
    h = _admin_headers()
    run = uuid.uuid4().hex[:8]
    spc_id = client.post("/api/v1/spc", json={"spc_name": f"SPC-{run}"}, headers=h).json()["id"]
    dorm_id = client.post("/api/v1/dormitories", json={"dormitory_name": f"대구-{run}", "school_name": "테스트대",
                                                      "spc_id": spc_id}, headers=h).json()["id"]
    fund = next(f for f in client.get("/api/v1/reference/funds", headers=h).json() if f["fund_name"] == "주택도시기금")
    code = lambda g, c: next(x for x in client.get("/api/v1/reference/codes", params={"group_code": g}, headers=h).json() if x["code"] == c)  # noqa: E731
    r = client.post("/api/v1/loans", json={
        "dormitory_id": dorm_id, "fund_id": fund["id"], "loan_code": f"2023-12-22_주택-{run}",
        "assigned_amount": 300_000_000, "assigned_date": "2023-12-22", "rate_type": "FIXED",
        "repay_cycle_code_id": code("REPAY_CYCLE", "SEMIANNUAL")["id"],
        "repay_method_code_id": code("REPAY_METHOD", "EQUAL_PAYMENT")["id"],
        "grace_start_date": "2023-12-22", "grace_end_date": "2026-08-31",
        "principal_repay_start_date": "2027-02-28", "repay_period_months": 324, "interest_rounding": "ROUNDDOWN10",
        "initial_rate": {"effective_start_date": "2023-12-22", "annual_rate": "1.57100"},
    }, headers=h)
    assert r.status_code == 201, r.text
    v = client.post(f"/api/v1/loans/{r.json()['id']}/schedule/generate", headers=h).json()
    assert v["status"] == "NORMAL", v["validation_errors"]
    by_date: dict[str, list[int]] = {}
    for x in v["rows"]:
        slot = by_date.setdefault(x["due_date"], [0, 0])
        slot[0] += x["principal_amount"]
        slot[1] += x["interest_amount"] + x["unpaid_interest_amount"]
    assert len(by_date) == 60
    for d, (p, i) in EXPECTED.items():
        assert tuple(by_date[d]) == (p, i), (d, by_date[d], (p, i))
    assert all(i % 10 == 0 for _, i in by_date.values())
    assert sum(p for p, _ in by_date.values()) == 300_000_000
