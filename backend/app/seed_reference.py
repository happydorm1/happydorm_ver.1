"""
최초 배포 시 1회 실행하는 기본 기준정보 시드.
    python -m app.seed_reference
문서 요구사항: 재원(§5)·상환주기(§14)·상환유형(§16)·거치유형(§10)의 "기본값"만 심어두고,
이후 관리자가 화면에서 추가할 수 있다(44번 요구사항은 '추가'가 가능해야 한다는 것이지,
기본값을 코드로 넣지 말라는 뜻은 아님).
"""
from app.core.database import SessionLocal
from app.models.reference import Code, CodeGroup, Fund, InterestMethod


def seed():
    db = SessionLocal()
    try:
        if not db.query(Fund).filter_by(fund_name="공공자금관리기금").first():
            db.add(Fund(fund_name="공공자금관리기금", default_day_count_basis=365, interest_method=InterestMethod.SIMPLE))
        if not db.query(Fund).filter_by(fund_name="주택도시기금").first():
            db.add(Fund(fund_name="주택도시기금", default_day_count_basis=365, interest_method=InterestMethod.PPMT_IPMT))

        default_codes = [
            (CodeGroup.REPAY_CYCLE, "QUARTERLY", "분기", {"periods_per_year": 4}, 1),
            (CodeGroup.REPAY_CYCLE, "SEMIANNUAL", "반기", {"periods_per_year": 2}, 2),
            (CodeGroup.REPAY_CYCLE, "MONTHLY", "월", {"periods_per_year": 12}, 3),
            (CodeGroup.REPAY_METHOD, "EQUAL_PRINCIPAL", "원금균등", None, 1),
            (CodeGroup.REPAY_METHOD, "EQUAL_PAYMENT", "원리금균등", None, 2),
            (CodeGroup.REPAY_METHOD, "MATURITY", "만기일시", None, 3),
            (CodeGroup.REPAY_METHOD, "CUSTOM", "사용자정의", None, 4),
            (CodeGroup.GRACE_TYPE, "GENERAL", "일반거치", None, 1),
            (CodeGroup.GRACE_TYPE, "CONSTRUCTION", "건설거치", None, 2),
            (CodeGroup.GRACE_TYPE, "PRINCIPAL_ONLY", "원금거치", None, 3),
            (CodeGroup.GRACE_TYPE, "CUSTOM", "원금·이자 별도처리", None, 4),
            (CodeGroup.DEFER_TARGET, "PRINCIPAL", "원금", None, 1),
            (CodeGroup.DEFER_TARGET, "PRINCIPAL_INTEREST", "원금+이자", None, 2),
            (CodeGroup.DEFER_TARGET, "INTEREST", "이자", None, 3),
        ]
        for group, code, label, extra, order in default_codes:
            exists = db.query(Code).filter_by(group_code=group.value, code=code).first()
            if not exists:
                db.add(Code(group_code=group.value, code=code, label=label, extra_value=extra, sort_order=order))

        db.commit()
        print("기준정보 시드 완료")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
