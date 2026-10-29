"""
DB 백업·복구(§ 백업 구조) — 외부 스케줄러(cron, 작업 스케줄러)가 실행한다.

    python -m app.jobs.backup run            # 백업(매일). 끝나면 보관 규칙에 따라 오래된 파일 정리
    python -m app.jobs.backup restore-test   # 복구 모의훈련(분기 1회 권장): 최신 백업을 임시 DB에 복원해 검증 후 삭제
    python -m app.jobs.backup restore --file <백업파일> --target-db <새 DB이름>
                                             # 실제 복구: 항상 '새 DB'에 복원한다(운영 DB를 덮어쓰지 않음).
                                             #   확인 후 .env의 DATABASE_URL을 새 DB로 바꾸고 서버를 재시작한다.
    python -m app.jobs.backup list

- 형식: pg_dump custom(-Fc). 파일마다 SHA-256을 기록하고, BACKUP_ENCRYPT=true면 AES-256-GCM으로 암호화(.dump.enc).
- 보관: 최근 BACKUP_KEEP_DAILY_DAYS일은 매일분 전부, 그 이전은 달마다 첫 백업만 BACKUP_KEEP_MONTHLY개월(기본 5년) 보관.
- 결과는 backup_log 테이블에 남고, 성공한 백업이 BACKUP_ALERT_HOURS 넘게 없으면 관리자 로그인 팝업으로 안내된다.
- 암호화 키(DATA_ENCRYPTION_KEY)가 없으면 암호화된 백업은 복원할 수 없다 → 키는 백업과 다른 곳에 따로 보관.
- 시점 복구(PITR)는 PostgreSQL WAL 보관 설정으로 한다(README '백업·복구' 참고). 이 스크립트는 일 단위 전체 백업을 담당한다.
"""
import argparse
import hashlib
import os
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

from sqlalchemy import create_engine, text

from app.core.config import settings
from app.core.crypto import decrypt_bytes, encrypt_bytes
from app.core.database import SessionLocal
from app.models.security import BackupLog

KEY_TABLES = ["loan", "interest_rate_history", "repayment_schedule_version", "repayment_schedule_row", "loan_event",
              "actual_payment", "audit_log", "app_user", "dormitory", "generated_document"]
PREFIX = "dormitory_loan_"


class BackupError(Exception):
    pass


def _conn_parts(url: str | None = None) -> dict:
    u = urlparse((url or settings.DATABASE_URL).replace("postgresql+psycopg://", "postgresql://"))
    return {"host": u.hostname or "localhost", "port": str(u.port or 5432), "user": unquote(u.username or ""),
            "password": unquote(u.password or ""), "dbname": u.path.lstrip("/")}


def _env(p: dict) -> dict:
    return {**os.environ, "PGPASSWORD": p["password"]}


def _pg_args(p: dict, db: str | None = None) -> list[str]:
    return ["-h", p["host"], "-p", p["port"], "-U", p["user"], "-d", db or p["dbname"]]


