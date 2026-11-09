"""데이터 옮기기(이관) — 파일 암호화와 암호화 값 다시 감싸기."""
import datetime as dt
import uuid

import pytest

from app.core.crypto import decrypt_str, encrypt_str, is_encrypted
from app.jobs.transfer import TransferError, _plain, _rewrap, seal, unseal


def test_seal_roundtrip_and_wrong_password():
    blob = seal(b"hello", "pw-12345678")
    assert unseal(blob, "pw-12345678") == b"hello"
    with pytest.raises(TransferError):
        unseal(blob, "wrong-password")
    with pytest.raises(TransferError):
        unseal(b"not-a-file", "pw-12345678")


def test_encrypted_values_are_unwrapped_and_rewrapped():
    acct = encrypt_str("123-456-789")
    row = {"a": acct, "j": {"bank_account": acct, "list": [acct, "x"]}, "d": dt.date(2026, 11, 9), "u": uuid.UUID(int=1)}
    p = _plain(row)
    assert p["a"] == {"$enc": "123-456-789"} and p["j"]["list"][1] == "x"
    assert p["d"] == "2026-11-09" and p["u"] == str(uuid.UUID(int=1))
    back = _rewrap(p)
    assert is_encrypted(back["a"]) and decrypt_str(back["a"]) == "123-456-789"
    assert decrypt_str(back["j"]["list"][0]) == "123-456-789"
