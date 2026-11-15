"""
클라우드 배포 시작 준비 — deploy_start.sh가 서버를 켜기 전에 한 번 실행한다.
1) 표·칸을 최신 상태로 맞춘다(init_db와 같음 — 여러 번 실행해도 안전)
2) 사용자가 한 명도 없는 빈 DB이고 SEED_DEMO=1이면 데모 데이터를 만든다(이미 데이터가 있으면 절대 건드리지 않음)
"""
import os
import subprocess
import sys

from sqlalchemy import func, select


def main() -> None:
    py = sys.executable
    subprocess.run([py, "-m", "app.jobs.init_db"], check=True)
    from app.core.database import SessionLocal
    from app.models.organization import User
    with SessionLocal() as db:
        users = db.execute(select(func.count()).select_from(User)).scalar_one()
    if users == 0 and os.environ.get("SEED_DEMO") == "1":
        print("[배포] 빈 DB — 데모 데이터를 만듭니다(1~2분).", flush=True)
        subprocess.run([py, "-m", "app.jobs.seed_demo"], check=True)
    else:
        print(f"[배포] 기존 데이터 유지(사용자 {users}명).", flush=True)


if __name__ == "__main__":
    main()
