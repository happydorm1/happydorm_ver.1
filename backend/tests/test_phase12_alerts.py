"""Phase 12: 영업일 계산 / 공휴일 / 상환일정·금리변동 알림 / 대시보드 업무 알림"""
import uuid
from datetime import date

from _helpers import admin_headers, client, make_loan, make_org, refs, spc_manager_headers

from app.core.database import SessionLocal
from app.engine.business_day import (
    current_threshold, next_business_day, prev_business_day, subtract_business_days,
)
from app.models.loan import InterestRateHistory
from app.models.schedule import RepaymentScheduleRow, RepaymentScheduleVersion, ScheduleVersionStatus


# ---------------------------------------------------------------- 영업일 엔진(순수)
def test_business_day_engine():
    none = set()
    mon = date(2024, 9, 23)
    # 달력일이 아니라 영업일: 15영업일 전 = 3주 전 월요일(달력 D-15는 9/8)
    assert subtract_business_days(mon, 15, none) == date(2024, 9, 2) != date(2024, 9, 8)
    assert subtract_business_days(mon, 1, none) == date(2024, 9, 20)        # 월요일의 D-1은 금요일

    chuseok = {date(2024, 9, 16), date(2024, 9, 17), date(2024, 9, 18)}
    thu = date(2024, 9, 19)
    assert subtract_business_days(thu, 1, chuseok) == date(2024, 9, 13)     # 연휴·주말을 건너뜀
    assert subtract_business_days(thu, 3, chuseok) == date(2024, 9, 11)
    # 상환일 자체가 토요일이어도 '이전 N번째 영업일'로 일관되게 계산(자동 순연 없음)
    assert subtract_business_days(date(2024, 9, 21), 1, none) == date(2024, 9, 20)

    assert next_business_day(date(2024, 9, 21), none) == date(2024, 9, 23)
    assert prev_business_day(date(2024, 9, 22), none) == date(2024, 9, 20)
    assert next_business_day(date(2024, 9, 17), chuseok) == date(2024, 9, 19)

    # 이미 도래한 기준 중 가장 임박한 것 하나(밀린 알림 폭주 방지)
    cases = {date(2024, 9, 1): None, date(2024, 9, 2): 15, date(2024, 9, 10): 10, date(2024, 9, 16): 5,
             date(2024, 9, 18): 3, date(2024, 9, 19): 3, date(2024, 9, 20): 1, date(2024, 9, 23): 1}
    for as_of, expected in cases.items():
        assert current_threshold(mon, as_of, none) == expected, as_of


# ---------------------------------------------------------------- 공휴일 기준정보 API
def test_holiday_management_and_calendar_endpoints():
    h = admin_headers()
    for x in client.get("/api/v1/holidays", params={"year": 2031}, headers=h).json():   # 반복 실행 대비 정리
        client.delete(f"/api/v1/holidays/{x['id']}", headers=h)

    d = date(2031, 5, 6)                                   # 화요일
    assert d.weekday() < 5
    r = client.post("/api/v1/holidays", json={"holiday_date": d.isoformat(), "holiday_name": "테스트 휴일"}, headers=h)
    assert r.status_code == 201
    hid = r.json()["id"]
    assert client.post("/api/v1/holidays", json={"holiday_date": d.isoformat(), "holiday_name": "중복"}, headers=h).status_code == 409

    info = client.get("/api/v1/calendar/business-day", params={"date": d.isoformat()}, headers=h).json()
    assert info["is_business_day"] is False and "공휴일" in info["reason"]
    assert info["next_business_day"] == "2031-05-07" and info["prev_business_day"] == "2031-05-05"
    assert info["holidays_missing_for_year"] is False

    # 수요일 상환일의 D-1은 원래 화요일이지만 공휴일이라 월요일로 당겨진다
    alert = client.get("/api/v1/calendar/repayment-alert-dates", params={"due_date": "2031-05-07"}, headers=h).json()
    assert alert["alert_dates"]["D-1"] == "2031-05-05" and alert["due_date_is_business_day"] is True

    # 일괄 등록: 새 날짜만 생성, 중복·입력 내 중복은 건너뜀
    bulk = client.post("/api/v1/holidays/bulk", json={"items": [
        {"holiday_date": "2031-05-05", "holiday_name": "일괄1"}, {"holiday_date": "2031-05-05", "holiday_name": "일괄1-중복"},
        {"holiday_date": d.isoformat(), "holiday_name": "이미있음"}]}, headers=h).json()
    # 5/5는 처음이면 신규 생성, 이전 실행에서 사용중지된 적이 있으면 되살림 — 어느 쪽이든 1건 반영
    assert bulk["exists"] == 1 and bulk["created"] + bulk["reactivated"] == 1

    # 삭제는 사용중지(되살릴 수 있음)
    assert client.delete(f"/api/v1/holidays/{hid}", headers=h).status_code == 204
    assert client.delete(f"/api/v1/holidays/{hid}", headers=h).status_code == 404
    assert [x["holiday_date"] for x in client.get("/api/v1/holidays", params={"year": 2031}, headers=h).json()] == ["2031-05-05"]
    again = client.post("/api/v1/holidays", json={"holiday_date": d.isoformat(), "holiday_name": "재등록"}, headers=h)
    assert again.status_code == 201 and again.json()["id"] == hid          # 새로 만들지 않고 되살림

    # 권한: 공휴일 수정은 재단 담당자만
    spc_id, _ = make_org(h)
    assert client.post("/api/v1/holidays", json={"holiday_date": "2031-06-02", "holiday_name": "x"},
                       headers=spc_manager_headers(h, spc_id)).status_code == 403
    for x in client.get("/api/v1/holidays", params={"year": 2031}, headers=h).json():
        client.delete(f"/api/v1/holidays/{x['id']}", headers=h)


