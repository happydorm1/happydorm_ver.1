"""
거치기간 중 원금상환 — 수작업 테이블 '(대환전) 2017-03-31_공자' 재현(J20·J22).
4,877,000,000원 · 공자 분기 원금균등 · 거치 2017-04-28~2023-02-28 · 2023-05-31부터 100회(25년)
거치 중 2018-11-30 776,980,774원, 2019-02-28 100,000,000원 상환 → 이후 이자는 줄어든 잔액으로,
상환기간 회차당 원금은 48,770,000원(배정금액/100) 그대로 → 잔액이 먼저 바닥나 2043-11-30(879,226원)에 종료.
금리: 2.088% → 2024-01-01 3.964% → 2031-01-01 2.088%(엑셀 G3 재사용)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_phase8_events import _headers, _refs, client  # noqa: E402

EXPECTED = json.loads((Path(__file__).parent / "data_grace_principal_2017_03_31.json").read_text())
GRACE = [{"due_date": "2018-11-30", "amount": 776_980_774}, {"due_date": "2019-02-28", "amount": 100_000_000}]


def _loan(h, run, **over):
    funds, cyc, mth = _refs(h)
    spc = client.post("/api/v1/spc", json={"spc_name": f"S-{run}"}, headers=h).json()["id"]
    dorm = client.post("/api/v1/dormitories", json={"dormitory_name": f"D-{run}", "school_name": "t", "spc_id": spc}, headers=h).json()["id"]
    body = {
        "dormitory_id": dorm, "fund_id": funds["공공자금관리기금"]["id"], "loan_code": f"2017-03-31_공자-{run}",
        "assigned_amount": 4_877_000_000, "assigned_date": "2017-04-28", "rate_type": "VARIABLE",
        "repay_cycle_code_id": cyc["QUARTERLY"]["id"], "repay_method_code_id": mth["EQUAL_PRINCIPAL"]["id"],
        "grace_start_date": "2017-04-28", "grace_end_date": "2023-02-28",
        "principal_repay_start_date": "2023-05-31", "repay_period_months": 300,
        "grace_principal_payments": GRACE,
        "initial_rate": {"effective_start_date": "2017-04-28", "effective_end_date": "2024-01-01", "annual_rate": "2.088"},
    }
    body.update(over)
    return client.post("/api/v1/loans", json=body, headers=h)


def test_grace_principal_payments_match_manual_table():
    import uuid
    h = _headers()
    r = _loan(h, uuid.uuid4().hex[:8])
    assert r.status_code == 201, r.text
    loan_id = r.json()["id"]
    assert r.json()["grace_principal_payments"] == GRACE
    for s, e, rate in (("2024-01-01", "2031-01-01", "3.964"), ("2031-01-01", None, "2.088")):
        assert client.post(f"/api/v1/loans/{loan_id}/rate-history", headers=h,
                           json={"effective_start_date": s, "effective_end_date": e, "annual_rate": rate}).status_code == 201
    v = client.post(f"/api/v1/loans/{loan_id}/schedule/generate", headers=h).json()
    assert v["status"] == "NORMAL", v.get("validation_errors")
    got: dict[str, list[int]] = {}
    for x in v["rows"]:
        a = got.setdefault(x["due_date"], [0, 0])
        a[0] += x["principal_amount"]
        a[1] += x["interest_amount"] + x["unpaid_interest_amount"]
    diff = {d: (got.get(d), EXPECTED.get(d)) for d in set(got) | set(EXPECTED) if got.get(d) != EXPECTED.get(d)}
    assert not diff, sorted(diff.items())[:5]
    assert v["rows"][-1]["due_date"] == "2043-11-30" and v["rows"][-1]["balance_after"] == 0


def test_grace_principal_payment_validation():
    import uuid
    h = _headers()
    bad_date = _loan(h, uuid.uuid4().hex[:8], grace_principal_payments=[{"due_date": "2025-11-30", "amount": 1}])
    assert bad_date.status_code == 422 and "거치기간" in bad_date.text
    off_cycle = _loan(h, uuid.uuid4().hex[:8], grace_principal_payments=[{"due_date": "2018-11-15", "amount": 1_000}])
    assert off_cycle.status_code == 201
    g = client.post(f"/api/v1/loans/{off_cycle.json()['id']}/schedule/generate", headers=h)
    assert g.status_code == 422 and "상환예정일" in g.text
