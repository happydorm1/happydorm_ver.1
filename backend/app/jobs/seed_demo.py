"""
데모 데이터 — 화면 확인·시연용 가상 기숙사/차입금을 '빈 데모 DB'에 만든다. 실제 기숙사·학교와 무관한 가상 이름이다.
운영 DB에는 절대 실행하지 않는다(이미 차입금이 있으면 중단).

    DATABASE_URL=postgresql+psycopg://.../dormitory_demo python -m app.jobs.seed_demo --as-of 2026-10-28

실제 API를 그대로 호출해서 만든다(계산·이벤트·납부가 운영과 같은 경로를 탄다):
- 공공자금관리기금(변동·분기·원금균등)·주택도시기금(고정·반기·원리금균등)·월 상환 차입금
- 상환유예, 원금가속상환, 일부 조기상환, 영업일 30일 이내 금리변동 예정
- 지난 상환일은 납부기한에 맞춰 납부 처리하되, 미납 1건·일부납부 1건을 일부러 남긴다
- 공휴일은 넣지 않는다(서비스키로 동기화하는 대상) → '확인 필요: 공휴일 미등록'이 보이는 것이 정상
- 중도변수 미처리 예시 1건: 가람관 공자 차입금에 새 금리를 '이력'으로만 등록(금리변동 이벤트 미적용) → 로그인 팝업
- 역할별 계정: 관리자·자금담당·결재권자·SPC 담당자·조회자(다운로드 제한)
"""
import argparse
import sys
from datetime import date

from fastapi.testclient import TestClient

from app.core.database import SessionLocal, engine
from app.core.security import hash_password
from app.main import app
from app.models import calendar, document, event, notification, payment  # noqa: F401  (테이블 등록)
from app.models.base import Base
from app.models.loan import Loan
from app.models.organization import User, UserRole

ADMIN = ("demo-admin@example.com", "demo1234!")

