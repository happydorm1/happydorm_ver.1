"""
원금 끝수처리(2026-11-28) — 이자 끝수처리와 같은 3가지(반올림·원 미만 / 절사·원 미만 / 절사·일의 자리).
원금균등의 회차당 원금 = 배정액 ÷ 회차수 에 적용. 홍제 2013-09-30_주택·2014-03-31_주택 수작업 테이블 재현
(거치기간 중 원금상환 2014-08-31 + 324개월 + 원금 일의 자리 절사 → 모든 상환일 원 단위 일치를 개발 중 원본으로 확인).
"""
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engine.schedule_engine import generate_equal_principal_schedule  # noqa: E402
from tests.test_phase6_deferral import _admin_headers, client  # noqa: E402


@pytest.mark.parametrize("mode,expect", [("ROUND", 64_814_815), ("ROUNDDOWN", 64_814_814), ("ROUNDDOWN10", 64_814_810)])
def test_engine_principal_rounding_modes(mode, expect):
    from datetime import date
    rows = generate_equal_principal_schedule(
        balance_start=3_500_000_000, assigned_amount=3_500_000_000, first_due_date=date(2017, 2, 28),
        total_installments=54, months_per_period=6, rate_lookup=lambda d: 2.0, period_start_date=date(2016, 8, 31),
        principal_rounding=mode)
    real = [r for r in rows if r.principal_amount]
    assert real[0].principal_amount == expect
    assert sum(r.principal_amount for r in real) == 3_500_000_000      # 마지막 회차 자동보정


def _loan(h, amount, start, pstart, ge, grace_pay, p_rounding):
    run = uuid.uuid4().hex[:8]
    code = lambda g, c: next(x for x in client.get("/api/v1/reference/codes", params={"group_code": g}, headers=h).json() if x["code"] == c)  # noqa: E731
    fund = next(f for f in client.get("/api/v1/reference/funds", headers=h).json() if f["fund_name"] == "주택도시기금")
    spc = client.post("/api/v1/spc", json={"spc_name": f"S{run}"}, headers=h).json()["id"]
    dorm = client.post("/api/v1/dormitories", json={"dormitory_name": f"홍제-{run}", "school_name": "t", "spc_id": spc}, headers=h).json()["id"]
    r = client.post("/api/v1/loans", json={
        "dormitory_id": dorm, "fund_id": fund["id"], "loan_code": f"{start}_주택-{run}", "assigned_amount": amount,
        "assigned_date": start, "rate_type": "FIXED", "principal_rounding": p_rounding,
        "repay_cycle_code_id": code("REPAY_CYCLE", "SEMIANNUAL")["id"], "repay_method_code_id": code("REPAY_METHOD", "EQUAL_PRINCIPAL")["id"],
        "grace_start_date": start, "grace_end_date": ge, "principal_repay_start_date": pstart, "repay_period_months": 324,
        "grace_principal_payments": [{"due_date": "2014-08-31", "amount": grace_pay}],
        "initial_rate": {"effective_start_date": start, "annual_rate": "2.00000"}}, headers=h)
    assert r.status_code == 201, r.text
    assert r.json()["principal_rounding"] == p_rounding
    rows = client.post(f"/api/v1/loans/{r.json()['id']}/schedule/generate", headers=h).json()["rows"]
    return [x for x in rows if x["row_type"] != "YEAR_END_SPLIT_MEMO"]


@pytest.mark.parametrize("amount,start,pstart,ge,grace_pay,ppi,last,interest_total", [
    (3_500_000_000, "2013-09-30", "2017-02-28", "2016-08-31", 112_305_001, 64_814_810, ("2043-02-28", 17_324_879), 1_102_690_068),
    (1_414_305_000, "2014-03-31", "2017-08-31", "2017-02-28", 45_381_007, 26_190_830, ("2043-08-31", 7_000_833), 445_177_020),
])
def test_hongje_tables_with_principal_rounddown10(amount, start, pstart, ge, grace_pay, ppi, last, interest_total):
    h = _admin_headers()
    rows = _loan(h, amount, start, pstart, ge, grace_pay, "ROUNDDOWN10")
    pr = [(x["due_date"], x["principal_amount"]) for x in rows if x["principal_amount"]]
    assert pr[0] == ("2014-08-31", grace_pay)
    assert {p for _, p in pr[1:-1]} == {ppi}
    assert pr[-1] == last
    assert sum(x["interest_amount"] + x["unpaid_interest_amount"] for x in rows) == interest_total
    # 기본(반올림)이면 회차당 원금이 원 단위 → 이자 총액이 수십 원 달라진다(예전 결과)
    rows_r = _loan(h, amount, start, pstart, ge, grace_pay, "ROUND")
    assert sum(x["interest_amount"] + x["unpaid_interest_amount"] for x in rows_r) != interest_total


