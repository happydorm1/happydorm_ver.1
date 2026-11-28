"""
상환유예 — '원금만' 유예 시 유예원금 이자(잔액 방식) 재현.
대상: 대구 행복기숙사 2018-12-28_주택(수작업 엑셀 '2018-12-28_주택' 시트 P열 우측 표)
  29,963,692,500원 · 1.571% · 반기 원리금균등 · 2022-02-28부터 54회
  5회차(2024-02-29) 원금 461,871,184원 유예 → 2025-02-28부터 분기 10회 회수
유예원금 이자 = ROUND(잔여 유예원금 × 금리 × 직전 납부일부터 일수 / 365).
2026-11-28 기준 변경(세종 2022-03-31_주택 수작업 테이블 — test_phase26): 이자는 유예 상환일(2024-02-29)부터,
회수 시작 전 1년도 회수 주기(분기)마다 납부. 예전 대구 수작업 방식(동결 회차 이자계산 시작일 2023-12-31부터 244일,
2024-08-31 4,850,584원)은 유예 회차 정규이자와 60일이 겹쳐 이중 부과가 되므로 쓰지 않는다.
"""
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_phase6_deferral import _admin_headers, client  # noqa: E402


def _daegu_loan(h):
    run = uuid.uuid4().hex[:8]
    spc_id = client.post("/api/v1/spc", json={"spc_name": f"SPC-{run}"}, headers=h).json()["id"]
    dorm_id = client.post("/api/v1/dormitories", json={"dormitory_name": f"대구-{run}", "school_name": "테스트대",
                                                      "spc_id": spc_id}, headers=h).json()["id"]
    fund = next(f for f in client.get("/api/v1/reference/funds", headers=h).json() if f["fund_name"] == "주택도시기금")
    code = lambda g, c: next(x for x in client.get("/api/v1/reference/codes", params={"group_code": g}, headers=h).json() if x["code"] == c)  # noqa: E731
    r = client.post("/api/v1/loans", json={
        "dormitory_id": dorm_id, "fund_id": fund["id"], "loan_code": f"2018-12-28_주택-{run}",
        "assigned_amount": 29_963_692_500, "assigned_date": "2018-12-28", "rate_type": "FIXED",
        "repay_cycle_code_id": code("REPAY_CYCLE", "SEMIANNUAL")["id"],
        "repay_method_code_id": code("REPAY_METHOD", "EQUAL_PAYMENT")["id"],
        "grace_start_date": "2018-12-28", "grace_end_date": "2021-08-31",
        "principal_repay_start_date": "2022-02-28", "repay_period_months": 324,
        "initial_rate": {"effective_start_date": "2018-12-28", "annual_rate": "1.57100"},
    }, headers=h)
    assert r.status_code == 201, r.text
    loan_id = r.json()["id"]
    v1 = client.post(f"/api/v1/loans/{loan_id}/schedule/generate", headers=h).json()["rows"]
    assert next(x for x in v1 if x["installment_no"] == 5)["principal_amount"] == 461_871_184
    return loan_id, v1


