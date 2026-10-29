"""로컬 PC 최초 설정 도우미 (Windows·Mac·Linux 공통).

하는 일
 1) PostgreSQL 관리자 계정으로 접속해 앱 전용 계정(dormuser)과 DB를 만든다(이미 있으면 그대로 둔다).
 2) backend/.env 를 만든다 — SECRET_KEY·DATA_ENCRYPTION_KEY를 무작위로 생성(이미 있으면 덮어쓰지 않음).
 3) 테이블·기준정보를 만들고
    - demo  모드: 가상 기숙사 8곳·차입금 9건 데모 데이터(비밀번호 demo1234!)
    - blank 모드: 빈 DB + 최초 시스템관리자 1명

사용: python local_setup.py [--mode demo|blank] [--pg-host 127.0.0.1] [--pg-port 5432] [--pg-user postgres]
"""
import argparse
import base64
import getpass
import os
import secrets
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENV = HERE / ".env"


def read_env() -> dict:
    out = {}
    if ENV.exists():
        for line in ENV.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def ensure_db(a, app_pw: str) -> None:
    import psycopg
    from psycopg import sql

    su_pw = os.environ.get("PG_SUPERUSER_PASSWORD")
    if su_pw is None:
        su_pw = getpass.getpass(f"PostgreSQL 관리자({a.pg_user}) 비밀번호 (설치할 때 정한 것): ")
    try:
        conn = psycopg.connect(host=a.pg_host, port=a.pg_port, user=a.pg_user, password=su_pw,
                               dbname="postgres", autocommit=True, connect_timeout=5)
    except Exception as e:  # noqa: BLE001
        sys.exit(f"[오류] PostgreSQL에 접속하지 못했습니다: {e}\n"
                 "  - PostgreSQL 서비스가 켜져 있는지, 비밀번호가 맞는지 확인하세요.")
    with conn:
        role_exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (a.app_user,)).fetchone()
        if role_exists:
            conn.execute(sql.SQL("ALTER ROLE {} WITH LOGIN CREATEDB PASSWORD {}").format(
                sql.Identifier(a.app_user), sql.Literal(app_pw)))
            print(f"[DB] 계정 {a.app_user} 확인(비밀번호 갱신)")
        else:
            conn.execute(sql.SQL("CREATE ROLE {} WITH LOGIN CREATEDB PASSWORD {}").format(
                sql.Identifier(a.app_user), sql.Literal(app_pw)))
            print(f"[DB] 계정 {a.app_user} 생성")
        if conn.execute("SELECT 1 FROM pg_database WHERE datname=%s", (a.db_name,)).fetchone():
            print(f"[DB] 데이터베이스 {a.db_name} 이미 있음 — 그대로 사용")
        else:
            conn.execute(sql.SQL("CREATE DATABASE {} OWNER {} ENCODING 'UTF8' TEMPLATE template0").format(
                sql.Identifier(a.db_name), sql.Identifier(a.app_user)))
            print(f"[DB] 데이터베이스 {a.db_name} 생성")


