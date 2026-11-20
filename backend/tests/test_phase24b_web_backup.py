"""웹에서 백업 내려받기·되돌리기 입력 검증(실제 되돌리기는 시험 DB를 비우므로 여기서는 하지 않는다 — E2E에서 확인)."""
from fastapi.testclient import TestClient

from app.jobs.transfer import MAGIC, read_meta
from app.main import app
from tests.test_phase23f_deposits import admin_headers

client = TestClient(app)


def test_web_backup_download_and_log():
    h = admin_headers()
    r = client.post("/api/v1/admin/backups/download", json={"password": "short"}, headers=h)
    assert r.status_code == 422
    r = client.post("/api/v1/admin/backups/download", json={"password": "backup-pass-1"}, headers=h)
    assert r.status_code == 200 and r.content.startswith(MAGIC)
    assert ".dlt" in r.headers["content-disposition"]
    assert read_meta(r.content, "backup-pass-1")["counts"]["loan"] >= 1
    b = client.get("/api/v1/admin/backups", headers=h).json()
    assert b["items"][0]["kind"] == "BACKUP" and b["items"][0]["status"] == "SUCCESS" and b["last_success_at"]
    # 백업이 있으면 '백업 확인 필요' 안내가 사라진다
    from app.core.database import SessionLocal
    from app.domain.popup_service import _backup_items
    with SessionLocal() as db:
        assert not [i for i in _backup_items(db) if i["kind"] == "BACKUP_STALE"]
    return r.content


def test_web_restore_guards():
    h = admin_headers()
    blob = client.post("/api/v1/admin/backups/download", json={"password": "backup-pass-1"}, headers=h).content
    files = {"file": ("b.dlt", blob, "application/octet-stream")}
    r = client.post("/api/v1/admin/backups/restore", files=files, data={"password": "backup-pass-1", "confirm": "네"}, headers=h)
    assert r.status_code == 422 and "되돌리기" in r.text
    r = client.post("/api/v1/admin/backups/restore", files=files, data={"password": "wrong-pass", "confirm": "되돌리기"}, headers=h)
    assert r.status_code == 422 and "비밀번호" in r.text
