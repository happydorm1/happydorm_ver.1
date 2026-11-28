"""
상환유예 '원금만' — 유예원금 이자 기준(2026-11-28 확정): 세종공동캠퍼스 2022-03-31_주택 수작업 테이블 재현.
  16,500,000,000원 · 1.572% · 반기 원리금균등 · 2025-08-31부터 54회
  2026-02-28 회차 원금 248,402,688원 유예 → 2027-02-28부터 분기 10회 회수
  · 이자 시작일 = 유예 상환일(2026-02-28) — 그 회차 정규이자에 이미 포함된 기간을 다시 받지 않는다
  · 회수 시작 전 1년도 회수 주기(분기)마다 납부: 2026-05-31·08-31·11-30·2027-02-28
"""
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_phase6_deferral import _admin_headers, client  # noqa: E402

# 수작업 엑셀 '주택 2022-03-31_(26유예)' — 상환일별 유예원금(1차)의 이자 합계(연말 분리분은 합산)
LEGACY_DEFERRED_INTEREST = {
    "2026-05-31": 984_246, "2026-08-31": 984_246, "2026-11-30": 973_548, "2027-02-28": 331_648 + 631_201,
    "2027-05-31": 885_822, "2027-08-31": 787_397, "2027-11-30": 681_484, "2028-02-29": 198_989 + 385_140,
    "2028-05-31": 492_123, "2028-08-31": 393_699, "2028-11-30": 292_064, "2029-02-28": 66_330 + 126_240,
    "2029-05-31": 98_425,
}


def test_principal_only_deferral_sejong():
    h = _admin_headers()
    run = uuid.uuid4().hex[:8]
    spc_id = client.post("/api/v1/spc", json={"spc_name": f"SPC-{run}"}, headers=h).json()["id"]
    dorm_id = client.post("/api/v1/dormitories", json={"dormitory_name": f"세종-{run}", "school_name": "테스트대",
                                                      "spc_id": spc_id}, headers=h).json()["id"]
    fund = next(f for f in client.get("/api/v1/reference/funds", headers=h).json() if f["fund_name"] == "주택도시기금")
    code = lambda g, c: next(x for x in client.get("/api/v1/reference/codes", params={"group_code": g}, headers=h).json() if x["code"] == c)  # noqa: E731
    r = client.post("/api/v1/loans", json={
        "dormitory_id": dorm_id, "fund_id": fund["id"], "loan_code": f"2022-03-31_주택-{run}",
        "assigned_amount": 16_500_000_000, "assigned_date": "2022-03-31", "rate_type": "FIXED",
        "repay_cycle_code_id": code("REPAY_CYCLE", "SEMIANNUAL")["id"],
        "repay_method_code_id": code("REPAY_METHOD", "EQUAL_PAYMENT")["id"],
        "grace_start_date": "2022-03-31", "grace_end_date": "2025-02-28",
        "principal_repay_start_date": "2025-08-31", "repay_period_months": 324,
        "initial_rate": {"effective_start_date": "2022-03-31", "annual_rate": "1.57200"},
    }, headers=h)
    assert r.status_code == 201, r.text
    loan_id = r.json()["id"]
    v1 = client.post(f"/api/v1/loans/{loan_id}/schedule/generate", headers=h).json()["rows"]
    assert next(x for x in v1 if x["due_date"] == "2026-02-28" and x["principal_amount"])["principal_amount"] == 248_402_688

    r = client.post(f"/api/v1/loans/{loan_id}/events/deferral", json={
        "event_date": "2026-02-28", "defer_target": "PRINCIPAL", "recovery_cycle_months": 3,
        "recovery_count": 10, "reason": "pytest 세종 재현"}, headers=h)
    assert r.status_code == 201, r.text
    res = r.json()
    assert res["resulting_schedule"]["status"] == "NORMAL", res["resulting_schedule"]["validation_errors"]
    rows = res["resulting_schedule"]["rows"]
    assert rows[-1]["balance_after"] == 0

    v1_by = {}
    for x in v1:
        v1_by.setdefault(x["due_date"], []).append(x)
    extra = {}
    for x in rows:
        if x["row_type"] == "YEAR_END_SPLIT_MEMO":
            continue
        base = sum(y["unpaid_interest_amount"] for y in v1_by.get(x["due_date"], []) if y["row_type"] != "YEAR_END_SPLIT_MEMO")
        extra[x["due_date"]] = extra.get(x["due_date"], 0) + x["unpaid_interest_amount"]
        extra[x["due_date"]] -= base if x is max((y for y in rows if y["due_date"] == x["due_date"] and y["row_type"] != "YEAR_END_SPLIT_MEMO"), key=lambda y: y["seq_no"]) else 0
    got = {d: v for d, v in extra.items() if v}
    # 연말(12/31)에서 나눠 반올림 — 원 단위까지 일치
    assert set(got) == set(LEGACY_DEFERRED_INTEREST), sorted(set(got) ^ set(LEGACY_DEFERRED_INTEREST))
    for d, v in LEGACY_DEFERRED_INTEREST.items():
        assert got[d] == v, (d, got[d], v)
    # 회수 시작 전 분기일(정규 상환일 아님)은 이자 전용 행
    for d in ("2026-05-31", "2026-11-30"):
        r_ = [x for x in rows if x["due_date"] == d]
        assert len(r_) == 1 and r_[0]["row_type"] == "DEFERRAL_RECOVERY" and r_[0]["principal_amount"] == 0
    # 회수 원금(2027-02-28부터 분기 10회)
    rec = sorted((x for x in res["event"]["deferral"]["recovery_rows"] if x["installment_seq"] > 0), key=lambda x: x["installment_seq"])
    assert [x["recovery_principal"] for x in rec] == [24_840_269] * 9 + [24_840_267]
