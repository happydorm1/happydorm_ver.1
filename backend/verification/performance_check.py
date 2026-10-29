"""
Phase 15 성능 점검 — 실제 규모(행복기숙사 약 77개 사업) 이상의 가상 차입금을 빈 DB에 만들고 주요 화면·작업 응답시간을 잰다.
    createdb perf_check
    DATABASE_URL=.../perf_check python -m verification.performance_check --loans 90 --as-of 2026-10-28
빈 DB에만 실행된다(데모 데이터 생성기를 그대로 쓰되 규모를 키운다). 결과는 표준출력과 --out(JSON).
"""
import argparse
import json
import statistics
import time
import uuid
from datetime import date

from fastapi.testclient import TestClient

from app.core.database import SessionLocal, engine
from app.core.security import hash_password
from app.main import app
from app.models.base import Base
from app.models.loan import Loan
from app.models.organization import User, UserRole

SPECS = [
    dict(fund="공공자금관리기금", cycle="QUARTERLY", method="EQUAL_PRINCIPAL", months=300, grace_years=3, rate="2.21", rate_type="VARIABLE"),
    dict(fund="주택도시기금", cycle="SEMIANNUAL", method="EQUAL_PAYMENT", months=324, grace_years=3, rate="2.0", rate_type="FIXED"),
    dict(fund="공공자금관리기금", cycle="MONTHLY", method="EQUAL_PRINCIPAL", months=240, grace_years=0, rate="1.95", rate_type="FIXED"),
]


def _eom(y, m):
    import calendar
    return date(y, m, calendar.monthrange(y, m)[1])