def test_principal_only_deferral_interest_on_remaining_balance():
    h = _admin_headers()
    loan_id, v1 = _daegu_loan(h)
    r = client.post(f"/api/v1/loans/{loan_id}/events/deferral", json={
        "event_date": "2024-02-29", "defer_target": "PRINCIPAL", "recovery_cycle_months": 3,
        "recovery_count": 10, "reason": "pytest 엑셀 재현"}, headers=h)
    assert r.status_code == 201, r.text
    res = r.json()
    d = res["event"]["deferral"]
    assert d["frozen_principal_total"] == 461_871_184 and d["frozen_interest_total"] == 0
    assert d["recovery_start_date"] == "2025-02-28"

    recs = sorted(d["recovery_rows"], key=lambda x: x["installment_seq"])
    # 회수 시작 전 분기일(2024-05-31·08-31·11-30): 회수원금 0, 이자만 — 유예 상환일(2024-02-29)부터 92·92·91일
    pre = [x for x in recs if x["installment_seq"] == 0]
    assert sorted(x["additional_interest_on_frozen_principal"] for x in pre) == [1_809_029, 1_828_909, 1_828_909]
    assert all(x["recovery_principal"] == 0 for x in pre)
    recs = [x for x in recs if x["installment_seq"] > 0]
    assert [x["recovery_principal"] for x in recs] == [46_187_118] * 9 + [46_187_122]
    assert [x["additional_interest_on_frozen_principal"] for x in recs] == [
        1_789_150, 1_646_018, 1_463_127, 1_266_320, 1_073_490, 914_454, 731_563, 542_709, 357_830, 182_891]

    rows = res["resulting_schedule"]["rows"]
    assert res["resulting_schedule"]["status"] == "NORMAL", res["resulting_schedule"]["validation_errors"]
    assert rows[-1]["balance_after"] == 0
    by_date = {}
    for x in rows:
        if x["row_type"] != "YEAR_END_SPLIT_MEMO":
            by_date.setdefault(x["due_date"], []).append(x)
    # 분기 납부일(05-31, 11-30)은 회수전용 행이 새로 생김(회수 시작 전 2024년 포함)
    for dd in ("2024-05-31", "2024-11-30", "2025-05-31", "2025-11-30", "2026-05-31", "2026-11-30", "2027-05-31"):
        assert by_date[dd][0]["row_type"] == "DEFERRAL_RECOVERY"
    # 회수 기간 정규회차: 정규 이자는 그대로, 유예원금 이자만 더해짐
    reg = next(x for x in v1 if x["due_date"] == "2025-08-31" and x["row_type"] != "YEAR_END_SPLIT_MEMO")
    new = by_date["2025-08-31"][-1]
    assert new["interest_amount"] == reg["interest_amount"] == 210_163_663
    assert new["unpaid_interest_amount"] - reg["unpaid_interest_amount"] == 1_463_127
    # 회수 끝난 뒤 정규회차는 그대로
    after = next(x for x in by_date["2027-08-31"])
    assert after["interest_amount"] == 195_131_035 and after["principal_amount"] == 487_873_524


def test_principal_only_deferral_regular_dates_only():
    """선택: 유예기간 이자 납부 '정규 상환일에만' — 회수 시작 전에는 반기 정규 상환일(2024-08-31·2025-02-28)에만 낸다.
    이자 시작일은 똑같이 유예 상환일(2024-02-29) 고정."""
    h = _admin_headers()
    loan_id, _ = _daegu_loan(h)
    r = client.post(f"/api/v1/loans/{loan_id}/events/deferral", json={
        "event_date": "2024-02-29", "defer_target": "PRINCIPAL", "recovery_cycle_months": 3,
        "recovery_count": 10, "interest_pay_mode": "REGULAR"}, headers=h)
    assert r.status_code == 201, r.text
    res = r.json()
    d = res["event"]["deferral"]
    assert d["interest_pay_mode"] == "REGULAR"
    recs = sorted(d["recovery_rows"], key=lambda x: x["installment_seq"])
    pre = [x for x in recs if x["installment_seq"] == 0]
    assert [x["additional_interest_on_frozen_principal"] for x in pre] == [3_657_817]          # 2024-08-31, 184일
    after = [x["additional_interest_on_frozen_principal"] for x in recs if x["installment_seq"] > 0]
    assert after == [3_598_179, 1_646_018, 1_463_127, 1_266_320, 1_073_490, 914_454, 731_563, 542_709, 357_830, 182_891]
    dates = {x["due_date"] for x in res["resulting_schedule"]["rows"]}
    assert "2024-05-31" not in dates and "2024-11-30" not in dates
    assert res["resulting_schedule"]["status"] == "NORMAL"
