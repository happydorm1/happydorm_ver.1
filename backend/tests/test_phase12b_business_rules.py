"""확정된 업무기준 검증: ① 휴일 상환일은 다음 영업일이 납부기한 ② 금리변동 D-30은 영업일 기준"""
import uuid
from datetime import date, timedelta

from _helpers import admin_headers, client, make_loan, refs
from test_phase12_alerts import _holidays, _inbox, _scan, _setup_org_with_people

from app.engine.business_day import (
    count_business_days, current_threshold, effective_due_date, subtract_business_days,
)

SAT_DUE = date(2024, 8, 31)       # 토요일 — make_loan(분기, 2023-02-28 시작)의 상환일 중 하나
assert SAT_DUE.weekday() == 5


def test_effective_due_date_and_business_day_counting_engine():
    none = set()
    assert effective_due_date(date(2024, 8, 30), none) == date(2024, 8, 30)       # 영업일이면 그대로
    assert effective_due_date(SAT_DUE, none) == date(2024, 9, 2)                  # 토 → 다음 영업일(월)
    chuseok = {date(2024, 9, 16), date(2024, 9, 17), date(2024, 9, 18)}
    assert effective_due_date(date(2024, 9, 14), chuseok) == date(2024, 9, 19)    # 주말+연휴를 지나 첫 영업일
    assert count_business_days(date(2024, 8, 30), date(2024, 9, 2), none) == 1    # 금 → 다음 월 = 1영업일
    assert count_business_days(date(2024, 9, 2), date(2024, 9, 2), none) == 0
    # 시작일은 세지 않는다: 7/31 다음 날부터 8/31까지 = 2024년 8월 평일 22일
    assert count_business_days(date(2024, 7, 31), date(2024, 8, 31), none) == 22
    assert count_business_days(date(2024, 8, 1), date(2024, 8, 31), none) == 21


def test_payment_deadline_shifts_to_next_business_day_for_status_and_overdue():
    h = admin_headers()
    loan_id, _, _ = make_loan(h)
    deadline = effective_due_date(SAT_DUE, _holidays(h))
    assert deadline > SAT_DUE

    def row(as_of):
        s = client.get(f"/api/v1/loans/{loan_id}/payments/summary", params={"as_of": as_of.isoformat()}, headers=h).json()
        return next(r for r in s["rows"] if r["due_date"] == SAT_DUE.isoformat())

    r = row(SAT_DUE + timedelta(days=1))                    # 상환일(토)은 지났지만 납부기한(월) 전
    assert r["payment_deadline"] == deadline.isoformat()
    assert r["status"] == "PENDING" and r["is_overdue"] is False        # 예전 방식이면 미납으로 판정되던 구간
    assert row(deadline)["is_overdue"] is False                          # 납부기한 당일까지는 연체 아님
    late = row(deadline + timedelta(days=1))
    assert late["status"] == "UNPAID" and late["is_overdue"] is True

    assert client.post(f"/api/v1/loans/{loan_id}/payments", json={
        "due_date": SAT_DUE.isoformat(), "actual_paid_date": deadline.isoformat(),
        "actual_principal": late["scheduled_principal"], "actual_interest": late["scheduled_interest"]}, headers=h).status_code == 201
    paid = row(deadline + timedelta(days=1))
    assert paid["status"] == "PAID" and paid["days_after_due"] == 0     # 납부기한 당일 납부 = 지연 0일


