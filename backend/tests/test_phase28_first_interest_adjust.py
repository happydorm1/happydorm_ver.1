"""
2026-11-29: 기존 엑셀 그대로 이관(인천 행복기숙사 사례)
- 첫 이자납입일: 2026-02-27 지급 → 2026-02-28(1일)을 건너뛰고 2026-08-31에 185일치(이자 일의 자리 절사)
- 회차 이자 조정: 엑셀이 연말 미분리로 계산한 회차(+1원) 등을 엑셀 금액 그대로 — 원금·잔액은 그대로
"""
import sys
import uuid
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_phase6_deferral import _admin_headers, client  # noqa: E402


def _make(h, *, amount, assigned, ge, pstart, irnd="ROUND", first=None, adjust=None):
    run = uuid.uuid4().hex[:8]
    code = lambda g, c: next(x for x in client.get("/api/v1/reference/codes", params={"group_code": g}, headers=h).json() if x["code"] == c)  # noqa: E731
    fund = next(f for f in client.get("/api/v1/reference/funds", headers=h).json() if f["fund_name"] == "주택도시기금")
    spc = client.post("/api/v1/spc", json={"spc_name": f"S{run}"}, headers=h).json()["id"]
    dorm = client.post("/api/v1/dormitories", json={"dormitory_name": f"인천-{run}", "school_name": "t", "spc_id": spc}, headers=h).json()["id"]
    r = client.post("/api/v1/loans", json={
        "dormitory_id": dorm, "fund_id": fund["id"], "loan_code": f"{assigned}_주택-{run}", "assigned_amount": amount,
        "assigned_date": assigned, "rate_type": "FIXED", "interest_rounding": irnd,
        "repay_cycle_code_id": code("REPAY_CYCLE", "SEMIANNUAL")["id"], "repay_method_code_id": code("REPAY_METHOD", "EQUAL_PAYMENT")["id"],
        "grace_start_date": assigned, "grace_end_date": ge, "principal_repay_start_date": pstart, "repay_period_months": 324,
        "first_interest_date": first, "interest_adjustments": adjust or [],
        "initial_rate": {"effective_start_date": assigned, "annual_rate": "1.57200"}}, headers=h)
    return r


def _billed(rows):
    out = {}
    for x in rows:
        if x["row_type"] != "YEAR_END_SPLIT_MEMO":
            out[x["due_date"]] = out.get(x["due_date"], 0) + x["interest_amount"]
    return out


def test_first_interest_date_skips_one_day_stub():
    h = _admin_headers()
    base = dict(amount=10_309_000_000, assigned="2026-02-27", ge="2029-02-28", pstart="2029-08-31", irnd="ROUNDDOWN10")
    r = _make(h, **base)
    rows = client.post(f"/api/v1/loans/{r.json()['id']}/schedule/generate", headers=h).json()["rows"]
    b = _billed(rows)
    assert (b["2026-02-28"], b["2026-08-31"]) == (443_990, 81_694_720)              # 기본: 지급 다음 날 1일치 따로
    r = _make(h, **base, first="2026-08-31")
    assert r.status_code == 201 and r.json()["first_interest_date"] == "2026-08-31"
    rows = client.post(f"/api/v1/loans/{r.json()['id']}/schedule/generate", headers=h).json()["rows"]
    b = _billed(rows)
    assert "2026-02-28" not in b and b["2026-08-31"] == 82_138_720                   # 185일치, 일의 자리 절사(엑셀)
    first = next(x for x in rows if x["due_date"] == "2026-08-31")
    assert (first["interest_calc_start"], first["days"]) == ("2026-02-27", 185)
    assert b["2027-02-28"] == 80_362_750 and len(b) == 60
    # 상환일이 아닌 날·거치기간 밖은 거부
    assert _make(h, **base, first="2026-07-31").status_code in (201, 422)
    bad = _make(h, **base, first="2026-07-31")
    if bad.status_code == 201:
        g = client.post(f"/api/v1/loans/{bad.json()['id']}/schedule/generate", headers=h)
        assert g.status_code in (400, 422) and "첫 이자납입일" in g.text
    assert _make(h, **base, first="2030-02-28").status_code == 422


def test_interest_adjustment_sets_billed_interest_only():
    h = _admin_headers()
    base = dict(amount=5_942_000_000, assigned="2023-06-30", ge="2026-02-28", pstart="2026-08-31")
    plain = client.post(f"/api/v1/loans/{_make(h, **base).json()['id']}/schedule/generate", headers=h).json()["rows"]
    assert _billed(plain)["2024-02-29"] == 46_576_163                                # 12/31에서 나눠 각각 반올림
    adj = [{"due_date": "2024-02-29", "interest": 46_576_164, "reason": "기존 엑셀(연말 미분리)"},
           {"due_date": "2025-02-28", "interest": 46_320_251}]
    r = _make(h, **base, adjust=adj)
    assert r.status_code == 201 and len(r.json()["interest_adjustments"]) == 2
    rows = client.post(f"/api/v1/loans/{r.json()['id']}/schedule/generate", headers=h).json()["rows"]
    b = _billed(rows)
    assert (b["2024-02-29"], b["2025-02-28"], b["2026-02-28"]) == (46_576_164, 46_320_251, 46_320_250)
    assert [x["balance_after"] for x in rows] == [x["balance_after"] for x in plain]  # 원금·잔액은 그대로
    assert any("이자 조정 +1원" in (x["remark"] or "") for x in rows if x["due_date"] == "2024-02-29")
    # 원리금 테이블에 없는 날짜는 생성 오류
    r = _make(h, **base, adjust=[{"due_date": "2024-03-31", "interest": 1}])
    g = client.post(f"/api/v1/loans/{r.json()['id']}/schedule/generate", headers=h)
    assert g.status_code in (400, 422) and "2024-03-31" in g.text
    _ = date


def test_migration_first_interest_and_adjust_sheet():
    from tests._helpers import admin_headers
    from tests.test_phase19_migration import _book, _find_loan, _loan_row, _send, _template
    h = admin_headers()
    wb = _template(h)
    assert "이자조정" in wb.sheetnames
    heads = [c.value for c in wb["차입금"][1]]
    assert heads[-1] == "첫이자납입일"
    run = uuid.uuid4().hex[:6]
    dorm = f"인천이관-{run}"
    row = _loan_row(dorm, f"IC-{run}", months=30, first="2024-02-29", assigned="2023-02-27", method="원금균등") + [None, "2023-08-31"]
    row[9], row[10], row[11] = None, None, "2023-08-31"
    content = _book(h, [[dorm, "인천대", f"SPC-{run}", "행복기숙사(연합)", None]], [row])
    from openpyxl import load_workbook
    import io
    wb2 = load_workbook(io.BytesIO(content))
    wb2["이자조정"].append([f"IC-{run}", "2023-08-31", 1_234_567, "테스트"])
    buf = io.BytesIO(); wb2.save(buf)
    r = _send(h, buf.getvalue(), action="apply", mark_paid=False)
    assert r.status_code == 200, r.text
    loan = client.get(f"/api/v1/loans/{_find_loan(h, f'IC-{run}')['id']}", headers=h).json()
    assert loan["first_interest_date"] == "2023-08-31"
    assert loan["interest_adjustments"] == [{"due_date": "2023-08-31", "interest": 1_234_567, "reason": "테스트"}]
