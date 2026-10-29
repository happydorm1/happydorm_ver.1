"""
회계연도(1/1~12/31) 귀속 이자 — 결산용 참고값. 원리금 테이블 자체(납입 기준 금액)는 바꾸지 않는다.

- 원금균등(공자)·거치기간: 12/31 경계 앞·뒤 행이 이미 각각 일수로 계산된 실지급액이므로 그대로 쓴다.
- 원리금균등(주택, PPMT/IPMT): 회차 이자는 '잔액 × 금리 ÷ 연간 회차수'로 한 덩어리로 정해져
  뒤쪽(납입일) 행에 전액 실리고 앞쪽은 메모행(0원)이다. 이 경우 실제 부과된 회차 이자를
  메모행·실지급행의 일수 비율로 나눠 연도별로 귀속시킨다(두 값의 합 = 회차 이자, 원단위 오차 없음).
  실제 부과 이자 기준이라 상환유예·조기상환 등 이벤트로 이자가 바뀐 버전에도 그대로 맞는다.
"""
from collections import OrderedDict

from app.engine.schedule_engine import excel_round

MEMO = "YEAR_END_SPLIT_MEMO"


def _rtype(r) -> str:
    t = r.row_type
    return getattr(t, "value", t)


def year_attributed_interest(rows) -> list[int]:
    """seq_no 순으로 정렬된 행 목록 → 같은 순서의 기간귀속 이자(원)."""
    out = [int(r.interest_amount) for r in rows]
    for i, r in enumerate(rows):
        if _rtype(r) != MEMO:
            continue
        j = next((k for k in range(i + 1, len(rows))
                  if rows[k].due_date == r.due_date and _rtype(rows[k]) != MEMO), None)
        if j is None:
            out[i] = 0
            continue
        pair = rows[j]
        total = int(pair.interest_amount)
        d1, d2 = int(r.days), int(pair.days)
        part = excel_round(total * d1 / (d1 + d2)) if (d1 + d2) else 0
        out[i] = part
        out[j] = total - part
    return out


def yearly_summary(rows) -> list[dict]:
    """연도별 집계: 원금·이자 상환액(납입일 기준)과 기간귀속 이자(이자계산기간 기준, 1/1~12/31)."""
    rows = sorted(rows, key=lambda x: x.seq_no)
    attributed = year_attributed_interest(rows)
    years: "OrderedDict[int, dict]" = OrderedDict()

    def bucket(y):
        return years.setdefault(y, {"year": y, "principal_paid": 0, "interest_paid": 0, "interest_attributed": 0})

    for r, att in zip(rows, attributed):
        if _rtype(r) != MEMO:
            b = bucket(r.due_date.year)
            b["principal_paid"] += int(r.principal_amount) + int(getattr(r, "deferral_recovery_principal", 0) or 0)
            b["interest_paid"] += int(r.interest_amount) + int(r.unpaid_interest_amount)
        # 기간귀속: 이자계산기간이 속한 연도(12/31 경계로 이미 나뉘어 있어 끝일의 연도로 판정)
        bucket(r.interest_calc_end.year)["interest_attributed"] += att + int(r.unpaid_interest_amount)
    return sorted(years.values(), key=lambda b: b["year"])
