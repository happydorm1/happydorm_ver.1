"""
암호화 도입 전에 저장된 계좌번호(공문 양식·발급 공문 스냅샷)를 한 번에 암호화한다. 여러 번 실행해도 안전(이미 암호화된 값은 건너뜀).
    python -m app.jobs.encrypt_existing
"""
from sqlalchemy.orm.attributes import flag_modified

from app.core.crypto import is_encrypted, protect_account
from app.core.database import SessionLocal
from app.models.document import DocumentTemplate, GeneratedDocument


def main() -> None:
    db = SessionLocal()
    n_t = n_d = 0
    try:
        for t in db.query(DocumentTemplate).all():
            if t.config and t.config.get("bank_account") and not is_encrypted(t.config["bank_account"]):
                t.config = protect_account(t.config)
                flag_modified(t, "config")
                n_t += 1
        for d in db.query(GeneratedDocument).all():
            acc = (d.generated_data or {}).get("account") or {}
            if acc.get("bank_account") and not is_encrypted(acc["bank_account"]):
                d.generated_data = {**d.generated_data, "account": protect_account(acc)}
                flag_modified(d, "generated_data")
                n_d += 1
        db.commit()
    finally:
        db.close()
    print(f"암호화 완료: 공문 양식 {n_t}건, 발급 공문 {n_d}건")


if __name__ == "__main__":
    main()
