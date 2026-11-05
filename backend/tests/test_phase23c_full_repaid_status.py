"""전액 조기상환 → 상태 '종료' + 꼬리표 '조기상환(전액)'. 이벤트 취소 시 다시 '진행중'."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_phase8_events import _headers, _make_loan, client  # noqa: E402


def _row(h, loan_id, **params):
    d = client.get("/api/v1/dashboard", params={"as_of": "2026-11-05", **params}, headers=h).json()
    return next((r for r in d["loans"] if r["loan_id"] == loan_id), None)


def test_full_early_repayment_closes_loan_and_labels():
    h = _headers()
    loan_id = _make_loan(h, fund="공공자금관리기금", cycle="QUARTERLY", method="EQUAL_PRINCIPAL",
                         start="2023-02-28", first="2023-05-31", months=60)
    row = _row(h, loan_id)
    assert row["status"] == "ACTIVE" and row["midterm_labels"] == []

    r = client.post(f"/api/v1/loans/{loan_id}/events/early-repayment",
                    json={"event_date": "2024-04-15", "repay_type": "FULL"}, headers=h)
    assert r.status_code == 201, r.text
    row = _row(h, loan_id)
    assert row["status"] == "CLOSED"
    assert row["midterm_labels"] == ["조기상환(전액)"]
    assert _row(h, loan_id, status="ACTIVE") is None              # 상태 조건: 진행중에서 빠지고
    assert _row(h, loan_id, status="CLOSED")["status"] == "CLOSED"  # 종료에 나온다
    assert client.get(f"/api/v1/loans/{loan_id}", headers=h).json()["closed_by_full_repayment"] is True

    ev = client.get(f"/api/v1/loans/{loan_id}/events", headers=h).json()
    eid = next(e["id"] for e in ev if e["event_type"] == "EARLY_REPAYMENT")
    assert client.post(f"/api/v1/loans/{loan_id}/events/{eid}/cancel", json={"reason": "테스트"}, headers=h).status_code == 200
    row = _row(h, loan_id)
    assert row["status"] == "ACTIVE" and row["midterm_labels"] == []
    assert client.get(f"/api/v1/loans/{loan_id}", headers=h).json()["closed_by_full_repayment"] is False


def test_partial_early_repayment_label():
    h = _headers()
    loan_id = _make_loan(h, fund="공공자금관리기금", cycle="QUARTERLY", method="EQUAL_PRINCIPAL",
                         start="2023-02-28", first="2023-05-31", months=60)
    r = client.post(f"/api/v1/loans/{loan_id}/events/early-repayment",
                    json={"event_date": "2024-02-29", "repay_type": "PARTIAL", "repay_amount": 10_000_000}, headers=h)
    assert r.status_code == 201, r.text
    row = _row(h, loan_id)
    assert row["status"] == "ACTIVE" and row["midterm_labels"] == ["조기상환(일부)"]
