"""
DB 초기화·업그레이드(최초 설치와 버전 올릴 때 같은 명령) — 여러 번 실행해도 안전하다.

    python -m app.jobs.init_db --admin-email admin@재단도메인            # 최초 설치(관리자 비밀번호는 입력창에서)
    python -m app.jobs.init_db                                          # 업그레이드·점검만
    python -m app.jobs.init_db --check                                  # 바꾸지 않고 점검 결과만

하는 일
1) 없는 테이블 생성(create_all — 기존 테이블·데이터는 건드리지 않음)
2) 기존 테이블에 새로 생긴 칸 추가, 선택값(enum)에 새 값 추가 — 모델과 실제 DB를 비교해 빠진 것만
3) 기준정보(재원·상환주기·상환유형 등) 기본값 등록(이미 있으면 그대로)
4) 시스템관리자가 한 명도 없을 때만 최초 관리자 생성(비밀번호 정책 적용)
5) 예전 은행휴무 규칙(5/1·12/31) 사용중지, 암호화 전 계좌 암호화
"""
import argparse
import getpass
import os
import sys

from sqlalchemy import Enum as SAEnum
from sqlalchemy import inspect, text

import app.main  # noqa: F401  (모든 모델 등록)
from app.core.database import SessionLocal, engine
from app.core.password_policy import password_problems
from app.core.security import hash_password
from app.models.base import Base
from app.models.organization import User, UserRole


def _col_ddl(col) -> str:
    t = col.type.compile(dialect=engine.dialect)
    ddl = f'"{col.name}" {t}'
    default = None
    if col.server_default is not None:
        arg = col.server_default.arg
        default = ("'" + arg.replace("'", "''") + "'") if isinstance(arg, str) else str(arg)
    elif col.default is not None and getattr(col.default, "is_scalar", False):
        v = col.default.arg
        default = ("TRUE" if v else "FALSE") if isinstance(v, bool) else (f"'{v.value if hasattr(v, 'value') else v}'" if isinstance(v, str) or hasattr(v, "value") else str(v))
    if default is not None:
        ddl += f" DEFAULT {default}"
    if not col.nullable and default is not None:
        ddl += " NOT NULL"
    return ddl


def schema_diff() -> dict:
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    missing_tables, missing_cols, missing_enum = [], [], []
    with engine.connect() as c:
        enums = {}
        for name, label in c.execute(text("SELECT t.typname, e.enumlabel FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid")):
            enums.setdefault(name, set()).add(label)
    for t in Base.metadata.sorted_tables:
        if t.name not in tables:
            missing_tables.append(t.name)
            continue
        have = {c["name"] for c in insp.get_columns(t.name)}
        for col in t.columns:
            if col.name not in have:
                missing_cols.append((t.name, col))
            if isinstance(col.type, SAEnum) and col.type.name in enums:
                for v in col.type.enums:
                    if v not in enums[col.type.name]:
                        missing_enum.append((col.type.name, v))
    return {"tables": missing_tables, "columns": missing_cols, "enums": sorted(set(missing_enum))}


def upgrade(dry: bool = False) -> list[str]:
    d = schema_diff()
    done = [f"테이블 생성: {t}" for t in d["tables"]]
    if not dry:
        Base.metadata.create_all(bind=engine)
    with engine.begin() as c:
        for tname, col in d["columns"]:
            sql = f'ALTER TABLE "{tname}" ADD COLUMN IF NOT EXISTS {_col_ddl(col)}'
            done.append(sql)
            if not dry:
                c.execute(text(sql))
    for typ, v in d["enums"]:
        sql = f"ALTER TYPE {typ} ADD VALUE IF NOT EXISTS '{v}'"
        done.append(sql)
        if not dry:
            with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
                c.execute(text(sql))
    return done


def create_admin(email: str, name: str) -> str:
    db = SessionLocal()
    try:
        if db.query(User).filter(User.role == UserRole.SYSTEM_ADMIN, User.is_deleted.is_(False)).first():
            return "시스템관리자가 이미 있어 만들지 않았습니다."
        pw = os.environ.get("INIT_ADMIN_PASSWORD") or getpass.getpass("최초 관리자 비밀번호: ")
        problems = password_problems(pw, email)
        if problems:
            raise SystemExit("비밀번호 정책 위반: " + " ".join(problems))
        db.add(User(name=name, email=email, role=UserRole.SYSTEM_ADMIN, hashed_password=hash_password(pw)))
        db.commit()
        return f"최초 관리자 생성: {email}"
    finally:
        db.close()


def main():
    ap = argparse.ArgumentParser(description="DB 초기화·업그레이드")
    ap.add_argument("--admin-email")
    ap.add_argument("--admin-name", default="시스템관리자")
    ap.add_argument("--check", action="store_true", help="변경 없이 점검만")
    a = ap.parse_args()

    steps = upgrade(dry=a.check)
    print(("[점검] 필요한 변경: " if a.check else "[DB] 반영한 변경: ") + (f"{len(steps)}건" if steps else "없음(최신)"))
    for s in steps:
        print("  -", s)
    if a.check:
        sys.exit(1 if steps else 0)

    from app.seed_reference import seed
    seed()
    from app.domain.holiday_sync import retire_stale_bank_rules
    db = SessionLocal()
    try:
        n = retire_stale_bank_rules(db)
    finally:
        db.close()
    if n:
        print(f"[정리] 예전 은행휴무 규칙 {n}건 사용중지")
    from app.jobs.encrypt_existing import main as encrypt_existing
    encrypt_existing()
    if a.admin_email:
        print("[관리자]", create_admin(a.admin_email, a.admin_name))
    left = schema_diff()
    print("[확인] 모델과 DB 일치" if not any(left.values()) else f"[확인] 남은 차이: {left}")


if __name__ == "__main__":
    main()
