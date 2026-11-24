"""Phase 20g: 대시보드 '다음 분기 상환예정' KPI"""
from test_phase13_dashboard import _dash, _schedule_rows
from _helpers import admin_headers, make_loan, make_org


def test_next_quarter_scheduled_and_label():
    h = admin_headers()
    _, dorm = make_org(h)
    a, _, _ = make_loan(h, dorm=dorm)          # 분기 상환(2·5·8·11월 말)
    rows = _schedule_rows(h, a)
    tot = lambda pre: sum(r["principal_amount"] + r["interest_amount"] + r["unpaid_interest_amount"]
                          for r in rows if r["due_date"][:7] in pre)

    k = _dash(h, "2024-05-10", dormitory_id=dorm)["kpis"]        # 2분기 → 다음 분기 3분기(7~9월)
    assert k["next_quarter"]["label"] == "2024년 3분기"
    assert (k["next_quarter"]["from"], k["next_quarter"]["to"]) == ("2024-07-01", "2024-09-30")
    assert k["next_quarter_scheduled"] == tot({"2024-07", "2024-08", "2024-09"}) > 0

    k = _dash(h, "2024-11-20", dormitory_id=dorm)["kpis"]        # 4분기 → 이듬해 1분기
    assert k["next_quarter"]["label"] == "2025년 1분기"
    assert k["next_quarter_scheduled"] == tot({"2025-01", "2025-02", "2025-03"}) > 0