# (기숙사, 학교, [차입금...])  — 모두 가상의 이름
DORMS = [
    ("가람관", "가람대학교", [
        dict(fund="공공자금관리기금", cycle="QUARTERLY", method="EQUAL_PRINCIPAL", amount=9_800_000_000, start="2017-08-21",
             grace=("2017-08-21", "2020-08-31"), first="2020-11-30", months=300,
             rates=[("2017-08-21", "2024-08-31", "2.21"), ("2024-08-31", None, "2.98")], rate_type="VARIABLE"),
        dict(fund="주택도시기금", cycle="SEMIANNUAL", method="EQUAL_PAYMENT", amount=14_200_000_000, start="2018-03-28",
             grace=("2018-03-28", "2021-02-28"), first="2021-08-31", months=324, rates=[("2018-03-28", None, "2.0")]),
    ]),
    ("누리학사", "누리대학교", [
        dict(fund="공공자금관리기금", cycle="QUARTERLY", method="EQUAL_PRINCIPAL", amount=6_400_000_000, start="2019-11-30",
             grace=("2019-11-30", "2021-11-30"), first="2022-02-28", months=300,
             rates=[("2019-11-30", "2026-11-30", "1.84")], rate_type="VARIABLE"),          # 금리 구간 종료 임박 → 금리변동 예정
    ]),
    ("다온생활관", "다온대학교", [
        dict(fund="주택도시기금", cycle="SEMIANNUAL", method="EQUAL_PAYMENT", amount=11_500_000_000, start="2016-09-30",
             grace=("2016-09-30", "2019-08-31"), first="2020-02-29", months=324, rates=[("2016-09-30", None, "2.0")]),
    ]),
    ("라온하우스", "라온대학교", [
        dict(fund="공공자금관리기금", cycle="QUARTERLY", method="EQUAL_PRINCIPAL", amount=4_300_000_000, start="2015-05-29",
             grace=("2015-05-29", "2018-05-31"), first="2018-08-31", months=300,
             rates=[("2015-05-29", "2022-05-31", "2.43"), ("2022-05-31", None, "2.61")], rate_type="VARIABLE"),
    ]),
    ("미르기숙사", "미르대학교", [
        dict(fund="공공자금관리기금", cycle="MONTHLY", method="EQUAL_PRINCIPAL", amount=2_400_000_000, start="2021-03-31",
             grace=None, first="2021-04-30", months=240, rates=[("2021-03-31", None, "1.95")]),  # 월 상환(10/31 토요일 → 납부기한 순연)
    ]),
    ("바름학사", "바름대학교", [
        dict(fund="주택도시기금", cycle="SEMIANNUAL", method="EQUAL_PAYMENT", amount=8_700_000_000, start="2019-12-30",
             grace=("2019-12-30", "2022-08-31"), first="2023-02-28", months=324, rates=[("2019-12-30", None, "1.572")]),
    ]),
    ("새봄관", "새봄대학교", [
        dict(fund="공공자금관리기금", cycle="QUARTERLY", method="EQUAL_PRINCIPAL", amount=5_600_000_000, start="2018-11-30",
             grace=("2018-11-30", "2020-11-30"), first="2021-02-28", months=300,
             rates=[("2018-11-30", "2025-11-30", "2.05"), ("2025-11-30", None, "2.74")], rate_type="VARIABLE"),
    ]),
    ("온새누리관", "온새대학교", [
        dict(fund="주택도시기금", cycle="SEMIANNUAL", method="EQUAL_PAYMENT", amount=6_900_000_000, start="2020-06-30",
             grace=("2020-06-30", "2023-02-28"), first="2023-08-31", months=324, rates=[("2020-06-30", None, "1.80")]),
    ]),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    as_of = ap.parse_args().as_of

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    if db.query(Loan).count():
        print("이 DB에는 이미 차입금이 있습니다. 데모 데이터는 빈 DB에만 만듭니다.", file=sys.stderr)
        sys.exit(1)
    from app.seed_reference import seed
    seed()
    if not db.query(User).filter(User.email == ADMIN[0]).first():
        db.add(User(name="데모관리자", email=ADMIN[0], role=UserRole.SYSTEM_ADMIN, hashed_password=hash_password(ADMIN[1])))
        db.commit()
    db.close()

    c = TestClient(app)
    tok = c.post("/api/v1/auth/login", data={"username": ADMIN[0], "password": ADMIN[1]}).json()["access_token"]
    H = {"Authorization": f"Bearer {tok}"}

    def ok(r, code=(200, 201)):
        if r.status_code not in code:
            raise SystemExit(f"실패 {r.request.method} {r.request.url}: {r.status_code} {r.text[:300]}")
        return r.json()

    funds = {f["fund_name"]: f["id"] for f in ok(c.get("/api/v1/reference/funds", headers=H))}
    cyc = {x["code"]: x["id"] for x in ok(c.get("/api/v1/reference/codes", params={"group_code": "REPAY_CYCLE"}, headers=H))}
    mth = {x["code"]: x["id"] for x in ok(c.get("/api/v1/reference/codes", params={"group_code": "REPAY_METHOD"}, headers=H))}
    manager = ok(c.post("/api/v1/users", json={"name": "자금담당(데모)", "email": "demo-fund@example.com",
                                                "role": "FOUNDATION_FUND_MANAGER", "password": "demo1234!"}, headers=H))

    loans: dict[str, str] = {}
    for i, (dname, school, specs) in enumerate(DORMS):
        spc = ok(c.post("/api/v1/spc", json={"spc_name": f"{dname}(주)"}, headers=H))["id"]
        dorm = ok(c.post("/api/v1/dormitories", json={"dormitory_name": dname, "school_name": school, "spc_id": spc,
                                                       "project_type": "행복기숙사(사립)", "manager_user_id": manager["id"]}, headers=H))["id"]
        if i == 0:
            ok(c.post("/api/v1/users", json={"name": f"{dname} SPC담당(데모)", "email": "demo-spc@example.com",
                                             "role": "SPC_MANAGER", "spc_id": spc, "password": "demo1234!"}, headers=H))
        for j, s in enumerate(specs):
            code = f"{s['start']}_{'공자' if s['fund'].startswith('공공') else '주택'}"
            first_rate = s["rates"][0]
            body = {"dormitory_id": dorm, "fund_id": funds[s["fund"]], "loan_code": f"{code}-{i}{j}",
                    "assigned_amount": s["amount"], "assigned_date": s["start"], "rate_type": s.get("rate_type", "FIXED"),
                    "repay_cycle_code_id": cyc[s["cycle"]], "repay_method_code_id": mth[s["method"]],
                    "principal_repay_start_date": s["first"], "repay_period_months": s["months"],
                    "initial_rate": {"effective_start_date": first_rate[0], "effective_end_date": first_rate[1],
                                     "annual_rate": first_rate[2], "rate_table_name": "데모 금리"}}
            if s["grace"]:
                body["grace_start_date"], body["grace_end_date"] = s["grace"]
            lid = ok(c.post("/api/v1/loans", json=body, headers=H))["id"]
            for st, en, rate in s["rates"][1:]:
                ok(c.post(f"/api/v1/loans/{lid}/rate-history", json={"effective_start_date": st, "effective_end_date": en,
                                                                     "annual_rate": rate}, headers=H))
            ok(c.post(f"/api/v1/loans/{lid}/schedule/generate", headers=H))
            loans[f"{dname}#{j}"] = lid

    # 중도변수(실제 이벤트 API)
    ok(c.post(f"/api/v1/loans/{loans['라온하우스#0']}/events/early-repayment",
              json={"event_date": "2024-05-31", "repay_type": "PARTIAL", "repay_amount": 200_000_000, "reason": "데모: 일부 조기상환"}, headers=H))
    ok(c.post(f"/api/v1/loans/{loans['다온생활관#0']}/events/acceleration",
              json={"event_date": "2025-08-01", "acceleration_multiplier": "1.5", "reason": "데모: 원금가속상환"}, headers=H))
    ok(c.post(f"/api/v1/loans/{loans['새봄관#0']}/events/deferral",
              json={"event_date": "2025-05-31", "defer_target": "PRINCIPAL_INTEREST", "recovery_cycle_months": 3,
                    "recovery_count": 4, "reason": "데모: 상환유예"}, headers=H))

    # 실제 납부: 납부기한이 지난 회차는 기한일에 예정액 그대로 납부. 단, 미납 1건·일부납부 1건을 남긴다.
    skip = {loans["바름학사#0"]: "UNPAID", loans["누리학사#0"]: "PARTIAL"}
    paid = 0
    for key, lid in loans.items():
        rows = ok(c.get(f"/api/v1/loans/{lid}/payments/summary", params={"as_of": as_of.isoformat()}, headers=H))["rows"]
        past = [r for r in rows if r["payment_deadline"] < as_of.isoformat()]
        for n, r in enumerate(past):
            last = n == len(past) - 1
            mode = skip.get(lid) if last else None
            if mode == "UNPAID":
                continue
            principal, interest = r["scheduled_principal"], r["scheduled_interest"]
            if mode == "PARTIAL":
                principal = principal // 2
            ok(c.post(f"/api/v1/loans/{lid}/payments", json={"due_date": r["due_date"], "actual_paid_date": r["payment_deadline"],
                                                             "actual_principal": principal, "actual_interest": interest,
                                                             "note": "데모 납부"}, headers=H))
            paid += 1

    # 중도변수 미처리 예시: 새 금리를 이력으로만 등록하고 금리변동 이벤트는 적용하지 않음
    ok(c.post(f"/api/v1/loans/{loans['가람관#0']}/rate-history",
              json={"effective_start_date": "2026-08-31", "annual_rate": "3.15", "rate_table_name": "데모 금리"}, headers=H))
    for name, email, role in (("결재권자(데모)", "demo-approver@example.com", "FOUNDATION_APPROVER"),
                              ("조회자(데모)", "demo-viewer@example.com", "VIEWER")):
        ok(c.post("/api/v1/users", json={"name": name, "email": email, "role": role, "password": "demo1234!",
                                         "can_download": role != "VIEWER"}, headers=H))

    ok(c.post("/api/v1/document-templates", json={"template_name": "데모 상환 안내공문",
                                                  "config": {"bank_account": "데모은행 000-000-000000", "account_holder": "데모기금"}}, headers=H))
    print(f"데모 데이터 생성 완료: 기숙사 {len(DORMS)}곳, 차입금 {len(loans)}건, 납부 {paid}건 (기준일 {as_of})")
    print(f"로그인(비밀번호 모두 {ADMIN[1]}): 관리자 {ADMIN[0]} · 자금담당 demo-fund@ · 결재권자 demo-approver@ · "
          "SPC 담당자 demo-spc@ · 조회자 demo-viewer@ (example.com)")


if __name__ == "__main__":
    main()
