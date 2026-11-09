"""
데이터 옮기기(이관) — 한 환경의 데이터를 통째로 다른 환경으로 옮긴다.
  예) 예전 Codespace → 새 Codespace, Codespace → 시연용 노트북(Windows)

    python -m app.jobs.transfer export                 # 내보내기 → backups/transfer_날짜_시각.dlt
    python -m app.jobs.transfer import <파일>          # 가져오기(현재 DB 내용을 모두 지우고 파일 내용으로 바꿈)
    python -m app.jobs.transfer info <파일>            # 파일 안의 건수만 확인(바꾸지 않음)

- 내보낼 때 정한 '이관 비밀번호'로 파일 전체를 암호화한다(scrypt + AES-256-GCM). 가져올 때 같은 비밀번호가 필요하다.
- 계좌번호 등 암호화된 값과 증빙파일은 내보낼 때 풀었다가, 가져오는 쪽의 암호화 키(DATA_ENCRYPTION_KEY)로 다시 암호화한다
  → 양쪽 .env(암호화 키)가 달라도 되고, .env를 옮길 필요가 없다.
- pg_dump 같은 별도 프로그램 없이 Python만으로 동작한다(Codespaces·Windows 공통).
- 사용자 계정·비밀번호(해시)도 함께 옮겨지므로, 가져온 뒤에는 예전 환경의 계정으로 로그인한다.
- 양쪽 프로그램 버전이 같아야 가장 안전하다(가져오는 쪽에서 먼저 최신 코드로 맞추고 init_db 실행).
"""
import argparse
import base64
import getpass
import hashlib
import io
import json
import os
import sys
import uuid
import zipfile
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import Date, DateTime, Enum as SAEnum, Integer, LargeBinary, Numeric, Uuid, select, text

import app.main  # noqa: F401  (모든 모델 등록)
from app.core.config import settings
from app.core.crypto import CryptoError, PREFIX, decrypt_bytes, decrypt_str, encrypt_bytes, encrypt_str
from app.core.database import engine
from app.models.base import Base

MAGIC = b"DLTX1"
ENC = "$enc"            # 암호화돼 있던 값 표시 — 가져올 때 다시 암호화한다


class TransferError(Exception):
    pass


# ---------- 파일 암호화(이관 비밀번호) ----------
def _derive(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2 ** 15, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32)


def seal(data: bytes, password: str) -> bytes:
    salt, nonce = os.urandom(16), os.urandom(12)
    return MAGIC + salt + nonce + AESGCM(_derive(password, salt)).encrypt(nonce, data, MAGIC)


def unseal(blob: bytes, password: str) -> bytes:
    if not blob.startswith(MAGIC):
        raise TransferError("이관 파일이 아닙니다(.dlt 파일을 지정하세요).")
    salt, nonce, body = blob[5:21], blob[21:33], blob[33:]
    try:
        return AESGCM(_derive(password, salt)).decrypt(nonce, body, MAGIC)
    except Exception as e:  # noqa: BLE001
        raise TransferError("비밀번호가 틀렸거나 파일이 손상되었습니다.") from e


# ---------- 값 변환 ----------
def _plain(v):
    """DB 값 → JSON. 암호화된 문자열은 풀어서 {"$enc": 평문}으로 표시."""
    if isinstance(v, str):
        return {ENC: decrypt_str(v)} if v.startswith(PREFIX) else v
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, (uuid.UUID, Decimal)):
        return str(v)
    if isinstance(v, bytes):
        return {"$b64": base64.b64encode(v).decode()}
    if hasattr(v, "name") and hasattr(v, "value"):        # enum
        return v.name
    return v