# ---------------------------------------------------------------- 알림
def _foundation_user(h):
    email = f"fund-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/api/v1/users", json={"name": "재단자금담당", "email": email, "role": "FOUNDATION_FUND_MANAGER",
                                            "password": "fundpass123"}, headers=h)
    assert r.status_code == 201, r.text
    login = client.post("/api/v1/auth/login", data={"username": email, "password": "fundpass123"})
    return r.json()["id"], {"Authorization": f"Bearer {login.json()['access_token']}"}


def _setup_org_with_people(h):
    spc_id, dorm_id = make_org(h)
    fund_id, fund_h = _foundation_user(h)
    assert client.put(f"/api/v1/dormitories/{dorm_id}", json={"manager_user_id": fund_id}, headers=h).status_code == 200
    return spc_id, dorm_id, spc_manager_headers(h, spc_id), fund_h


def _holidays(h):
    return {date.fromisoformat(x["holiday_date"]) for x in client.get("/api/v1/holidays", headers=h).json()}


def _scan(h, as_of, dorm_id):
    r = client.post("/api/v1/notifications/scan", params={"as_of": as_of, "dormitory_id": dorm_id}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _inbox(hh, loan_id=None, **params):
    items = client.get("/api/v1/notifications", params={"limit": 200, **params}, headers=hh).json()
    return [n for n in items if loan_id is None or n["loan_id"] == loan_id]


def test_repayment_notifications_business_days_dedup_and_catchup():
    h = admin_headers()
    spc_id, dorm_id, spc_h, fund_h = _setup_org_with_people(h)
    other_spc_h = spc_manager_headers(h, make_org(h)[0])
    loan_id, _, _ = make_loan(h, dorm=dorm_id)            # 1억원, 분기, 원금균등 — 2023-05-31 상환일 500만원 + 이자
    hol = _holidays(h)
    due = date(2023, 5, 31)

    # ---- 1) D-N 알림: 영업일 기준으로 계산한 기준에 맞는 하나의 알림만
    as_of = date(2023, 5, 24)
    n = current_threshold(due, as_of, hol)
    assert n is not None
    res = _scan(h, as_of, dorm_id)
    assert res["created"]["repayment"] == 2                  # SPC 담당자 + 재단 담당자, 상환일 1개
    for hh in (spc_h, fund_h):
        mine = _inbox(hh, loan_id)
        assert [x["notification_type"] for x in mine] == [f"REPAYMENT_D{n}"]
        p = mine[0]["payload"]
        # §32 알림 내용: 기숙사명·차입금·재원·납부일·원금·이자·원리금·잔여원금
        assert p["fund"] == "공공자금관리기금" and p["due_date"] == "2023-05-31" and p["loan_code"]
        assert p["principal"] == 5_000_000 and p["interest"] == 756_164 and p["total"] == 5_756_164
        assert p["remaining_principal"] == 95_000_000 and p["dormitory_name"]
        assert "원금 5,000,000원" in mine[0]["body"] and mine[0]["channel"] == "IN_APP"
    assert _inbox(other_spc_h, loan_id) == []                # 다른 SPC 담당자에게는 가지 않는다

    # ---- 2) 같은 날 다시 실행해도 중복 없음
    assert _scan(h, as_of, dorm_id)["created"] == {"repayment": 0, "rate_change": 0}

    # ---- 3) 읽음 처리: 내 알림만
    nid = _inbox(spc_h, loan_id)[0]["id"]
    assert client.post(f"/api/v1/notifications/{nid}/read", headers=other_spc_h).status_code == 404
    assert client.post(f"/api/v1/notifications/{nid}/read", headers=spc_h).json()["is_read"] is True
    assert _inbox(spc_h, loan_id, unread_only=True) == []
    assert client.post("/api/v1/notifications/read-all", headers=fund_h).json()["marked"] >= 1

    # ---- 4) 더 임박한 기준이 도래하면 새 알림(D-1)
    n2 = current_threshold(due, date(2023, 5, 30), hol)
    assert n2 == 1 and n2 < n
    assert _scan(h, date(2023, 5, 30), dorm_id)["created"]["repayment"] == 2
    assert sorted(x["notification_type"] for x in _inbox(spc_h, loan_id)) == sorted([f"REPAYMENT_D{n}", "REPAYMENT_D1"])

    # ---- 5) 상환일이 지난 뒤에는 보내지 않는다
    assert _scan(h, date(2023, 6, 2), dorm_id)["created"]["repayment"] == 0


def test_catchup_sends_only_most_imminent_threshold():
    h = admin_headers()
    _, dorm_id, spc_h, fund_h = _setup_org_with_people(h)
    loan_id, _, _ = make_loan(h, dorm=dorm_id)
    # 배치가 오래 멈췄다가 상환일 하루 전에야 돌았다 → D-15/10/5/3이 한꺼번에 쏟아지면 안 되고 D-1 하나만
    assert _scan(h, date(2023, 5, 30), dorm_id)["created"]["repayment"] == 2
    assert [x["notification_type"] for x in _inbox(fund_h, loan_id)] == ["REPAYMENT_D1"]


def test_paid_due_dates_do_not_trigger_reminders():
    h = admin_headers()
    _, dorm_id, spc_h, fund_h = _setup_org_with_people(h)
    loan_id, _, _ = make_loan(h, dorm=dorm_id)
    row = next(r for r in client.get(f"/api/v1/loans/{loan_id}/payments/summary", params={"as_of": "2023-05-20"}, headers=h).json()["rows"]
               if r["due_date"] == "2023-05-31")
    assert client.post(f"/api/v1/loans/{loan_id}/payments", json={
        "due_date": "2023-05-31", "actual_paid_date": "2023-05-20", "actual_principal": row["scheduled_principal"],
        "actual_interest": row["scheduled_interest"]}, headers=h).status_code == 201
    assert _scan(h, date(2023, 5, 24), dorm_id)["created"]["repayment"] == 0     # 이미 납부 완료 → 알림 없음
    assert _inbox(spc_h, loan_id) == []


def test_rate_change_notification_d30():
    h = admin_headers()
    _, dorm_id, spc_h, fund_h = _setup_org_with_people(h)
    funds, cyc, mth = refs(h)

    def raw(rate_type, end):
        r = client.post("/api/v1/loans", json={
            "dormitory_id": dorm_id, "fund_id": funds["공공자금관리기금"]["id"], "loan_code": f"RC-{uuid.uuid4().hex[:8]}",
            "assigned_amount": 100_000_000, "assigned_date": "2023-02-28", "rate_type": rate_type,
            "repay_cycle_code_id": cyc["QUARTERLY"]["id"], "repay_method_code_id": mth["EQUAL_PRINCIPAL"]["id"],
            "principal_repay_start_date": "2023-05-31", "repay_period_months": 60,
            "initial_rate": {"effective_start_date": "2023-02-28", "effective_end_date": end, "annual_rate": "3.0"}}, headers=h)
        assert r.status_code == 201, r.text
        lid = r.json()["id"]
        assert client.post(f"/api/v1/loans/{lid}/schedule/generate", headers=h).status_code == 201
        return lid

    soon = raw("VARIABLE", "2023-06-15")        # 5/20 기준 26일 남음 → 알림 대상
    far = raw("VARIABLE", "2023-08-01")         # 73일 남음 → 아직 아님
    fixed = raw("FIXED", "2023-06-15")          # 고정금리는 금리변동 대상 아님

    as_of = date(2023, 5, 20)
    res = _scan(h, as_of, dorm_id)
    assert res["created"]["rate_change"] >= 1
    mine = [x for x in _inbox(fund_h) if x["notification_type"] == "RATE_CHANGE_D30"]
    assert {x["loan_id"] for x in mine} == {soon}
    p = mine[0]["payload"]
    # §33 알림 내용: 대상 기숙사·차입금·현재금리·변경예정일·변경예정금리·관련 상환회차·해당 원리금·확인 필요사항
    assert (p["current_rate"], p["change_date"], p["new_rate"], p["days_left"]) == (3.0, "2023-06-15", None, 26)
    assert p["related_due_date"] == "2023-08-31" and p["related_total"] > 0 and "고시" in p["check_items"]
    assert "미정" in mine[0]["body"] and p["dormitory_name"] and p["fund"]
    def rate_alerts(hh, lid):
        return [x for x in _inbox(hh, lid) if x["notification_type"] == "RATE_CHANGE_D30"]

    assert rate_alerts(spc_h, soon) == []       # 금리변동 알림은 재단 자금담당자에게만(§33) — 상환 알림은 별개로 받는다
    assert rate_alerts(fund_h, far) == [] and rate_alerts(fund_h, fixed) == []

    # 변경금리를 등록한 뒤 다시 돌려도 같은 건은 중복 발송하지 않는다
    assert client.post(f"/api/v1/loans/{soon}/rate-history", json={
        "effective_start_date": "2023-06-15", "annual_rate": "3.4"}, headers=h).status_code == 201
    assert _scan(h, as_of, dorm_id)["created"]["rate_change"] == 0


def test_alert_summary_for_dashboard_and_needs_attention():
    h = admin_headers()
    spc_id, dorm_id, spc_h, fund_h = _setup_org_with_people(h)
    loan_id, _, _ = make_loan(h, dorm=dorm_id)
    hol = _holidays(h)

    s = client.get("/api/v1/alerts/summary", params={"as_of": "2023-05-24", "dormitory_id": dorm_id}, headers=h).json()
    up = [i for i in s["repayment_upcoming"]["items"] if i["loan_id"] == loan_id]
    assert len(up) == 1 and up[0]["due_date"] == "2023-05-31"
    assert up[0]["threshold"] == f"D-{current_threshold(date(2023, 5, 31), date(2023, 5, 24), hol)}"
    assert up[0]["principal"] == 5_000_000 and up[0]["total"] == 5_756_164

    # 기한이 지났는데 납부가 없으면 미납
    late = client.get("/api/v1/alerts/summary", params={"as_of": "2023-07-01", "dormitory_id": dorm_id}, headers=h).json()
    assert late["overdue"]["count"] == 1 and late["overdue"]["amount"] == 5_756_164
    assert late["overdue"]["items"][0]["status"] == "UNPAID"

    # 확인 필요: 공휴일 미등록 연도(이 차입금의 향후 1년 상환일 기준)
    registered_years = {d.year for d in hol}
    assert s["needs_attention"]["holidays_not_registered_years"] == sorted({2023, 2024} - registered_years)
    assert s["needs_attention"]["not_covered"] == [] and s["needs_attention"]["midterm_pending"] == []   # 중도변수 미처리 점검 포함(2026-10-28 확정)

    # 확인 필요: 계산 오류 / 금리 미등록 / 잔여원금 불일치 — 문제를 일부러 만들어 감지되는지 확인
    bad_review, _, _ = make_loan(h, dorm=dorm_id)
    no_rate, _, _ = make_loan(h, dorm=dorm_id)
    broken, _, _ = make_loan(h, dorm=dorm_id)
    db = SessionLocal()
    try:
        v = db.query(RepaymentScheduleVersion).filter_by(loan_id=uuid.UUID(bad_review), is_current=True).one()
        v.status, v.validation_errors = ScheduleVersionStatus.NEEDS_REVIEW, ["테스트 오류"]
        for rh in db.query(InterestRateHistory).filter_by(loan_id=uuid.UUID(no_rate)):
            rh.is_deleted = True
        v = db.query(RepaymentScheduleVersion).filter_by(loan_id=uuid.UUID(broken), is_current=True).one()
        row = db.query(RepaymentScheduleRow).filter_by(version_id=v.id).order_by(RepaymentScheduleRow.seq_no).first()
        row.principal_amount += 1
        db.commit()
    finally:
        db.close()
    na = client.get("/api/v1/alerts/summary", params={"as_of": "2023-05-24", "dormitory_id": dorm_id}, headers=h).json()["needs_attention"]
    assert [x["loan_id"] for x in na["calculation_errors"]] == [bad_review] and na["calculation_errors"][0]["errors"] == ["테스트 오류"]
    assert [x["loan_id"] for x in na["rate_not_registered"]] == [no_rate]
    assert [x["loan_id"] for x in na["balance_mismatches"]] == [broken]
    assert na["count"] >= 3

    # 권한: SPC 담당자는 자기 SPC 차입금만 본다
    own = client.get("/api/v1/alerts/summary", params={"as_of": "2023-07-01"}, headers=spc_h).json()
    assert {i["loan_id"] for i in own["overdue"]["items"]} <= {loan_id, bad_review, no_rate, broken}
    assert loan_id in {i["loan_id"] for i in own["overdue"]["items"]}


def test_scan_and_inbox_permissions():
    h = admin_headers()
    spc_id, dorm_id, spc_h, fund_h = _setup_org_with_people(h)
    assert client.post("/api/v1/notifications/scan", params={"dormitory_id": dorm_id}, headers=spc_h).status_code == 403
    assert client.post("/api/v1/notifications/scan", params={"dormitory_id": dorm_id}, headers=fund_h).status_code == 200
    assert client.get("/api/v1/notifications").status_code == 401
    assert client.get("/api/v1/alerts/summary").status_code == 401
