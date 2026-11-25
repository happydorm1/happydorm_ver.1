"""2026-11-25: 발송완료 조회 결과 전체 안내문 일괄 내려받기(Excel 시트별 / PDF 한 파일)."""
import io
import uuid
from datetime import date

from openpyxl import load_workbook

from _helpers import admin_headers, client, make_loan, make_org

SEED = "_bulk_" + uuid.uuid4().hex[:6]


def _issue(h, dorm, due):
    t = client.post("/api/v1/document-templates", headers=h,
                    json={"template_name": f"양식{SEED}{uuid.uuid4().hex[:4]}", "config": {"bank_account": "농협 301-0000-1111-22", "account_holder": "재단"}})
    assert t.status_code in (200, 201), t.text
    r = client.post(f"/api/v1/dormitories/{dorm}/notices", headers=h, json={"due_date": due, "template_id": t.json()["id"]})
    assert r.status_code in (200, 201), r.text
    return r.json()


def _schedule_due(h, loan_id):
    rows = client.get(f"/api/v1/loans/{loan_id}/schedule", headers=h).json()["rows"]
    return [r["due_date"] for r in rows]


def test_bulk_xlsx_and_pdf():
    h = admin_headers()
    _, d1 = make_org(h)
    _, d2 = make_org(h)
    l1, _, _ = make_loan(h, dorm=d1)
    l2, _, _ = make_loan(h, dorm=d2)
    n1 = _issue(h, d1, _schedule_due(h, l1)[0])
    n2 = _issue(h, d2, _schedule_due(h, l2)[1])
    ids = [n1["id"], n2["id"]]

    r = client.post("/api/v1/notices/bulk-download", headers=h, json={"ids": ids, "format": "xlsx", "label": "테스트 조회"})
    assert r.status_code == 200, r.text
    assert "2%EA%B1%B4" in r.headers["content-disposition"]           # '2건'
    wb = load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames[0] == "목록" and len(wb.sheetnames) == 3
    ws = wb["목록"]
    assert ws["A2"].value == "테스트 조회"
    assert [ws.cell(4, c).value for c in range(1, 11)][:6] == ["No", "상환예정일", "납부기한", "기숙사", "학교", "안내문"]
    assert ws["F5"].value == n1["title"] and ws["F6"].value == n2["title"]
    assert ws["I5"].value == "미수신"
    assert ws["G5"].value == n1["generated_data"]["totals"]["total"]
    # 안내문 시트는 개별 Excel과 같은 내용(제목 셀)
    single = load_workbook(io.BytesIO(client.get(f"/api/v1/notices/{ids[0]}/download", headers=h).content))["Sheet1"]
    s1 = wb[wb.sheetnames[1]]
    assert [[c.value for c in row] for row in s1.iter_rows(max_row=12)] == [[c.value for c in row] for row in single.iter_rows(max_row=12)]

    r = client.post("/api/v1/notices/bulk-download", headers=h, json={"ids": ids, "format": "pdf"})
    assert r.status_code == 200, r.text
    assert r.content[:4] == b"%PDF"
    assert r.content.count(b"/Type /Page\n") + r.content.count(b"/Type /Page ") + r.content.count(b"/Type /Page>") >= 2 or b"/Count 2" in r.content


def test_bulk_rejects_unknown_and_bad_format():
    h = admin_headers()
    r = client.post("/api/v1/notices/bulk-download", headers=h, json={"ids": [str(uuid.uuid4())], "format": "xlsx"})
    assert r.status_code == 404
    r = client.post("/api/v1/notices/bulk-download", headers=h, json={"ids": [str(uuid.uuid4())], "format": "csv"})
    assert r.status_code == 422
    r = client.post("/api/v1/notices/bulk-download", headers=h, json={"ids": [], "format": "xlsx"})
    assert r.status_code == 422
    _ = date


def test_bulk_respects_download_permission_and_spc_scope():
    from test_phase14_security import _login, _user
    h = admin_headers()
    spc, dorm = make_org(h)
    loan, _, _ = make_loan(h, dorm=dorm)
    nid = _issue(h, dorm, _schedule_due(h, loan)[0])["id"]
    viewer = _login(_user(h, "VIEWER", can_download=False)[1])  # 다운로드 허용 안 된 조회자
    assert client.post("/api/v1/notices/bulk-download", headers=viewer, json={"ids": [nid]}).status_code == 403
    other_spc, _ = make_org(h)
    outsider = _login(_user(h, "SPC_MANAGER", other_spc)[1])     # 다른 SPC 담당자 → 보이지 않는 안내문
    assert client.post("/api/v1/notices/bulk-download", headers=outsider, json={"ids": [nid]}).status_code in (403, 404)
    mine = _login(_user(h, "SPC_MANAGER", spc)[1])               # 자기 기숙사 SPC가 받으면 수신 처리
    assert client.post("/api/v1/notices/bulk-download", headers=mine, json={"ids": [nid]}).status_code == 200
    assert client.get(f"/api/v1/notices/{nid}", headers=h).json()["received_at"]