def test_migration_sheet_principal_rounding_column():
    """이관 양식 '차입금' 시트 맨 끝 '원금끝수처리' — 비우면 반올림, '절사(일의 자리)'면 ROUNDDOWN10, 잘못 적으면 행 오류."""
    from tests._helpers import admin_headers
    from tests.test_phase19_migration import _book, _find_loan, _loan_row, _send
    h = admin_headers()
    run = uuid.uuid4().hex[:6]
    dorm = f"원금끝수-{run}"
    rows = [_loan_row(dorm, f"PR-{run}-1", amount=100_000_007, months=30) + ["절사(일의 자리)"],
            _loan_row(dorm, f"PR-{run}-2", amount=100_000_007, months=30) + [None]]
    content = _book(h, [[dorm, "끝수대", f"SPC-{run}", None, None]], rows, rates=[], excepts=[])
    r = _send(h, content, action="apply", mark_paid=False)
    assert r.status_code == 200, r.text
    a, b = _find_loan(h, f"PR-{run}-1"), _find_loan(h, f"PR-{run}-2")
    assert client.get(f"/api/v1/loans/{a['id']}", headers=h).json()["principal_rounding"] == "ROUNDDOWN10"
    assert client.get(f"/api/v1/loans/{b['id']}", headers=h).json()["principal_rounding"] == "ROUND"
    bad = _book(h, [[f"{dorm}x", "끝수대", f"SPC-{run}x", None, None]],
                [_loan_row(f"{dorm}x", f"PR-{run}-3") + ["버림"]], rates=[], excepts=[])
    r = _send(h, bad)
    assert "원금끝수처리" in r.text


def test_migration_project_type_list_and_grace_start_default():
    """2026-11-29: 기숙사 시트 '사업유형' 목록 선택(행복기숙사(연합)/(사립)/에듀21/기숙사형 청년주택), 차입금 시트 '거치시작일' 비우면 배정일."""
    import io
    from openpyxl import load_workbook
    from tests._helpers import admin_headers
    from tests.test_phase19_migration import _book, _find_loan, _loan_row, _send, _template
    h = admin_headers()
    wb = _template(h)
    heads = [c.value for c in wb["차입금"][1]]
    k = heads.index("거치시작일") + 1
    assert k == 11 and "배정일" in wb["차입금"].cell(1, k).comment.text           # K1 안내 메모
    lists = wb["목록"]
    col = next(c.column for c in lists[1] if c.value == "사업유형")
    assert [lists.cell(r, col).value for r in range(2, 7)] == ["행복기숙사(연합)", "행복기숙사(사립)", "에듀21", "기숙사형 청년주택", None]
    dvs = [str(dv.sqref) for dv in wb["기숙사"].data_validations.dataValidation]
    assert any(x.startswith("D2") for x in dvs)                                    # 사업유형(D열) 드롭다운

    run = uuid.uuid4().hex[:6]
    dorm = f"사업유형-{run}"
    row = _loan_row(dorm, f"PT-{run}", months=30, first="2023-11-30")
    row[9], row[10], row[11] = None, None, "2023-08-31"                             # 거치유형·거치시작일 비움, 거치종료일만
    r = _send(h, _book(h, [[dorm, "유형대", f"SPC-{run}", "행복기숙사(연합)", None]], [row]), action="apply", mark_paid=False)
    assert r.status_code == 200, r.text
    loan = client.get(f"/api/v1/loans/{_find_loan(h, f'PT-{run}')['id']}", headers=h).json()
    assert (loan["grace_start_date"], loan["grace_end_date"]) == ("2023-02-28", "2023-08-31")   # 배정일 2023-02-28

    bad = _book(h, [[f"{dorm}x", "유형대", f"SPC-{run}x", "청년주택", None]], [_loan_row(f"{dorm}x", f"PT-{run}-x")])
    r = _send(h, bad)
    assert "목록에 없는 사업유형" in r.text
    _ = (io, load_workbook)