def test_notice_deadline_defaults_to_next_business_day():
    h = admin_headers()
    loan_id, dorm_id, _ = make_loan(h)
    r = client.post("/api/v1/document-templates", json={
        "template_name": f"양식-{uuid.uuid4().hex[:6]}", "config": {"bank_account": "테스트 000", "account_holder": "테스트"}}, headers=h)
    assert r.status_code == 201
    deadline = effective_due_date(SAT_DUE, _holidays(h))

    d = client.get(f"/api/v1/dormitories/{dorm_id}/notices/preview", params={"due_date": SAT_DUE.isoformat()}, headers=h).json()
    assert d["due_date"] == "2024-08-31" and d["payment_deadline"] == deadline.isoformat()
    assert d["payment_deadline_adjusted"] is True
    assert any("다음 영업일" in w for w in d["warnings"])

    d2 = client.get(f"/api/v1/dormitories/{dorm_id}/notices/preview",
                    params={"due_date": SAT_DUE.isoformat(), "payment_deadline": "2024-08-30"}, headers=h).json()
    assert d2["payment_deadline"] == "2024-08-30" and d2["payment_deadline_adjusted"] is False   # 직접 지정 우선
    d3 = client.get(f"/api/v1/dormitories/{dorm_id}/notices/preview", params={"due_date": "2024-05-31"}, headers=h).json()
    assert d3["payment_deadline"] == "2024-05-31" and d3["payment_deadline_adjusted"] is False   # 영업일이면 그대로


def test_reminders_continue_until_the_adjusted_deadline():
    """상환일(토) 다음 날(일)에 처음 돈 배치도 납부기한(월)이 남았으므로 알림을 보낸다 — 예전 방식은 건너뛰던 구간."""
    h = admin_headers()
    _, dorm_id, spc_h, fund_h = _setup_org_with_people(h)
    loan_id, _, _ = make_loan(h, dorm=dorm_id)
    hol = _holidays(h)
    deadline = effective_due_date(SAT_DUE, hol)

    as_of = SAT_DUE + timedelta(days=1)
    assert as_of < deadline
    assert _scan(h, as_of, dorm_id)["created"]["repayment"] == 2
    n = current_threshold(deadline, as_of, hol)
    note = [x for x in _inbox(fund_h, loan_id) if x["reference_date"] == SAT_DUE.isoformat()]
    assert [x["notification_type"] for x in note] == [f"REPAYMENT_D{n}"]
    p = note[0]["payload"]
    assert p["due_date"] == "2024-08-31" and p["payment_deadline"] == deadline.isoformat()
    assert "납부기한은" in note[0]["body"]
    assert _scan(h, deadline + timedelta(days=1), dorm_id)["created"]["repayment"] == 0   # 기한 경과 후엔 안 보냄


def test_rate_change_notice_window_is_30_business_days_not_calendar_days():
    h = admin_headers()
    _, dorm_id, spc_h, fund_h = _setup_org_with_people(h)
    funds, cyc, mth = refs(h)
    change = date(2023, 6, 15)
    hol = _holidays(h)

    r = client.post("/api/v1/loans", json={
        "dormitory_id": dorm_id, "fund_id": funds["공공자금관리기금"]["id"], "loan_code": f"BD-{uuid.uuid4().hex[:8]}",
        "assigned_amount": 100_000_000, "assigned_date": "2023-02-28", "rate_type": "VARIABLE",
        "repay_cycle_code_id": cyc["QUARTERLY"]["id"], "repay_method_code_id": mth["EQUAL_PRINCIPAL"]["id"],
        "principal_repay_start_date": "2023-05-31", "repay_period_months": 60,
        "initial_rate": {"effective_start_date": "2023-02-28", "effective_end_date": change.isoformat(), "annual_rate": "3.0"}},
        headers=h)
    assert r.status_code == 201, r.text
    loan_id = r.json()["id"]
    assert client.post(f"/api/v1/loans/{loan_id}/schedule/generate", headers=h).status_code == 201

    first_day = subtract_business_days(change, 30, hol)                  # 변경일 전 30번째 영업일 = 첫 알림일
    assert count_business_days(first_day, change, hol) == 30

    def rate_alerts(as_of):
        _scan(h, as_of, dorm_id)
        return [x for x in _inbox(fund_h, loan_id) if x["notification_type"] == "RATE_CHANGE_D30"]

    assert rate_alerts(first_day - timedelta(days=1)) == []              # 31영업일 전 — 아직 아님
    got = rate_alerts(first_day)
    assert len(got) == 1
    p = got[0]["payload"]
    assert p["business_days_left"] == 30 and p["days_left"] == (change - first_day).days
    assert p["days_left"] > 30                                            # 달력일 기준이었다면 보내지 않았을 시점
    assert got[0]["title"].startswith("[금리변동 D-30]")
