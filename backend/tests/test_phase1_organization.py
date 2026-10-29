"""
Phase 1 (기숙사/SPC/사용자) End-to-End 테스트.
실행 전: DATABASE_URL이 가리키는 PostgreSQL에 테이블이 생성되어 있어야 함.
    python -c "from app.core.database import engine; from app.models.base import Base; from app.models import organization; Base.metadata.create_all(bind=engine)"
"""
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.main import app  # noqa: E402
from app.models.organization import User, UserRole  # noqa: E402

client = TestClient(app)


def _get_or_create_admin(email: str, password: str) -> None:
    """테스트를 여러 번 재실행해도 충돌 없이 동작하도록 존재하면 그대로 사용."""
    db = SessionLocal()
    existing = db.query(User).filter(User.email == email).first()
    if existing is None:
        admin = User(
            name="테스트관리자",
            email=email,
            role=UserRole.SYSTEM_ADMIN,
            hashed_password=hash_password(password),
        )
        db.add(admin)
        db.commit()
    db.close()


def test_rbac_and_crud_flow():
    _get_or_create_admin("pytest-admin@example.com", "testpass123")

    # 관리자 로그인
    r = client.post(
        "/api/v1/auth/login",
        data={"username": "pytest-admin@example.com", "password": "testpass123"},
    )
    assert r.status_code == 200
    admin_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

    # 매 실행마다 고유한 이름 사용 — 재실행 시 UNIQUE 제약 충돌 방지
    run_id = uuid.uuid4().hex[:8]

    # SPC 생성
    r = client.post("/api/v1/spc", json={"spc_name": f"테스트SPC-{run_id}"}, headers=admin_headers)
    assert r.status_code == 201
    spc_id = r.json()["id"]

    # 기숙사 등록
    r = client.post(
        "/api/v1/dormitories",
        json={
            "dormitory_name": f"테스트기숙사-{run_id}",
            "school_name": "테스트대학교",
            "spc_id": spc_id,
        },
        headers=admin_headers,
    )
    assert r.status_code == 201
    dorm_id = r.json()["id"]
    assert r.json()["status"] == "IN_PROGRESS"

    # SPC 담당자 생성 및 row-level 권한 검증
    spc_manager_email = f"pytest-spc-{run_id}@example.com"
    r = client.post(
        "/api/v1/users",
        json={
            "name": "테스트SPC담당자",
            "email": spc_manager_email,
            "role": "SPC_MANAGER",
            "spc_id": spc_id,
            "password": "spcpass123",
        },
        headers=admin_headers,
    )
    assert r.status_code == 201

    r = client.post(
        "/api/v1/auth/login",
        data={"username": spc_manager_email, "password": "spcpass123"},
    )
    spc_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

    r = client.get("/api/v1/dormitories", headers=spc_headers)
    assert r.status_code == 200
    assert all(d["spc_id"] == spc_id for d in r.json())

    # SPC담당자는 SPC 생성 불가 (403)
    r = client.post("/api/v1/spc", json={"spc_name": "불법생성시도"}, headers=spc_headers)
    assert r.status_code == 403

    # 기숙사 종료 처리 (물리삭제 아님 — 상태 변경만)
    r = client.delete(f"/api/v1/dormitories/{dorm_id}", headers=admin_headers)
    assert r.status_code == 204
    r = client.get(f"/api/v1/dormitories/{dorm_id}", headers=admin_headers)
    assert r.json()["status"] == "CLOSED"
