"""
민감정보 암호화(§45: 계좌번호 등 컬럼 암호화, 파일 AES-256) — AES-256-GCM.
- 키: DATA_ENCRYPTION_KEY(base64 32바이트, .env에만). 운영(production)에서는 필수.
  개발 환경에서 비어 있으면 SECRET_KEY에서 파생한 키를 쓴다(운영 금지 — 시작 검사에서 막힘).
- 암호문 형식: "enc:v1:" + base64(nonce 12바이트 + 암호문). 이 접두어가 없으면 평문으로 보고 그대로 돌려준다
  → 암호화 도입 전 데이터도 읽을 수 있고, `python -m app.jobs.encrypt_existing`으로 한 번에 암호화한다.
- 키를 잃으면 암호화된 값·백업은 복구할 수 없다. 키는 DB·백업과 다른 곳에 따로 보관한다.
키 생성: python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"
"""
import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import settings

PREFIX = "enc:v1:"


class CryptoError(Exception):
    pass


def _key() -> bytes:
    if settings.DATA_ENCRYPTION_KEY:
        try:
            k = base64.b64decode(settings.DATA_ENCRYPTION_KEY)
        except ValueError as e:
            raise CryptoError("DATA_ENCRYPTION_KEY가 base64 형식이 아닙니다.") from e
        if len(k) != 32:
            raise CryptoError("DATA_ENCRYPTION_KEY는 32바이트(AES-256)여야 합니다.")
        return k
    return hashlib.sha256(("dev-only:" + settings.SECRET_KEY).encode()).digest()


def encrypt_bytes(data: bytes) -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(_key()).encrypt(nonce, data, None)


def decrypt_bytes(blob: bytes) -> bytes:
    try:
        return AESGCM(_key()).decrypt(blob[:12], blob[12:], None)
    except Exception as e:  # noqa: BLE001
        raise CryptoError("복호화 실패 — 암호화 키가 다르거나 데이터가 손상되었습니다.") from e


def is_encrypted(value) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def encrypt_str(value: str | None) -> str | None:
    if value is None or value == "" or is_encrypted(value):
        return value
    return PREFIX + base64.b64encode(encrypt_bytes(value.encode("utf-8"))).decode()


def decrypt_str(value: str | None) -> str | None:
    if not is_encrypted(value):
        return value
    return decrypt_bytes(base64.b64decode(value[len(PREFIX):])).decode("utf-8")


def mask_account(value: str | None) -> str | None:
    """화면 목록용: 숫자는 끝 4자리만 남긴다. 예) '데모은행 000-000-001234' → '데모은행 ***-***-**1234'"""
    plain = decrypt_str(value)
    if not plain:
        return plain
    digits = [i for i, ch in enumerate(plain) if ch.isdigit()]
    keep = set(digits[-4:])
    return "".join("*" if (ch.isdigit() and i not in keep) else ch for i, ch in enumerate(plain))


# 공문에서 암호화하는 필드
ACCOUNT_FIELDS = ("bank_account",)


def protect_account(d: dict | None) -> dict | None:
    if not d:
        return d
    return {k: (encrypt_str(v) if k in ACCOUNT_FIELDS else v) for k, v in d.items()}


def reveal_account(d: dict | None) -> dict | None:
    if not d:
        return d
    return {k: (decrypt_str(v) if k in ACCOUNT_FIELDS else v) for k, v in d.items()}
