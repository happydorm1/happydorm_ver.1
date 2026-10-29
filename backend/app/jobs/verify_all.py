"""
전체 차입금 계산 검증(§37) 일괄 실행 — 운영 중 정기 점검·이관 직후 확인용.
    python -m app.jobs.verify_all              # 현재 버전
    python -m app.jobs.verify_all --all-versions
"""
import argparse
from collections import Counter

from sqlalchemy import select

from app.core.database import SessionLocal
from app.domain.verification_service import verify_loan
from app.models.loan import Loan
from app.models.schedule import RepaymentScheduleVersion


def run(all_versions: bool = False) -> dict:
    db = SessionLocal()
    try:
        results = []
        for loan in db.execute(select(Loan).where(Loan.is_deleted.is_(False)).order_by(Loan.loan_code)).scalars():
            nos = [None] if not all_versions else [v for (v,) in db.execute(
                select(RepaymentScheduleVersion.version_no).where(RepaymentScheduleVersion.loan_id == loan.id,
                                                                  RepaymentScheduleVersion.is_deleted.is_(False))
                .order_by(RepaymentScheduleVersion.version_no))]
            for no in nos:
                r = verify_loan(db, loan.id, no)
                if r:
                    results.append(r)
        per_check = Counter((c["no"], c["status"]) for r in results for c in r["checks"])
        return {"versions": len(results), "needs_review": [r for r in results if r["overall"] != "NORMAL"], "per_check": per_check}
    finally:
        db.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all-versions", action="store_true")
    out = run(ap.parse_args().all_versions)
    print(f"검증한 원리금 테이블 {out['versions']}개, 검토 필요 {len(out['needs_review'])}개")
    for no in range(1, 10):
        print(f"  {no}. PASS {out['per_check'][(no, 'PASS')]:4}  FAIL {out['per_check'][(no, 'FAIL')]:3}  NA {out['per_check'][(no, 'NA')]:3}")
    for r in out["needs_review"][:30]:
        print(f"  - {r['loan_code']} V{r['version_no']}: 실패 항목 {r['failed']} — "
              + " / ".join(c["detail"] for c in r["checks"] if c["status"] == "FAIL")[:200])


if __name__ == "__main__":
    main()
