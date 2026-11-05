"""금리변동 이벤트에 적용종료일(선택) — 금리이력에 저장되고, 영업일 D-30부터 '금리변동 예정' 알림에 나온다."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_phase8_events import _headers, _make_loan, client  # noqa: E402


def _rate_alerts(h, dorm_id, as_of):
    d = client.get("/api/v1/dashboard", params={"as_of": as_of, "dormitory_id": dorm_id}, headers=h).json()
    return d["alerts"]["rate_changes_upcoming"]["items"]


def test_rate_change_with_end_date_triggers_d30_alert():
    h = _headers()
    loan_id = _make_loan(h, fund="공공자금관리기금", cycle="QUARTERLY", method="EQUAL_PRINCIPAL",
                         start="2023-02-28", first="2023-05-31", months=60, rate_type="VARIABLE")
    bad = client.post(f"/api/v1/loans/{loan_id}/events/rate-change", json={
        "effective_start_date": "2024-01-01", "effective_end_date": "2023-12-31", "annual_rate": "3.5"}, headers=h)
    assert bad.status_code == 422 and "적용종료일" in bad.text

    r = client.post(f"/api/v1/loans/{loan_id}/events/rate-change", json={
        "effective_start_date": "2024-01-01", "effective_end_date": "2024-12-31", "annual_rate": "3.5"}, headers=h)
    assert r.status_code == 201, r.text
    loan = client.get(f"/api/v1/loans/{loan_id}", headers=h).json()
    hist = sorted((x for x in loan["rate_history"] if not x["is_deleted"]), key=lambda x: x["effective_start_date"])
    assert hist[-1]["effective_start_date"] == "2024-01-01" and hist[-1]["effective_end_date"] == "2024-12-31"
    assert hist[-2]["effective_end_date"] == "2024-01-01"            # 직전 구간은 새 적용시작일로 닫힘

    dorm = loan["dormitory_id"]
    assert not [x for x in _rate_alerts(h, dorm, "2024-10-01") if x["loan_id"] == loan_id]   # D-30 전
    items = [x for x in _rate_alerts(h, dorm, "2024-12-02") if x["loan_id"] == loan_id]     # D-30 안
    assert items and items[0]["change_date"] == "2024-12-31" and items[0]["current_rate"] == 3.5
