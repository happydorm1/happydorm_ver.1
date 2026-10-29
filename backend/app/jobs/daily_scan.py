"""
일일 알림 배치 — 외부 스케줄러(cron, 작업 스케줄러 등)가 하루 한 번 실행한다.
    python -m app.jobs.daily_scan                    # 오늘 기준
    python -m app.jobs.daily_scan --as-of 2026-08-24  # 특정일 기준(재실행/점검용)
여러 번 실행해도 같은 알림이 중복 생성되지 않는다.
알림을 만들기 전에 올해·내년 공휴일을 공식 데이터로 갱신한다(서비스키가 있고 마지막 동기화가 오래된 경우에만).
공휴일 갱신이 실패해도 알림 배치는 계속 진행하고 결과에 실패를 표시한다.
"""
import argparse
import json
from datetime import date

from app.core.database import SessionLocal
from app.domain.holiday_sync import ensure_holidays, retire_stale_bank_rules
from app.domain.notification_service import scan_notifications


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", type=date.fromisoformat, default=None)
    args = parser.parse_args()
    db = SessionLocal()
    try:
        as_of = args.as_of or date.today()
        retired = retire_stale_bank_rules(db)
        holidays = ensure_holidays(db, {as_of.year, as_of.year + 1})
        result = scan_notifications(db, as_of)
        from app.jobs.verify_all import run as verify_run      # §37 계산 검증을 매일 전체 차입금에 다시 돌린다
        v = verify_run(False)
        verification = {"checked": v["versions"],
                        "needs_review": [{"loan_code": r["loan_code"], "version_no": r["version_no"], "failed": r["failed"]}
                                         for r in v["needs_review"]]}
        print(json.dumps({"holiday_sync": holidays, "bank_rules_retired": retired, "notifications": result,
                          "verification": verification}, ensure_ascii=False, default=str))
    finally:
        db.close()


if __name__ == "__main__":
    main()