def timed(fn, n=1):
    xs = []
    out = None
    for _ in range(n):
        t = time.perf_counter()
        out = fn()
        xs.append((time.perf_counter() - t) * 1000)
    return out, xs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loans", type=int, default=90)
    ap.add_argument("--as-of", type=date.fromisoformat, default=date(2026, 10, 28))
    ap.add_argument("--out")
    a = ap.parse_args()

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    if db.query(Loan).count():
        raise SystemExit("빈 DB에서만 실행합니다.")
    from app.seed_reference import seed
    seed()
    db.add(User(name="성능점검", email="perf@example.com", role=UserRole.SYSTEM_ADMIN, hashed_password=hash_password("Perf!Check2026")))
    db.commit()
    db.close()

    c = TestClient(app)
    H = {"Authorization": "Bearer " + c.post("/api/v1/auth/login", data={"username": "perf@example.com", "password": "Perf!Check2026"}).json()["access_token"]}
    funds = {f["fund_name"]: f["id"] for f in c.get("/api/v1/reference/funds", headers=H).json()}
    cyc = {x["code"]: x["id"] for x in c.get("/api/v1/reference/codes", params={"group_code": "REPAY_CYCLE"}, headers=H).json()}
    mth = {x["code"]: x["id"] for x in c.get("/api/v1/reference/codes", params={"group_code": "REPAY_METHOD"}, headers=H).json()}

    gen_ms, event_ms, loans, dorms = [], [], [], []
    t_seed = time.perf_counter()
    for i in range(a.loans):
        if i % 2 == 0:   # 기숙사 1곳당 차입금 평균 2건
            spc = c.post("/api/v1/spc", json={"spc_name": f"성능SPC-{i}"}, headers=H).json()["id"]
            dorm = c.post("/api/v1/dormitories", json={"dormitory_name": f"성능기숙사-{i // 2:03d}", "school_name": f"성능대학교{i // 2}",
                                                        "spc_id": spc}, headers=H).json()["id"]
            dorms.append(dorm)
        s = SPECS[i % len(SPECS)]
        y = 2012 + (i % 12)
        m = [2, 5, 8, 11][i % 4]
        start = _eom(y, m)
        g_end = _eom(y + s["grace_years"], m)
        first_m = (m + (3 if s["cycle"] == "QUARTERLY" else 6 if s["cycle"] == "SEMIANNUAL" else 1) - 1) % 12 + 1
        first_y = g_end.year + (1 if first_m <= m else 0)
        body = {"dormitory_id": dorm, "fund_id": funds[s["fund"]], "loan_code": f"PERF-{i:03d}", "assigned_amount": 2_000_000_000 + i * 97_000_000,
                "assigned_date": start.isoformat(), "rate_type": s["rate_type"], "repay_cycle_code_id": cyc[s["cycle"]],
                "repay_method_code_id": mth[s["method"]], "principal_repay_start_date": _eom(first_y, first_m).isoformat(),
                "repay_period_months": s["months"], "initial_rate": {"effective_start_date": start.isoformat(), "annual_rate": s["rate"]}}
        if s["grace_years"]:
            body["grace_start_date"], body["grace_end_date"] = start.isoformat(), g_end.isoformat()
        r = c.post("/api/v1/loans", json=body, headers=H)
        assert r.status_code == 201, r.text
        lid = r.json()["id"]
        r, ms = timed(lambda: c.post(f"/api/v1/loans/{lid}/schedule/generate", headers=H))
        assert r.status_code == 201, r.text
        gen_ms += ms
        loans.append(lid)
        # 일부 차입금에 중도변수
        if i % 5 == 1 and s["method"] == "EQUAL_PAYMENT":
            r, ms = timed(lambda: c.post(f"/api/v1/loans/{lid}/events/acceleration", json={"event_date": "2025-08-01"}, headers=H))
            event_ms += ms
        elif i % 5 == 3 and s["rate_type"] == "VARIABLE":
            r, ms = timed(lambda: c.post(f"/api/v1/loans/{lid}/events/rate-change", json={"effective_start_date": "2024-08-31", "annual_rate": "2.98"}, headers=H))
            event_ms += ms
        # 지난 회차 납부(마지막 1건은 미납으로 남김)
        rows = c.get(f"/api/v1/loans/{lid}/payments/summary", params={"as_of": a.as_of.isoformat()}, headers=H).json()["rows"]
        past = [x for x in rows if x["payment_deadline"] < a.as_of.isoformat()]
        for x in past[:-1] if i % 7 == 0 else past:
            c.post(f"/api/v1/loans/{lid}/payments", json={"due_date": x["due_date"], "actual_paid_date": x["payment_deadline"],
                                                        "actual_principal": x["scheduled_principal"], "actual_interest": x["scheduled_interest"]}, headers=H)
    seed_s = time.perf_counter() - t_seed
    c.post("/api/v1/document-templates", json={"template_name": "성능 공문", "config": {"bank_account": "검증은행 000-00-000000", "account_holder": "검증"}}, headers=H)

    asof = a.as_of.isoformat()
    res = {}

    def rec(name, fn, n=5):
        out, xs = timed(fn, n)
        res[name] = {"median_ms": round(statistics.median(xs)), "max_ms": round(max(xs)), "runs": n}
        return out

    d = rec("대시보드(전체, 필터 없음)", lambda: c.get("/api/v1/dashboard", params={"as_of": asof}, headers=H).json())
    rec("대시보드(재원+상태 필터)", lambda: c.get("/api/v1/dashboard", params={"as_of": asof, "fund_id": funds["주택도시기금"], "status": "ACTIVE"}, headers=H))
    rec("대시보드 검색조건 목록", lambda: c.get("/api/v1/dashboard/filter-options", headers=H))
    rec("로그인 알림 팝업", lambda: c.get("/api/v1/popup", params={"as_of": asof}, headers=H))
    rec("원리금 테이블 조회(1건)", lambda: c.get(f"/api/v1/loans/{loans[0]}/schedule", headers=H))
    rec("원리금 테이블 Excel 다운로드", lambda: c.get(f"/api/v1/loans/{loans[0]}/schedule/download", headers=H))
    rec("계산 검증 9개 항목(1건)", lambda: c.get(f"/api/v1/loans/{loans[0]}/schedule/verification", headers=H))
    rec("납부 현황(1건)", lambda: c.get(f"/api/v1/loans/{loans[0]}/payments/summary", params={"as_of": asof}, headers=H))
    rec("안내공문 미리보기(기숙사 1곳)", lambda: c.get(f"/api/v1/dormitories/{dorms[0]}/notices/preview", params={"due_date": "2026-11-30"}, headers=H))
    rec("일일 알림 배치(전체)", lambda: c.post("/api/v1/notifications/scan", params={"as_of": asof}, headers=H), n=2)
    from app.jobs.verify_all import run as verify_run
    v = rec("전체 계산 검증(현재 버전 전부)", lambda: verify_run(False), n=1)

    summary = {
        "규모": {"차입금": a.loans, "기숙사": len(dorms), "원리금 테이블 생성 중앙값_ms": round(statistics.median(gen_ms)),
               "원리금 테이블 생성 최대_ms": round(max(gen_ms)), "중도변수 적용 건수": len(event_ms),
               "중도변수 적용 중앙값_ms": round(statistics.median(event_ms)) if event_ms else None, "데이터 생성 총 초": round(seed_s, 1),
               "대시보드 KPI 차입금 수": d["kpis"]["loan_count"], "미납 건수": d["kpis"]["overdue_count"]},
        "응답시간": res,
        "계산검증": {"검증 버전 수": v["versions"], "검토 필요": len(v["needs_review"])},
    }
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
