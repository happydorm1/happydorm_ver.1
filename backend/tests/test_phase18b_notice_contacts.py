"""Phase 18b — 공문 수신처·연락처, 발급 시 SPC 담당자 시스템 알림, PDF 변환기 찾기."""
import uuid
from pathlib import Path

import pytest

from _helpers import admin_headers, client, make_loan, make_org, spc_manager_headers
from app.core.config import settings
from app.engine import notice_export

ACCOUNT = {"bank_account": "가나은행 123-456-789012", "account_holder": "재단"}


def _template(h):
    r = client.post("/api/v1/document-templates", json={"template_name": f"t-{uuid.uuid4().hex[:6]}", "config": ACCOUNT}, headers=h)
    assert r.status_code == 201, r.text


def test_contacts_crud_primary_deactivate_and_permissions():
    h = admin_headers()
    spc_a, dorm_a = make_org(h)
    spc_b, dorm_b = make_org(h)
    url = f"/api/v1/dormitories/{dorm_a}/contacts"

    a = client.post(url, json={"name": "김담당", "organization": "가람대학교", "email": "kim@example.com", "is_primary": True}, headers=h)
    assert a.status_code == 201, a.text
    b = client.post(url, json={"name": "이담당", "email": "lee@example.com", "is_primary": True}, headers=h).json()
    rows = {c["name"]: c for c in client.get(url, headers=h).json()}
    assert rows["이담당"]["is_primary"] and not rows["김담당"]["is_primary"]          # 주 수신처는 1명
    assert list(rows)[0] == "이담당"                                                     # 주 수신처가 먼저

    assert client.post(url, json={"name": "x", "email": "메일아님"}, headers=h).status_code == 422
    up = client.put(f"/api/v1/contacts/{a.json()['id']}", json={"name": "김담당", "email": "kim2@example.com", "phone": "02-000-0000"}, headers=h)
    assert up.status_code == 200 and up.json()["email"] == "kim2@example.com"

    assert client.post(f"/api/v1/contacts/{b['id']}/deactivate", json={}, headers=h).status_code == 422      # 사유 필수
    off = client.post(f"/api/v1/contacts/{b['id']}/deactivate", json={"reason": "퇴사"}, headers=h).json()
    assert off["is_active"] is False and off["is_primary"] is False
    assert [c["name"] for c in client.get(url, headers=h).json()] == ["김담당"]
    assert len(client.get(url, params={"include_inactive": "true"}, headers=h).json()) == 2

    logs = client.get("/api/v1/admin/audit-logs", params={"table_name": "dormitory_contact"}, headers=h).json()
    text = str(logs)
    assert "퇴사" in text and "kim2@example.com" in text

    # SPC 담당자: 자기 기숙사 연락처는 조회, 다른 SPC는 403, 변경은 불가
    spc_h = spc_manager_headers(h, spc_a)
    assert client.get(url, headers=spc_h).status_code == 200
    assert client.get(f"/api/v1/dormitories/{dorm_b}/contacts", headers=spc_h).status_code == 403
    assert client.post(url, json={"name": "x"}, headers=spc_h).status_code == 403


def test_issue_notifies_spc_managers_once_per_due_date():
    h = admin_headers()
    _template(h)
    spc, dorm = make_org(h)
    make_loan(h, dorm=dorm)
    spc_h = spc_manager_headers(h, spc)

    r = client.post(f"/api/v1/dormitories/{dorm}/notices", json={"due_date": "2023-05-31"}, headers=h)
    assert r.status_code == 201, r.text
    assert r.json()["notified_users"] == 1

    def mine():
        return [n for n in client.get("/api/v1/notifications", headers=spc_h).json() if n["notification_type"] == "NOTICE_ISSUED"]

    got = mine()
    assert len(got) == 1 and got[0]["title"].startswith("[안내공문]") and "납부기한" in got[0]["body"]
    assert client.post(f"/api/v1/notifications/{got[0]['id']}/read", headers=spc_h).status_code in (200, 204)

    # 같은 상환일 공문을 다시 발급하면 알림이 늘지 않고 새 내용으로 갱신·안 읽음으로
    again = client.post(f"/api/v1/dormitories/{dorm}/notices", json={"due_date": "2023-05-31"}, headers=h)
    assert again.status_code == 201
    got = mine()
    assert len(got) == 1 and got[0]["is_read"] is False

    # 목록·단건 조회 응답에는 알림 수가 기본값 0(발급 직후 응답에만 의미)
    assert all(n["notified_users"] == 0 for n in client.get(f"/api/v1/dormitories/{dorm}/notices", headers=h).json())


def test_find_soffice_setting_path_and_missing(monkeypatch, tmp_path):
    fake = tmp_path / "soffice.exe"
    fake.write_text("x")
    monkeypatch.setattr(settings, "LIBREOFFICE_PATH", str(fake))
    assert notice_export.find_soffice() == str(fake)
    monkeypatch.setattr(settings, "LIBREOFFICE_PATH", str(tmp_path / "없음.exe"))
    assert notice_export.find_soffice() is None
    with pytest.raises(notice_export.PdfUnavailableError) as e:
        notice_export.convert_xlsx_to_pdf(b"x")
    assert "LibreOffice" in str(e.value) and "Excel" in str(e.value)


@pytest.mark.skipif(notice_export.find_soffice() is None, reason="이 환경에 LibreOffice 없음")
def test_notice_pdf_download_when_libreoffice_installed():
    h = admin_headers()
    _template(h)
    _, dorm = make_org(h)
    make_loan(h, dorm=dorm)
    nid = client.post(f"/api/v1/dormitories/{dorm}/notices", json={"due_date": "2023-05-31"}, headers=h).json()["id"]
    r = client.get(f"/api/v1/notices/{nid}/download", params={"format": "pdf"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf" and r.content[:4] == b"%PDF"
    assert Path(notice_export.find_soffice()).exists()