def write_env(a, app_pw: str) -> None:
    cur = read_env()
    values = {
        "DATABASE_URL": f"postgresql+psycopg://{a.app_user}:{app_pw}@{a.pg_host}:{a.pg_port}/{a.db_name}",
        "SECRET_KEY": cur.get("SECRET_KEY") or secrets.token_urlsafe(48),
        "DATA_ENCRYPTION_KEY": cur.get("DATA_ENCRYPTION_KEY") or base64.b64encode(os.urandom(32)).decode(),
        "APP_ENV": "development",
        "CORS_ORIGINS": f"http://127.0.0.1:{a.port},http://localhost:{a.port}",
        "KASI_SERVICE_KEY": cur.get("KASI_SERVICE_KEY", ""),
        "BANK_EXTRA_CLOSURES": cur.get("BANK_EXTRA_CLOSURES", ""),
        "ACCESS_TOKEN_EXPIRE_MINUTES": "480",
        "LOGIN_MAX_FAILURES": "5",
        "LOGIN_LOCK_MINUTES": "15",
        "EVENT_APPROVAL_REQUIRED": cur.get("EVENT_APPROVAL_REQUIRED", "false"),
        "BACKUP_DIR": "./backups",
        "BACKUP_ENCRYPT": "true",
        "ATTACHMENT_DIR": "./attachments",
        "ATTACHMENT_MAX_MB": "20",
        "ATTACHMENT_EXTENSIONS": "pdf,hwp,hwpx,doc,docx,xls,xlsx,csv,ppt,pptx,jpg,jpeg,png,gif,txt,zip",
    }
    lines = ["# 로컬 설정 도우미(local_setup.py)가 만든 파일입니다.",
             "# DATA_ENCRYPTION_KEY를 잃으면 암호화된 계좌·증빙파일·백업을 복구할 수 없습니다 — 따로 보관하세요."]
    lines += [f"{k}={v}" for k, v in values.items()]
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[설정] {ENV.name} 작성 (암호화 키 {'유지' if cur.get('DATA_ENCRYPTION_KEY') else '새로 생성'})")


def run(*args, env=None) -> None:
    r = subprocess.run([sys.executable, "-W", "ignore", *args], cwd=HERE, env=env)
    if r.returncode:
        sys.exit(r.returncode)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["demo", "blank"], default=None)
    ap.add_argument("--pg-host", default="127.0.0.1")
    ap.add_argument("--pg-port", default="5432")
    ap.add_argument("--pg-user", default="postgres")
    ap.add_argument("--app-user", default="dormuser")
    ap.add_argument("--db-name", default=None)
    ap.add_argument("--port", default=os.environ.get("PORT", "8000"))
    ap.add_argument("--admin-email", default=None)
    a = ap.parse_args()

    if a.mode is None:
        ans = input("데모 데이터로 시작할까요? (Y=데모 / N=빈 DB로 실제 사용) [Y]: ").strip().lower()
        a.mode = "blank" if ans in ("n", "no", "아니오") else "demo"
    a.db_name = a.db_name or ("dormitory_demo" if a.mode == "demo" else "dormitory_loan")

    # 앱 계정 비밀번호: 기존 .env에 있으면 재사용, 없으면 무작위
    app_pw = None
    url = read_env().get("DATABASE_URL", "")
    if f"//{a.app_user}:" in url:
        app_pw = url.split(f"//{a.app_user}:", 1)[1].split("@", 1)[0]
    app_pw = app_pw or secrets.token_hex(16)

    ensure_db(a, app_pw)
    write_env(a, app_pw)

    env = dict(os.environ)
    if a.mode == "demo":
        from datetime import date
        print("[데모] 데이터 생성 중… (1~2분)")
        sys.path.insert(0, str(HERE))
        r = subprocess.run([sys.executable, "-W", "ignore", "-m", "app.jobs.seed_demo",
                            "--as-of", date.today().isoformat()], cwd=HERE, env=env)
        if r.returncode:
            print("  (이미 데이터가 있는 DB라 데모 생성을 건너뜁니다)")
        run("-m", "app.jobs.init_db")
        print("\n완료! 로그인: demo-admin@example.com / demo1234!  (자금담당 demo-fund@, 결재권자 demo-approver@,"
              " SPC 담당자 demo-spc@, 조회자 demo-viewer@ — 모두 example.com)")
    else:
        email = a.admin_email or input("최초 시스템관리자 이메일: ").strip()
        print("비밀번호 규칙: 2종류 조합 10자 이상 또는 3종류 조합 8자 이상(영문·숫자·특수문자), 같은 문자 4연속 금지")
        run("-m", "app.jobs.init_db", "--admin-email", email)
        print(f"\n완료! 로그인: {email} / (방금 정한 비밀번호)")


if __name__ == "__main__":
    main()