def _log(**kw) -> BackupLog:
    db = SessionLocal()
    try:
        row = BackupLog(**kw)
        db.add(row)
        db.commit()
        db.refresh(row)
        return row
    finally:
        db.close()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def backup_dir() -> Path:
    d = Path(settings.BACKUP_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return d


def run_backup(now: datetime | None = None) -> BackupLog:
    started = datetime.now(timezone.utc)
    now = now or started
    p = _conn_parts()
    name = f"{PREFIX}{now:%Y%m%d_%H%M%S}.dump" + (".enc" if settings.BACKUP_ENCRYPT else "")
    out = backup_dir() / name
    try:
        r = subprocess.run(["pg_dump", "-Fc", "--no-owner", *_pg_args(p)], capture_output=True, env=_env(p), timeout=3600)
        if r.returncode != 0:
            raise BackupError(r.stderr.decode(errors="replace")[:500])
        data = encrypt_bytes(r.stdout) if settings.BACKUP_ENCRYPT else r.stdout
        tmp = out.with_suffix(out.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(out)                                     # 다 쓴 뒤에만 정식 이름 → 반쯤 쓴 파일이 백업으로 보이지 않게
        digest = _sha256(out)
        Path(str(out) + ".sha256").write_text(f"{digest}  {name}\n")
        files_note = _backup_attachments(out)
        log = _log(kind="BACKUP", status="SUCCESS", started_at=started, finished_at=datetime.now(timezone.utc),
                   file_name=name, size_bytes=out.stat().st_size, sha256=digest, encrypted=settings.BACKUP_ENCRYPT,
                   message=files_note)
    except (BackupError, OSError, subprocess.TimeoutExpired) as e:
        return _log(kind="BACKUP", status="FAILED", started_at=started, finished_at=datetime.now(timezone.utc),
                    file_name=name, message=str(e)[:1000])
    prune(now)
    return log


def files_archive(dump: Path) -> Path:
    """DB 백업과 짝이 되는 증빙파일 묶음(같은 시각 이름)."""
    return dump.parent / (dump.name[:len(PREFIX) + 15] + "_files.tar")


def _backup_attachments(dump: Path) -> str | None:
    """증빙파일 폴더를 tar로 묶는다. 파일은 이미 하나씩 AES-256-GCM으로 암호화돼 있어 다시 암호화하지 않는다."""
    import tarfile
    src = Path(settings.ATTACHMENT_DIR)
    if not src.is_dir() or not any(src.iterdir()):
        return None
    target = files_archive(dump)
    tmp = target.with_suffix(".tar.part")
    with tarfile.open(tmp, "w") as tf:
        for f in sorted(src.iterdir()):
            if f.is_file():
                tf.add(f, arcname=f.name)
    tmp.replace(target)
    Path(str(target) + ".sha256").write_text(f"{_sha256(target)}  {target.name}\n")
    return f"증빙파일 묶음 {target.name}"


def _stamp(f: Path) -> datetime | None:
    try:
        return datetime.strptime(f.name[len(PREFIX):len(PREFIX) + 15], "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def backup_files() -> list[Path]:
    return sorted((f for f in backup_dir().glob(f"{PREFIX}*.dump*") if not f.name.endswith((".sha256", ".part")) and _stamp(f)),
                  key=_stamp)


def prune(now: datetime | None = None) -> list[str]:
    """보관 규칙에서 벗어난 백업 파일만 지운다(DB 업무 데이터가 아니라 백업 사본의 순환 정리)."""
    now = now or datetime.now(timezone.utc)
    files = backup_files()
    keep: set[Path] = set()
    daily_cut = now - timedelta(days=settings.BACKUP_KEEP_DAILY_DAYS)
    first_of_month: dict[tuple, Path] = {}
    for f in files:
        ts = _stamp(f)
        if ts >= daily_cut:
            keep.add(f)
        first_of_month.setdefault((ts.year, ts.month), f)
    months = sorted(first_of_month)[-settings.BACKUP_KEEP_MONTHLY:]
    keep.update(first_of_month[m] for m in months)
    if files:
        keep.add(files[-1])                                   # 어떤 경우에도 최신 1개는 남긴다
    removed = []
    for f in files:
        if f not in keep:
            f.unlink(missing_ok=True)
            Path(str(f) + ".sha256").unlink(missing_ok=True)
            fa = files_archive(f)
            fa.unlink(missing_ok=True)
            Path(str(fa) + ".sha256").unlink(missing_ok=True)
            removed.append(f.name)
    if removed:
        _log(kind="PRUNE", status="SUCCESS", finished_at=datetime.now(timezone.utc), message=f"{len(removed)}개 정리: " + ", ".join(removed[:20]))
    return removed


def _read_dump(path: Path) -> bytes:
    sha_file = Path(str(path) + ".sha256")
    if sha_file.exists() and sha_file.read_text().split()[0] != _sha256(path):
        raise BackupError(f"체크섬 불일치 — 백업 파일이 손상되었습니다: {path.name}")
    raw = path.read_bytes()
    return decrypt_bytes(raw) if path.name.endswith(".enc") else raw


def _restore_into(dump: bytes, target_db: str) -> None:
    p = _conn_parts()
    r = subprocess.run(["createdb", "-h", p["host"], "-p", p["port"], "-U", p["user"], target_db], capture_output=True, env=_env(p))
    if r.returncode != 0:
        raise BackupError("복원용 DB 생성 실패(이미 있는 이름이면 다른 이름을 쓰세요): " + r.stderr.decode(errors="replace")[:300])
    with tempfile.NamedTemporaryFile(suffix=".dump") as tf:
        tf.write(dump)
        tf.flush()
        r = subprocess.run(["pg_restore", "--no-owner", "--exit-on-error", *_pg_args(p, target_db), tf.name],
                           capture_output=True, env=_env(p), timeout=3600)
    if r.returncode != 0:
        raise BackupError("pg_restore 실패: " + r.stderr.decode(errors="replace")[:500])


def _counts(url: str) -> dict:
    eng = create_engine(url)
    try:
        with eng.connect() as c:
            out = {}
            for t in KEY_TABLES:
                try:
                    out[t] = c.execute(text(f'SELECT count(*) FROM "{t}"')).scalar()
                except Exception:  # noqa: BLE001
                    c.rollback()
                    out[t] = None
            return out
    finally:
        eng.dispose()


def _url_for(dbname: str) -> str:
    p = _conn_parts()
    base = settings.DATABASE_URL.rsplit("/", 1)[0]
    return f"{base}/{dbname}"


def _drop(dbname: str) -> None:
    p = _conn_parts()
    subprocess.run(["dropdb", "--if-exists", "-h", p["host"], "-p", p["port"], "-U", p["user"], dbname], capture_output=True, env=_env(p))


def restore_test(path: Path | None = None) -> BackupLog:
    """최신(또는 지정) 백업을 임시 DB에 복원 → 핵심 테이블 건수를 백업 시점 기록·현재 DB와 비교 → 임시 DB 삭제."""
    started = datetime.now(timezone.utc)
    files = backup_files()
    path = path or (files[-1] if files else None)
    if path is None:
        return _log(kind="RESTORE_TEST", status="FAILED", started_at=started, finished_at=datetime.now(timezone.utc),
                    message="백업 파일이 없습니다.")
    tmpdb = f"restore_check_{uuid.uuid4().hex[:8]}"
    try:
        _restore_into(_read_dump(path), tmpdb)
        restored = _counts(_url_for(tmpdb))
        live = _counts(settings.DATABASE_URL)
        empty = [t for t in ("loan", "repayment_schedule_row", "app_user") if not restored.get(t) and live.get(t)]
        if empty:
            raise BackupError(f"복원본에 핵심 데이터가 비어 있습니다: {', '.join(empty)}")
        diff = {t: (restored[t], live[t]) for t in KEY_TABLES if restored.get(t) != live.get(t)}
        msg = "복원 성공. " + ("현재 DB와 건수 동일" if not diff else
                               "백업 이후 변경분(복원본/현재): " + ", ".join(f"{t} {a}/{b}" for t, (a, b) in diff.items()))
        return _log(kind="RESTORE_TEST", status="SUCCESS", started_at=started, finished_at=datetime.now(timezone.utc),
                    file_name=path.name, message=msg)
    except (BackupError, OSError, subprocess.TimeoutExpired, Exception) as e:  # noqa: BLE001
        return _log(kind="RESTORE_TEST", status="FAILED", started_at=started, finished_at=datetime.now(timezone.utc),
                    file_name=path.name, message=str(e)[:1000])
    finally:
        _drop(tmpdb)


def restore(path: Path, target_db: str) -> BackupLog:
    started = datetime.now(timezone.utc)
    if target_db == _conn_parts()["dbname"]:
        raise BackupError("운영 중인 DB에는 직접 복원하지 않습니다. 새 DB 이름을 지정하세요.")
    try:
        _restore_into(_read_dump(path), target_db)
        counts = _counts(_url_for(target_db))
        return _log(kind="RESTORE", status="SUCCESS", started_at=started, finished_at=datetime.now(timezone.utc),
                    file_name=path.name, message=f"{target_db}에 복원: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    except (BackupError, OSError) as e:
        return _log(kind="RESTORE", status="FAILED", started_at=started, finished_at=datetime.now(timezone.utc),
                    file_name=path.name, message=str(e)[:1000])


def main() -> None:
    ap = argparse.ArgumentParser(description="DB 백업·복구")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run")
    sub.add_parser("list")
    sub.add_parser("prune")
    t = sub.add_parser("restore-test")
    t.add_argument("--file")
    r = sub.add_parser("restore")
    r.add_argument("--file", required=True)
    r.add_argument("--target-db", required=True)
    a = ap.parse_args()

    if a.cmd == "run":
        log = run_backup()
        print(f"[{log.status}] {log.file_name} {log.size_bytes or ''} {log.message or ''}")
        sys.exit(0 if log.status == "SUCCESS" else 1)
    if a.cmd == "list":
        for f in backup_files():
            print(f.name, f.stat().st_size)
    elif a.cmd == "prune":
        print("정리:", prune() or "없음")
    elif a.cmd == "restore-test":
        log = restore_test(Path(a.file) if a.file else None)
        print(f"[{log.status}] {log.file_name}: {log.message}")
        sys.exit(0 if log.status == "SUCCESS" else 1)
    elif a.cmd == "restore":
        try:
            log = restore(Path(a.file), a.target_db)
        except BackupError as e:
            print(f"[중단] {e}")
            sys.exit(2)
        print(f"[{log.status}] {log.message}")
        sys.exit(0 if log.status == "SUCCESS" else 1)


if __name__ == "__main__":
    main()