def _rewrap(v):
    """JSON → 값(중첩). {"$enc": 평문}은 이 환경의 키로 다시 암호화."""
    if isinstance(v, dict):
        if set(v) == {ENC}:
            return encrypt_str(v[ENC]) if v[ENC] else v[ENC]
        if set(v) == {"$b64"}:
            return base64.b64decode(v["$b64"])
        return {k: _rewrap(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_rewrap(x) for x in v]
    return v


def _to_db(col, v):
    v = _rewrap(v)
    if v is None:
        return None
    t = col.type
    if isinstance(t, DateTime) and isinstance(v, str):
        return datetime.fromisoformat(v)
    if isinstance(t, Date) and isinstance(v, str):
        return date.fromisoformat(v)
    if isinstance(t, Uuid) and isinstance(v, str):
        return uuid.UUID(v)
    if isinstance(t, Numeric) and isinstance(v, (str, int, float)) and not isinstance(t, Integer):
        return Decimal(str(v))
    if isinstance(t, SAEnum) and t.enum_class is not None and isinstance(v, str):
        return t.enum_class[v]
    if isinstance(t, LargeBinary) and isinstance(v, str):
        return base64.b64decode(v)
    return v


# ---------- 내보내기 ----------
def export_bytes() -> tuple[bytes, dict]:
    tables, counts = {}, {}
    with engine.connect() as c:
        for t in Base.metadata.sorted_tables:
            rows = [{k: _plain(v) for k, v in r._mapping.items()} for r in c.execute(select(t))]
            tables[t.name] = rows
            counts[t.name] = len(rows)
    files = {}
    adir = Path(settings.ATTACHMENT_DIR)
    if adir.is_dir():
        for f in sorted(adir.iterdir()):
            if f.is_file():
                raw = f.read_bytes()
                try:
                    files[f.name] = (decrypt_bytes(raw), True)
                except CryptoError:
                    files[f.name] = (raw, False)
    meta = {"format": 1, "exported_at": datetime.now(timezone.utc).isoformat(), "counts": counts, "files": len(files),
            "encrypted_files": [n for n, (_, e) in files.items() if e]}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("meta.json", json.dumps(meta, ensure_ascii=False))
        z.writestr("data.json", json.dumps(tables, ensure_ascii=False))
        for name, (data, _) in files.items():
            z.writestr(f"files/{name}", data)
    return buf.getvalue(), meta


def _open(blob: bytes, password: str) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(unseal(blob, password)))


def read_meta(blob: bytes, password: str) -> dict:
    with _open(blob, password) as z:
        return json.loads(z.read("meta.json"))


# ---------- 가져오기 ----------
def import_bytes(blob: bytes, password: str) -> dict:
    with _open(blob, password) as z:
        meta = json.loads(z.read("meta.json"))
        tables = json.loads(z.read("data.json"))
        files = {n[len("files/"):]: z.read(n) for n in z.namelist() if n.startswith("files/") and not n.endswith("/")}
    known = {t.name: t for t in Base.metadata.sorted_tables}
    skipped_tables = [n for n in tables if n not in known]
    dropped_cols: dict[str, set] = {}
    with engine.begin() as c:
        c.execute(text("TRUNCATE " + ", ".join(f'"{n}"' for n in known) + " RESTART IDENTITY CASCADE"))
        for t in Base.metadata.sorted_tables:
            rows = tables.get(t.name) or []
            if not rows:
                continue
            cols = {col.name: col for col in t.columns}
            # 같은 표의 다른 줄을 가리키는 칸(이전 상환일정 버전, 대환 전·후 차입금)은 먼저 비워 넣고 나중에 채운다
            selfref = [fk.parent.name for fk in t.foreign_keys if fk.column.table is t]
            out, later = [], []
            for r in rows:
                extra = set(r) - set(cols)
                if extra:
                    dropped_cols.setdefault(t.name, set()).update(extra)
                row = {k: _to_db(cols[k], v) for k, v in r.items() if k in cols}
                links = {k: row[k] for k in selfref if row.get(k) is not None}
                if links:
                    later.append((row, links))
                    row.update({k: None for k in links})
                out.append(row)
            for i in range(0, len(out), 1000):
                c.execute(t.insert(), out[i:i + 1000])
            if later:
                pk = list(t.primary_key.columns)[0]
                for row, links in later:
                    keep = {col.name: row.get(col.name) for col in t.columns if col.onupdate is not None}   # 수정 시각 등은 원래 값 그대로
                    c.execute(t.update().where(pk == row[pk.name]).values(**links, **keep))
        # 자동 번호(정수 키)는 가져온 최대값 다음부터
        for t in Base.metadata.sorted_tables:
            for col in t.primary_key.columns:
                if isinstance(col.type, Integer):
                    seq = c.execute(text("SELECT pg_get_serial_sequence(:t, :c)"), {"t": t.name, "c": col.name}).scalar()
                    if seq:
                        c.execute(text(f'SELECT setval(:s, COALESCE((SELECT MAX("{col.name}") FROM "{t.name}"), 0) + 1, false)'),
                                  {"s": seq})
    adir = Path(settings.ATTACHMENT_DIR)
    adir.mkdir(parents=True, exist_ok=True)
    enc = set(meta.get("encrypted_files") or [])
    for name, data in files.items():
        (adir / name).write_bytes(encrypt_bytes(data) if name in enc else data)
    return {"counts": {n: len(tables.get(n) or []) for n in known}, "files": len(files),
            "skipped_tables": skipped_tables, "dropped_columns": {k: sorted(v) for k, v in dropped_cols.items()},
            "exported_at": meta.get("exported_at")}


