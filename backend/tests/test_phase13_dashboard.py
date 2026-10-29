"""Phase 13: 대시보드 API — KPI 정의(§34), 검색조건(§36), 권한"""
import uuid
from datetime import date

from _helpers import admin_headers, client, make_loan, make_org, refs, spc_manager_headers


def _dash(h, as_of, **params):
    r = client.get("/api/v1/dashboard", params={"as_of": as_of, **params}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _schedule_rows(h, loan_id):
    return client.get(f"/api/v1/loans/{loan_id}/schedule", headers=h).json()["rows"]


def test_kpis_match_hand_calculation_and_raw_schedule():
    h = admin_headers()
    _, dorm = make_org(h)
    # 1억원, 분기, 원금균등 20회(회당 500만원), 연 3%, 첫 상환 2023-05-31
    a, _, _ = make_loan(h, dorm=dorm)
    d = _dash(h, "2024-05-10", dormitory_id=dorm)
    k = d["kpis"]
    assert (k["dormitory_count"], k["loan_count"], k["total_assigned"]) == (1, 1, 100_000_000)
    # 기준일(2024-05-10)까지 지난 상환일: 2023-05-31, 08-31, 11-30, 2024-02-29 → 4회 × 500만
    assert k["total_remaining_principal"] == 100_000_000 - 4 * 5_000_000

    rows = _schedule_rows(h, a)
    def total_on(day):
        return sum(r["principal_amount"] + r["interest_amount"] + r["unpaid_interest_amount"] for r in rows if r["due_date"] == day)
    assert k["this_month_scheduled"] == total_on("2024-05-31")          # 5월 상환일
    assert k["next_month_scheduled"] == 0                                # 6월엔 상환일 없음
    year_rows = [r for r in rows if r["due_date"].startswith("2024-")]
    assert k["this_year_interest"] == sum(r["interest_amount"] + r["unpaid_interest_amount"] for r in year_rows)
    assert k["this_year_scheduled"] == 4 * 5_000_000 + k["this_year_interest"]

    # 월별 12개월: 기준월부터, 원금+이자=합계
    m = d["monthly_schedule"]
    assert [x["month"] for x in m][:3] == ["2024-05", "2024-06", "2024-07"] and len(m) == 12
    assert m[0]["total"] == total_on("2024-05-31") and m[0]["principal"] == 5_000_000
    assert all(x["principal"] + x["interest"] == x["total"] for x in m)

    loan = d["loans"][0]
    assert loan["next_due_date"] == "2024-05-31" and loan["next_due_total"] == total_on("2024-05-31")
    assert loan["current_rate"] == 3.0 and loan["repay_method"] == "원금균등" and loan["repay_cycle"] == "분기"
    assert loan["schedule_status"] == "NORMAL" and loan["remaining_principal"] == k["total_remaining_principal"]

    # 기간 상환예정액(선택)
    p = _dash(h, "2024-05-10", dormitory_id=dorm, period_from="2024-01-01", period_to="2024-06-30")["kpis"]
    assert p["period_scheduled"] == total_on("2024-02-29") + total_on("2024-05-31")
    assert "period_scheduled" not in k


def test_overdue_and_rate_change_kpis_follow_alert_rules():
    h = admin_headers()
    _, dorm = make_org(h)
    a, _, _ = make_loan(h, dorm=dorm)
    k = _dash(h, "2023-07-01", dormitory_id=dorm)["kpis"]
    assert k["overdue_count"] == 1                                         # 2023-05-31분 미납
    rows = _schedule_rows(h, a)
    first = sum(r["principal_amount"] + r["interest_amount"] for r in rows if r["due_date"] == "2023-05-31")
    assert k["overdue_amount"] == first


def test_filters_by_fund_rate_type_status_school_events_and_rate_change():
    h = admin_headers()
    funds, cyc, mth = refs(h)
    school = f"필터대학교-{uuid.uuid4().hex[:6]}"
    spc = client.post("/api/v1/spc", json={"spc_name": f"S-{uuid.uuid4().hex[:6]}"}, headers=h).json()["id"]
    dorm = client.post("/api/v1/dormitories", json={"dormitory_name": f"D-{uuid.uuid4().hex[:6]}", "school_name": school,
                                                    "spc_id": spc}, headers=h).json()["id"]
    gongja, _, _ = make_loan(h, dorm=dorm)                                   # 공공자금, 변동, 원금균등, 분기
    housing, _, _ = make_loan(h, dorm=dorm, fund="주택도시기금", cycle="SEMIANNUAL", method="EQUAL_PAYMENT",
                              first="2023-08-31", rate="2.0")
    base = {"dormitory_id": dorm}

    allr = _dash(h, "2024-01-15", **base)
    assert allr["kpis"]["loan_count"] == 2
    assert {x["fund"] for x in allr["by_fund"]} == {"공공자금관리기금", "주택도시기금"}
    assert sum(x["assigned"] for x in allr["by_fund"]) == allr["kpis"]["total_assigned"]

    only = lambda **p: [x["loan_id"] for x in _dash(h, "2024-01-15", **base, **p)["loans"]]
    assert only(fund_id=funds["주택도시기금"]["id"]) == [housing]
    assert only(repay_method_code_id=mth["EQUAL_PAYMENT"]["id"]) == [housing]
    assert only(repay_cycle_code_id=cyc["QUARTERLY"]["id"]) == [gongja]
    assert set(only(school=school[:6])) == {gongja, housing}                 # 학교명 부분일치
    assert only(school="없는학교명") == []

    # 중도변수 여부: 조기상환 적용 후 그 차입금만
    assert only(has_midterm_event=True) == []
    assert client.post(f"/api/v1/loans/{gongja}/events/early-repayment", json={
        "event_date": "2023-11-30", "repay_type": "PARTIAL", "repay_amount": 10_000_000}, headers=h).status_code == 201
    assert only(has_midterm_event=True) == [gongja] and only(has_midterm_event=False) == [housing]

    # 금리변동 여부: 금리 이력이 2건 이상
    assert only(has_rate_change=True) == []
    assert client.post(f"/api/v1/loans/{housing}/rate-history", json={
        "effective_start_date": "2030-08-31", "annual_rate": "2.5"}, headers=h).status_code == 201
    assert only(has_rate_change=True) == [housing]
    row = next(x for x in _dash(h, "2024-01-15", **base)["loans"] if x["loan_id"] == housing)
    assert row["has_rate_change"] is True and row["current_rate"] == 2.0      # 미래 금리는 아직 미적용

    # 상태: 종료 처리한 차입금은 '진행중' 조건에서 빠지고, 업무 알림에서도 빠진다
    assert client.put(f"/api/v1/loans/{housing}", json={"status": "CLOSED", "change_reason": "테스트"}, headers=h).status_code == 200
    assert only(status="ACTIVE") == [gongja] and only(status="CLOSED") == [housing]
    assert set(only(status=["ACTIVE", "CLOSED"])) == {gongja, housing}
    alerts = _dash(h, "2024-01-15", **base)["alerts"]
    assert all(i["loan_id"] != housing for i in alerts["overdue"]["items"])


def test_permissions_period_validation_and_filter_options():
    h = admin_headers()
    spc_a, dorm_a = make_org(h)
    _, dorm_b = make_org(h)
    loan_a, _, _ = make_loan(h, dorm=dorm_a)
    make_loan(h, dorm=dorm_b)
    spc_h = spc_manager_headers(h, spc_a)

    mine = _dash(spc_h, "2024-01-15")
    assert [x["loan_id"] for x in mine["loans"]] == [loan_a] and mine["kpis"]["dormitory_count"] == 1
    assert _dash(spc_h, "2024-01-15", dormitory_id=dorm_b)["kpis"]["loan_count"] == 0   # 다른 SPC는 조건으로도 못 봄

    opts = client.get("/api/v1/dashboard/filter-options", headers=spc_h).json()
    assert [x["id"] for x in opts["dormitories"]] == [dorm_a] and [x["id"] for x in opts["spcs"]] == [spc_a]
    assert {x["label"] for x in opts["repay_cycles"]} >= {"분기", "반기"} and len(opts["funds"]) >= 2

    assert client.get("/api/v1/dashboard", params={"period_from": "2024-01-01"}, headers=h).status_code == 422
    assert client.get("/api/v1/dashboard", params={"period_from": "2024-02-01", "period_to": "2024-01-01"}, headers=h).status_code == 422
    assert client.get("/api/v1/dashboard").status_code == 401
