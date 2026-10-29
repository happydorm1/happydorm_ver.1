"""Phase 14: 권한(전 API 자동 점검)·로그인 잠금·비밀번호 정책·토큰 무효화·접근기록·민감정보 암호화·보안 헤더·결재 분리·백업"""
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from _helpers import admin_headers, client, make_loan, make_org, spc_manager_headers
from fastapi.routing import APIRoute
from sqlalchemy import select, text

import app.jobs.backup as bk
from app.core.config import settings
from app.core.crypto import decrypt_str, is_encrypted, mask_account
from app.core.database import SessionLocal
from app.core.password_policy import password_problems
from app.core.startup_checks import production_problems
from app.main import app
from app.models.security import AccessLog, BackupLog

PW = "Secure!2345"


def _routes(routes=None, prefix=""):
    for r in routes if routes is not None else app.routes:
        if isinstance(r, APIRoute):
            yield prefix + r.path, r
        elif hasattr(r, "original_router"):
            yield from _routes(r.original_router.routes, prefix + r.include_context.prefix)


def _user(h, role, spc_id=None, **extra):
    email = f"{role.lower()}-{uuid.uuid4().hex[:8]}@example.com"
    body = {"name": role, "email": email, "role": role, "password": PW, **extra}
    if spc_id:
        body["spc_id"] = spc_id
    r = client.post("/api/v1/users", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"], email


def _login(email, pw=PW):
    r = client.post("/api/v1/auth/login", data={"username": email, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# 역할과 무관하게 로그인한 사용자 누구나 쓸 수 있는 변경 API(본인 관련)
SELF_SERVICE = {"/api/v1/auth/login", "/api/v1/auth/change-password", "/api/v1/popup/ack",
                "/api/v1/notifications/{notification_id}/read", "/api/v1/notifications/read-all"}
APPROVAL_ACTIONS = {"/api/v1/event-requests/{request_id}/approve", "/api/v1/event-requests/{request_id}/reject",
                    "/api/v1/event-requests/{request_id}/withdraw"}
ZERO = "00000000-0000-0000-0000-000000000000"


def _fill(path):
    import re
    return re.sub(r"\{[^}]+\}", ZERO, path)


def test_every_write_api_is_denied_to_read_only_roles():
    """새 API가 생겨도 자동으로 점검: 조회자·SPC 담당자는 어떤 변경 API도 쓸 수 없다. 결재권자는 결재만."""
    h = admin_headers()
    spc, _ = make_org(h)
    viewer = _login(_user(h, "VIEWER")[1])
    spc_h = _login(_user(h, "SPC_MANAGER", spc)[1])
    approver = _login(_user(h, "FOUNDATION_APPROVER")[1])
    checked = 0
    for path, r in _routes():
        for m in r.methods - {"GET", "HEAD"}:
            if path in SELF_SERVICE:
                continue
            url = _fill(path)
            for who, hdr in (("VIEWER", viewer), ("SPC", spc_h)):
                res = client.request(m, url, headers=hdr, json={})
                assert res.status_code == 403, f"{who} {m} {path} → {res.status_code}"
            if path not in APPROVAL_ACTIONS:
                res = client.request(m, url, headers=approver, json={})
                assert res.status_code == 403, f"APPROVER {m} {path} → {res.status_code}"
            checked += 1
    assert checked >= 30


def test_spc_manager_cannot_read_other_spc_data_on_any_id_route():
    h = admin_headers()
    spc_a, _ = make_org(h)
    spc_b, dorm_b = make_org(h)
    loan_b, _, _ = make_loan(h, dorm=dorm_b)
    tpl = client.post("/api/v1/document-templates", json={"template_name": f"t-{uuid.uuid4().hex[:6]}",
                                                         "config": {"bank_account": "가나은행 123-456-789012", "account_holder": "재단"}}, headers=h).json()
    notice = client.post(f"/api/v1/dormitories/{dorm_b}/notices", json={"template_id": tpl["id"], "due_date": "2023-05-31"}, headers=h).json()
    pay = client.post(f"/api/v1/loans/{loan_b}/payments", json={"due_date": "2023-05-31", "actual_paid_date": "2023-05-31",
                                                                "actual_principal": 1, "actual_interest": 0}, headers=h).json()
    ids = {"loan_id": loan_b, "dormitory_id": dorm_b, "notice_id": notice["id"], "spc_id": spc_b, "payment_id": pay["id"]}
    spc_h = spc_manager_headers(h, spc_a)
    params = {"as_of_end_date": "2023-05-31", "from_version": 1, "to_version": 1, "due_date": "2023-05-31"}
    checked = 0
    for path, r in _routes():
        if "GET" not in r.methods or not any("{" + k + "}" in path for k in ids):
            continue
        url = path
        for k, v in ids.items():
            url = url.replace("{" + k + "}", str(v))
        if "{" in url:
            continue
        res = client.get(url, headers=spc_h, params=params)
        assert res.status_code in (403, 404), f"{path} → {res.status_code}"
        checked += 1
    assert checked >= 14
    # 목록도 자기 것만
    for url in ("/api/v1/loans", "/api/v1/dormitories", "/api/v1/spc"):
        body = client.get(url, headers=spc_h).text
        assert loan_b not in body and dorm_b not in body and spc_b not in body


def test_password_policy_rules():
    assert password_problems("Secure!2345") == []
    assert password_problems("abcdefghij1") == []                     # 2종류 10자 이상
    assert password_problems("Abc!2345") == []                        # 3종류 8자 이상
    assert password_problems("abcdefgh12") == []
    assert password_problems("abcd1234")                              # 2종류인데 8자
    assert password_problems("aaaaaaaaaaaa")                          # 1종류
    assert password_problems("Paaaa!2345678")                         # 같은 문자 4번 연속
    assert password_problems("hong1234!Xy", "hong1234@example.com")   # 이메일 아이디 포함
    h = admin_headers()
    r = client.post("/api/v1/users", json={"name": "x", "email": f"w-{uuid.uuid4().hex[:6]}@example.com", "role": "VIEWER",
                                           "password": "abcd1234"}, headers=h)
    assert r.status_code == 422 and "비밀번호 정책" in r.json()["detail"]
    r = client.post("/api/v1/users", json={"name": "x", "email": f"s-{uuid.uuid4().hex[:6]}@example.com", "role": "SPC_MANAGER",
                                           "password": PW}, headers=h)
    assert r.status_code == 422 and "SPC" in r.json()["detail"]      # SPC 담당자는 소속 필수


def test_login_lockout_unlock_and_login_records():
    h = admin_headers()
    uid, email = _user(h, "VIEWER")
    for i in range(settings.LOGIN_MAX_FAILURES - 1):
        r = client.post("/api/v1/auth/login", data={"username": email, "password": "wrong-Pass1!"})
        assert r.status_code == 401 and "남은 시도" in r.json()["detail"]
    r = client.post("/api/v1/auth/login", data={"username": email, "password": "wrong-Pass1!"})
    assert r.status_code == 401 and "잠겼습니다" in r.json()["detail"]
    r = client.post("/api/v1/auth/login", data={"username": email, "password": PW})
    assert r.status_code == 423                                         # 맞는 비밀번호여도 잠금 중
    # 없는 계정은 계정 존재 여부가 드러나지 않게 같은 메시지
    r = client.post("/api/v1/auth/login", data={"username": "nobody@example.com", "password": "x"})
    assert r.status_code == 401 and r.json()["detail"] == "이메일 또는 비밀번호가 올바르지 않습니다."

    assert client.post(f"/api/v1/users/{uid}/unlock", headers=h).status_code == 200
    _login(email)
    logs = client.get("/api/v1/admin/access-logs", params={"user_id": uid, "limit": 20}, headers=h).json()
    actions = [x["action"] for x in logs]
    assert actions[0] == "LOGIN_SUCCESS" and "LOGIN_LOCKED" in actions and actions.count("LOGIN_FAILED") == settings.LOGIN_MAX_FAILURES
    audit = client.get("/api/v1/admin/audit-logs", params={"table_name": "app_user", "limit": 50}, headers=h).json()
    assert any(a["record_id"] == uid and a["action"] == "UNLOCK" for a in audit)


def test_password_change_invalidates_old_tokens_and_me():
    h = admin_headers()
    _, email = _user(h, "FOUNDATION_FUND_MANAGER")
    old = _login(email)
    me = client.get("/api/v1/auth/me", headers=old).json()
    assert me["email"] == email and me["role"] == "FOUNDATION_FUND_MANAGER" and me["can_download"] is True
    assert client.post("/api/v1/auth/change-password", json={"current_password": "x", "new_password": "New!Pass2026"},
                       headers=old).status_code == 400
    assert client.post("/api/v1/auth/change-password", json={"current_password": PW, "new_password": "short1"},
                       headers=old).status_code == 422
    import time
    time.sleep(1.1)                                                       # 토큰 발급 시각(초 단위)과 구분
    assert client.post("/api/v1/auth/change-password", json={"current_password": PW, "new_password": "New!Pass2026"},
                       headers=old).status_code == 200
    assert client.get("/api/v1/auth/me", headers=old).status_code == 401  # 다른 곳에 남은 토큰도 무효
    client.get("/api/v1/auth/me", headers=_login(email, "New!Pass2026")).raise_for_status()


def test_user_admin_changes_are_audited_and_guarded():
    h = admin_headers()
    uid, _ = _user(h, "VIEWER")
    r = client.put(f"/api/v1/users/{uid}", json={"role": "FOUNDATION_FUND_MANAGER", "change_reason": "인사이동"}, headers=h)
    assert r.status_code == 200
    audit = client.get("/api/v1/admin/audit-logs", params={"table_name": "app_user", "limit": 50}, headers=h).json()
    row = next(a for a in audit if a["record_id"] == uid and a["field"] == "role")
    assert (row["before"], row["after"], row["reason"]) == ("VIEWER", "FOUNDATION_FUND_MANAGER", "인사이동")
    me = client.get("/api/v1/auth/me", headers=h).json()
    assert client.put(f"/api/v1/users/{me['id']}", json={"role": "VIEWER"}, headers=h).status_code == 422   # 본인 강등 금지
    assert client.delete(f"/api/v1/users/{me['id']}", headers=h).status_code == 422
    assert client.get("/api/v1/admin/audit-logs", headers=_login(_user(h, "FOUNDATION_FUND_MANAGER")[1])).status_code == 403


def test_viewer_download_setting_and_access_log():
    h = admin_headers()
    loan, dorm, _ = make_loan(h)
    vid, vemail = _user(h, "VIEWER", can_download=False)
    v = _login(vemail)
    assert client.get(f"/api/v1/loans/{loan}/schedule", headers=v).status_code == 200            # 조회는 가능
    r = client.get(f"/api/v1/loans/{loan}/schedule/download", headers=v)
    assert r.status_code == 403 and "다운로드" in r.json()["detail"]
    assert client.get("/api/v1/auth/me", headers=v).json()["can_download"] is False
    client.put(f"/api/v1/users/{vid}", json={"can_download": True}, headers=h)
    assert client.get(f"/api/v1/loans/{loan}/schedule/download", headers=v).status_code == 200

    logs = client.get("/api/v1/admin/access-logs", params={"loan_id": loan, "limit": 50}, headers=h).json()
    mine = [x for x in logs if x["user_id"] == vid]
    assert {"VIEW", "DOWNLOAD", "DENIED"} <= {x["action"] for x in mine}                         # 누가 어떤 차입금을 조회·다운로드
    assert all(x["loan_id"] == loan for x in mine) and vemail in mine[0]["user"]
    downloads = client.get("/api/v1/admin/access-logs", params={"action": "DOWNLOAD", "loan_id": loan}, headers=h).json()
    assert len(downloads) == 1 and downloads[0]["path"].endswith("/schedule/download")


def test_bank_account_is_encrypted_at_rest_masked_in_lists_and_plain_in_notice():
    h = admin_headers()
    _, dorm = make_org(h)
    make_loan(h, dorm=dorm)
    acct = "가나은행 123-456-789012"
    tpl = client.post("/api/v1/document-templates", json={"template_name": f"enc-{uuid.uuid4().hex[:6]}",
                                                         "config": {"bank_account": acct, "account_holder": "재단"}}, headers=h).json()
    assert tpl["config"]["bank_account"] == "가나은행 ***-***-**9012"
    db = SessionLocal()
    raw = db.execute(text("SELECT config->>'bank_account' FROM document_template WHERE id = :i"), {"i": tpl["id"]}).scalar()
    assert is_encrypted(raw) and "789012" not in raw and decrypt_str(raw) == acct
    n = client.post(f"/api/v1/dormitories/{dorm}/notices", json={"template_id": tpl["id"], "due_date": "2023-05-31"}, headers=h).json()
    raw = db.execute(text("SELECT generated_data->'account'->>'bank_account' FROM generated_document WHERE id = :i"), {"i": n["id"]}).scalar()
    db.close()
    assert is_encrypted(raw)
    assert n["generated_data"]["account"]["bank_account"] == acct                                  # 발급본은 실제 계좌
    assert client.get(f"/api/v1/notices/{n['id']}", headers=h).json()["generated_data"]["account"]["bank_account"] == acct
    assert client.get(f"/api/v1/notices/{n['id']}/download", headers=h).status_code == 200
    assert mask_account("국민 000-12-3456") == "국민 ***-**-3456"


def test_security_headers_and_production_settings_check():
    r = client.get("/api/v1/auth/me", headers=admin_headers())
    for k in ("X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy"):
        assert k in r.headers
    assert r.headers["Cache-Control"] == "no-store"

    class S:  # 운영 설정 점검 로직
        SECRET_KEY = "CHANGE_ME_IN_PRODUCTION"
        CORS_ORIGINS = "*"
        DATA_ENCRYPTION_KEY = None
        ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24
        DATABASE_URL = "postgresql+psycopg://user:password@localhost:5432/x"
    assert len(production_problems(S)) == 5
    S.SECRET_KEY, S.CORS_ORIGINS = "x" * 40, "https://loan.example.or.kr"
    S.DATA_ENCRYPTION_KEY, S.ACCESS_TOKEN_EXPIRE_MINUTES = "MDEyMzQ1Njc4OTAxMjM0NTY3ODkwMTIzNDU2Nzg5MDE=", 480
    S.DATABASE_URL = "postgresql+psycopg://svc:strong@db:5432/loan"
    assert production_problems(S) == []
    st = client.get("/api/v1/admin/security-status", headers=admin_headers()).json()
    assert st["app_env"] == "development" and st["production_ready"] is False


def test_event_approval_separates_request_and_approval(monkeypatch):
    monkeypatch.setattr(settings, "EVENT_APPROVAL_REQUIRED", True)
    h = admin_headers()
    loan, dorm, _ = make_loan(h)
    fid, femail = _user(h, "FOUNDATION_FUND_MANAGER")
    fund = _login(femail)
    approver = _login(_user(h, "FOUNDATION_APPROVER")[1])
    v1 = client.get(f"/api/v1/loans/{loan}/schedule", headers=h).json()

    r = client.post(f"/api/v1/loans/{loan}/events/early-repayment",
                    json={"event_date": "2023-11-30", "repay_type": "PARTIAL", "repay_amount": 10_000_000, "reason": "기안"}, headers=fund)
    assert r.status_code == 202 and r.json()["approval_required"] is True
    req = r.json()["request"]
    assert req["status"] == "REQUESTED" and req["event_label"] == "조기상환"
    assert client.get(f"/api/v1/loans/{loan}/schedule", headers=h).json()["version_no"] == v1["version_no"]   # 아직 미반영
    # 결재 대기 = 중도변수 미처리
    m = client.get("/api/v1/dashboard", params={"as_of": "2023-10-01", "dormitory_id": dorm}, headers=h).json()["alerts"]["needs_attention"]["midterm_pending"]
    assert [x["kind"] for x in m] == ["APPROVAL_PENDING"]
    # 같은 차입금에 두 번째 기안 불가, 기안자 본인 승인 불가
    assert client.post(f"/api/v1/loans/{loan}/events/acceleration", json={"event_date": "2024-01-01"}, headers=fund).status_code == 409
    assert client.post(f"/api/v1/event-requests/{req['id']}/approve", json={}, headers=fund).status_code == 403
    assert client.post(f"/api/v1/event-requests/{req['id']}/reject", json={}, headers=approver).status_code == 422   # 반려 사유 필수

    r = client.post(f"/api/v1/event-requests/{req['id']}/approve", json={"note": "확인"}, headers=approver)
    assert r.status_code == 200, r.text
    done = r.json()
    assert done["status"] == "APPROVED" and done["resulting_version_id"]
    v2 = client.get(f"/api/v1/loans/{loan}/schedule", headers=h).json()
    assert v2["version_no"] == v1["version_no"] + 1 and v2["id"] == done["resulting_version_id"]
    ev = client.get(f"/api/v1/loans/{loan}/events", headers=h).json()
    assert ev[0]["created_by"] == fid                                                    # 이벤트 등록자는 기안자
    assert client.post(f"/api/v1/event-requests/{req['id']}/approve", json={}, headers=approver).status_code == 409
    assert client.get("/api/v1/dashboard", params={"as_of": "2023-10-01", "dormitory_id": dorm},
                      headers=h).json()["alerts"]["needs_attention"]["midterm_pending"] == []

    # 반려·회수
    r2 = client.post(f"/api/v1/loans/{loan}/events/acceleration", json={"event_date": "2024-01-01"}, headers=fund).json()["request"]
    assert client.post(f"/api/v1/event-requests/{r2['id']}/withdraw", headers=approver).status_code == 403   # 본인만 회수
    assert client.post(f"/api/v1/event-requests/{r2['id']}/reject", json={"note": "근거 서류 미비"}, headers=approver).json()["status"] == "REJECTED"
    r3 = client.post(f"/api/v1/loans/{loan}/events/acceleration", json={"event_date": "2024-01-01"}, headers=fund).json()["request"]
    assert client.post(f"/api/v1/event-requests/{r3['id']}/withdraw", headers=fund).json()["status"] == "WITHDRAWN"
    hist = client.get(f"/api/v1/loans/{loan}/audit-logs", headers=h).json()
    acts = {x.get("action") for x in (hist if isinstance(hist, list) else hist.get("items", []))}
    assert {"APPROVE", "REJECT", "WITHDRAW"} <= acts


def test_backup_run_restore_drill_prune_and_admin_alerts(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "BACKUP_DIR", str(tmp_path))
    log = bk.run_backup()
    assert log.status == "SUCCESS", log.message
    f = tmp_path / log.file_name
    assert f.name.endswith(".dump.enc") and not f.read_bytes().startswith(b"PGDMP")              # 암호화 저장
    assert (tmp_path / (log.file_name + ".sha256")).read_text().split()[0] == log.sha256
    drill = bk.restore_test()
    assert drill.status == "SUCCESS", drill.message
    db = SessionLocal()
    assert not db.execute(text("SELECT 1 FROM pg_database WHERE datname LIKE 'restore_check_%'")).first()   # 임시 DB 정리

    # 손상된 파일은 복원 전에 막는다
    f.write_bytes(f.read_bytes()[:-1] + b"x")
    bad = bk.restore_test(f)
    assert bad.status == "FAILED" and "체크섬" in bad.message
    with pytest.raises(bk.BackupError):
        bk.restore(f, bk._conn_parts()["dbname"])                                                 # 운영 DB 덮어쓰기 금지

    # 보관 규칙: 최근 35일 매일 + 그 이전은 월 첫 백업(60개월)
    for p in tmp_path.glob("*"):
        p.unlink()
    now = datetime(2026, 10, 28, 3, tzinfo=timezone.utc)
    for d in range(0, 120):
        ts = now - timedelta(days=d)
        (tmp_path / f"dormitory_loan_{ts:%Y%m%d_%H%M%S}.dump.enc").write_bytes(b"x")
    bk.prune(now)
    left = sorted(p.name for p in tmp_path.glob("*.enc"))
    assert len([n for n in left if n >= f"dormitory_loan_{(now - timedelta(days=35)):%Y%m%d}"]) == 36
    assert "dormitory_loan_20260701_030000.dump.enc" in left and "dormitory_loan_20260702_030000.dump.enc" not in left

    # 관리자 안내: 백업이 오래되면 팝업
    from app.domain.popup_service import _backup_items
    assert [i["kind"] for i in _backup_items(db, now=datetime.now(timezone.utc))] == []
    stale = _backup_items(db, now=datetime.now(timezone.utc) + timedelta(hours=settings.BACKUP_ALERT_HOURS + 1))
    assert [i["kind"] for i in stale] == ["BACKUP_STALE"]
    last = db.execute(select(BackupLog).where(BackupLog.kind == "BACKUP").order_by(BackupLog.started_at.desc())).scalars().first()
    db.add(BackupLog(kind="BACKUP", status="FAILED", message="디스크 부족(테스트)", started_at=last.started_at + timedelta(seconds=1)))
    db.commit()
    assert "BACKUP_FAILED" in [i["kind"] for i in _backup_items(db)]
    db.close()
    assert client.get("/api/v1/admin/backups", headers=admin_headers()).json()["last_restore_test_at"] is not None