# ---------- 명령 ----------
KEY = [("loan", "차입금"), ("dormitory", "기숙사"), ("repayment_schedule_row", "상환일정 행"), ("actual_payment", "납부 기록"),
       ("generated_document", "안내공문"), ("bank_deposit", "입금내역"), ("app_user", "사용자"), ("audit_log", "감사로그")]


def _summary(counts: dict, files: int) -> str:
    return " · ".join(f"{label} {counts.get(k, 0):,}건" for k, label in KEY) + f" · 증빙파일 {files}개"


def _ask_password(confirm: bool) -> str:
    pw = os.environ.get("TRANSFER_PASSWORD")
    if pw:
        return pw
    pw = getpass.getpass("이관 비밀번호(입력해도 화면에 보이지 않습니다): ")
    if confirm:
        if len(pw) < 8:
            sys.exit("[오류] 이관 비밀번호는 8자 이상으로 정하세요.")
        if getpass.getpass("한 번 더 입력: ") != pw:
            sys.exit("[오류] 두 비밀번호가 다릅니다. 다시 실행하세요.")
    return pw


def main() -> None:
    ap = argparse.ArgumentParser(description="데이터 옮기기(이관)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("--out")
    i = sub.add_parser("import")
    i.add_argument("file")
    i.add_argument("--yes", action="store_true", help="확인 질문 없이 진행")
    f = sub.add_parser("info")
    f.add_argument("file")
    a = ap.parse_args()
    try:
        if a.cmd == "export":
            pw = _ask_password(confirm=True)
            data, meta = export_bytes()
            out = Path(a.out) if a.out else Path(settings.BACKUP_DIR) / f"transfer_{datetime.now():%Y%m%d_%H%M%S}.dlt"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(seal(data, pw))
            print(f"\n[완료] 내보냈습니다: {out}")
            print("  " + _summary(meta["counts"], meta["files"]))
            print("  이 파일과 이관 비밀번호가 있으면 다른 환경에서 가져올 수 있습니다. 파일과 비밀번호는 따로 보관하세요.")
        else:
            path = Path(a.file)
            if not path.is_file():
                sys.exit(f"[오류] 파일이 없습니다: {path}")
            blob = path.read_bytes()
            pw = _ask_password(confirm=False)
            meta = read_meta(blob, pw)
            print(f"[파일] 내보낸 시각 {meta['exported_at'][:19].replace('T', ' ')} (UTC)")
            print("  " + _summary(meta["counts"], meta["files"]))
            if a.cmd == "info":
                return
            if not a.yes:
                ans = input("\n지금 이 환경의 데이터를 모두 지우고 위 내용으로 바꿉니다. 계속하려면 yes 를 입력하세요: ").strip().lower()
                if ans != "yes":
                    sys.exit("취소했습니다. 아무것도 바뀌지 않았습니다.")
            r = import_bytes(blob, pw)
            print("\n[완료] 가져왔습니다. 서버를 다시 켜고(bash .devcontainer/start.sh 또는 2_start_windows.bat) 예전 계정으로 로그인하세요.")
            if r["skipped_tables"]:
                print("  [참고] 이 버전에 없는 표는 건너뜀: " + ", ".join(r["skipped_tables"]))
            if r["dropped_columns"]:
                print("  [참고] 이 버전에 없는 칸은 건너뜀: " + "; ".join(f"{k}({', '.join(v)})" for k, v in r["dropped_columns"].items()))
    except (TransferError, CryptoError) as e:
        sys.exit(f"[오류] {e}")
    except Exception as e:  # noqa: BLE001  (DB 오류 — 전체가 되돌려져 아무것도 바뀌지 않음)
        first = str(getattr(e, "orig", e)).splitlines()[0][:300]
        sys.exit(f"[오류] 가져오기 중 문제가 생겨 모두 되돌렸습니다(현재 데이터는 그대로입니다).\n  {type(e).__name__}: {first}")


if __name__ == "__main__":
    main()
