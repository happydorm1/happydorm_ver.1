"""공휴일 자동 동기화(한국천문연구원 특일 정보) — 실제 API는 이 환경에서 호출할 수 없어 응답 형식대로 만든 가짜 응답으로 검증"""
from datetime import date, datetime, timedelta, timezone

import pytest
from _helpers import admin_headers, client, make_org, spc_manager_headers

import app.domain.holiday_sync as hs
from app.core.config import settings
from app.core.database import SessionLocal
from app.engine.holiday_source import HolidaySourceError, bank_rule_dates, parse_kasi_response
from app.models.calendar import Holiday, HolidaySyncLog

Y = 2091  # 다른 테스트와 겹치지 않는 연도


def _resp(items):
    """getRestDeInfo JSON 형식. items: list → item 배열, dict → 단건, None → 빈 문자열(해당 월 없음)"""
    body_items = "" if items is None else {"item": items}
    return {"response": {"header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE."},
                         "body": {"items": body_items, "numOfRows": 50, "pageNo": 1, "totalCount": 0}}}


def _it(d, name, holiday="Y"):
    return {"dateKind": "01", "dateName": name, "isHoliday": holiday, "locdate": int(d.strftime("%Y%m%d")), "seq": 1}


BASE = {
    1: [_it(date(Y, 1, 1), "1월1일")],
    3: _it(date(Y, 3, 1), "삼일절"),                                    # 단건(dict)으로 오는 달
    5: [_it(date(Y, 5, 5), "어린이날"), _it(date(Y, 5, 6), "기념일", holiday="N")],   # 공휴일 아님은 제외
    6: [_it(date(Y, 6, 6), "현충일")],
    9: [_it(date(Y, 9, 17), "추석"), _it(date(Y, 9, 18), "추석")],
    10: [_it(date(Y, 10, 3), "개천절"), _it(date(Y, 10, 9), "한글날")],
}


def make_fetcher(by_month, calls=None, fail_month=None, bad_code=False):
    def fetcher(url, params):
        assert params["ServiceKey"] == "TEST-KEY" and params["_type"] == "json" and params["solYear"] == str(Y)
        m = int(params["solMonth"])
        if calls is not None:
            calls.append(m)
        if fail_month == m:
            raise HolidaySourceError("네트워크 오류(테스트)")
        if bad_code:
            return {"response": {"header": {"resultCode": "30", "resultMsg": "SERVICE_KEY_IS_NOT_REGISTERED_ERROR"}}}
        return _resp(by_month.get(m))
    return fetcher


@pytest.fixture(autouse=True)
def clean_year(monkeypatch):
    monkeypatch.setattr(settings, "KASI_SERVICE_KEY", "TEST-KEY")
    monkeypatch.setattr(settings, "BANK_EXTRA_CLOSURES", "")   # 확정 업무기준: 공휴일 외 추가 휴무 없음
    def wipe():
        db = SessionLocal()
        db.query(Holiday).filter(Holiday.holiday_date >= date(Y, 1, 1), Holiday.holiday_date <= date(Y + 1, 12, 31)).delete()
        db.query(HolidaySyncLog).filter(HolidaySyncLog.year.in_([Y, Y + 1])).delete()
        db.commit()
        db.close()
    wipe()
    yield
    wipe()


def _rows():
    db = SessionLocal()
    try:
        return {h.holiday_date: (h.holiday_name, h.holiday_type, h.source, h.is_deleted)
                for h in db.query(Holiday).filter(Holiday.holiday_date >= date(Y, 1, 1), Holiday.holiday_date <= date(Y, 12, 31))}
    finally:
        db.close()


def test_parser_handles_list_single_empty_and_errors():
    assert parse_kasi_response(_resp(None)) == []
    assert parse_kasi_response(_resp(_it(date(Y, 3, 1), "삼일절"))) == [(date(Y, 3, 1), "삼일절")]
    assert parse_kasi_response(_resp([_it(date(Y, 5, 5), "어린이날"), _it(date(Y, 5, 6), "x", "N")])) == [(date(Y, 5, 5), "어린이날")]
    with pytest.raises(HolidaySourceError):
        parse_kasi_response({"response": {"header": {"resultCode": "22", "resultMsg": "LIMITED"}}})
    with pytest.raises(HolidaySourceError):
        parse_kasi_response({"unexpected": 1})
    assert bank_rule_dates(Y, "05-01:근로자의 날,12-31:연말") == [(date(Y, 5, 1), "근로자의 날"), (date(Y, 12, 31), "연말")]


def test_sync_creates_public_holidays_only_and_is_idempotent():
    db = SessionLocal()
    calls = []
    log = hs.sync_year(db, Y, fetcher=make_fetcher(BASE, calls))
    assert calls == list(range(1, 13))                                      # 12개월 모두 조회
    assert (log.status, log.fetched, log.created, log.deactivated) == ("SUCCESS", 8, 8, 0)   # 법정공휴일 8건만
    rows = _rows()
    assert rows[date(Y, 3, 1)] == ("삼일절", "PUBLIC", "KASI", False)
    assert date(Y, 5, 1) not in rows and date(Y, 12, 31) not in rows        # 5/1·12/31은 휴무일 아님(확정)
    assert date(Y, 5, 6) not in rows                                       # isHoliday=N 제외

    again = hs.sync_year(db, Y, fetcher=make_fetcher(BASE))
    assert (again.created, again.updated, again.deactivated) == (0, 0, 0)  # 다시 돌려도 변화 없음
    db.close()


def test_manual_entries_are_never_overwritten_and_cancelled_holidays_are_deactivated():
    db = SessionLocal()
    db.add(Holiday(holiday_date=date(Y, 6, 6), holiday_name="담당자 입력 휴일", holiday_type="OTHER", source="MANUAL"))
    db.add(Holiday(holiday_date=date(Y, 8, 14), holiday_name="담당자 지정 휴무", holiday_type="OTHER", source="MANUAL"))
    db.commit()
    hs.sync_year(db, Y, fetcher=make_fetcher(BASE))
    assert _rows()[date(Y, 6, 6)] == ("담당자 입력 휴일", "OTHER", "MANUAL", False)     # KASI도 같은 날을 줬지만 보존

    # 임시공휴일 추가 발표 → 다음 동기화에서 반영
    with_temp = {**BASE, 8: [_it(date(Y, 8, 16), "임시공휴일")]}
    log = hs.sync_year(db, Y, fetcher=make_fetcher(with_temp))
    assert log.created == 1 and _rows()[date(Y, 8, 16)] == ("임시공휴일", "PUBLIC", "KASI", False)

    # 임시공휴일이 원천에서 빠지면(취소) 삭제가 아니라 사용중지, 담당자 입력분은 그대로
    log = hs.sync_year(db, Y, fetcher=make_fetcher(BASE))
    assert log.deactivated == 1 and _rows()[date(Y, 8, 16)][3] is True
    assert _rows()[date(Y, 8, 14)] == ("담당자 지정 휴무", "OTHER", "MANUAL", False)

    # 다시 발표되면 되살린다
    log = hs.sync_year(db, Y, fetcher=make_fetcher(with_temp))
    assert log.updated == 1 and _rows()[date(Y, 8, 16)][3] is False
    db.close()


def test_failure_changes_nothing_and_zero_results_delete_nothing():
    db = SessionLocal()
    hs.sync_year(db, Y, fetcher=make_fetcher(BASE))
    before = _rows()

    log = hs.sync_year(db, Y, fetcher=make_fetcher({**BASE, 2: [_it(date(Y, 2, 2), "새 휴일")]}, fail_month=7))
    assert log.status == "FAILED" and "네트워크" in log.message
    assert _rows() == before                                                # 7월에서 실패 → 2월 신규분도 반영 안 됨

    log = hs.sync_year(db, Y, fetcher=make_fetcher(BASE, bad_code=True))
    assert log.status == "FAILED" and "resultCode=30" in log.message and _rows() == before

    log = hs.sync_year(db, Y, fetcher=make_fetcher({}))                    # 0건(미발표/장애) → 기존 유지
    assert log.status == "SUCCESS" and log.deactivated == 0 and "0건" in log.message
    assert all(not v[3] for v in _rows().values())
    db.close()


def test_synced_holidays_drive_business_day_rules():
    h = admin_headers()
    db = SessionLocal()
    hs.sync_year(db, Y, fetcher=make_fetcher(BASE))
    db.close()
    # 추석(9/17~18)이 공휴일로 들어오면 9/17 상환일의 납부기한은 다음 영업일
    info = client.get("/api/v1/calendar/business-day", params={"date": f"{Y}-09-17"}, headers=h).json()
    assert info["is_business_day"] is False and info["holidays_missing_for_year"] is False
    nxt = date.fromisoformat(info["next_business_day"])
    assert nxt > date(Y, 9, 18) and nxt.weekday() < 5
    # 5/1(근로자의 날)은 은행 영업일 — 평일이면 영업일로 본다(확정 업무기준)
    may1 = date(Y, 5, 1)
    r = client.get("/api/v1/calendar/business-day", params={"date": may1.isoformat()}, headers=h).json()
    assert r["is_business_day"] is (may1.weekday() < 5)


def test_optional_extra_closures_and_retiring_old_bank_rules(monkeypatch):
    """기관 방침으로 추가 휴무일이 생기면 설정으로 넣을 수 있고, 규칙에서 빼면 사용중지(삭제 아님)된다."""
    db = SessionLocal()
    monkeypatch.setattr(settings, "BANK_EXTRA_CLOSURES", "05-01:근로자의 날(은행 휴무),12-31:은행 연말 휴무")
    log = hs.sync_year(db, Y, fetcher=make_fetcher(BASE))
    assert log.created == 10 and _rows()[date(Y, 5, 1)] == ("근로자의 날(은행 휴무)", "BANK", "BANK_RULE", False)

    monkeypatch.setattr(settings, "BANK_EXTRA_CLOSURES", "")
    assert hs.retire_stale_bank_rules(db) >= 2                              # 키 없이도 일일 배치에서 정리
    rows = _rows()
    assert rows[date(Y, 5, 1)][3] is True and rows[date(Y, 12, 31)][3] is True
    assert rows[date(Y, 3, 1)][3] is False                                  # 공휴일은 그대로
    monkeypatch.setattr(settings, "BANK_EXTRA_CLOSURES", "05-01:x")
    hs.sync_year(db, Y, fetcher=make_fetcher(BASE))
    monkeypatch.setattr(settings, "BANK_EXTRA_CLOSURES", "")
    log = hs.sync_year(db, Y, fetcher=make_fetcher(BASE))                  # 동기화 때도 규칙에서 빠진 날은 사용중지
    assert log.deactivated == 1 and _rows()[date(Y, 5, 1)][3] is True
    db.close()


def test_ensure_holidays_for_daily_batch(monkeypatch):
    db = SessionLocal()
    calls = []
    out = hs.ensure_holidays(db, {Y, Y + 1}, fetcher=lambda url, p: (calls.append(p["solYear"]), _resp(None))[1])
    assert [o["status"] for o in out] == ["SUCCESS", "SUCCESS"] and len(calls) == 24
    calls.clear()
    out = hs.ensure_holidays(db, {Y}, fetcher=lambda url, p: (calls.append(1), _resp(None))[1])
    assert out[0]["status"] == "UP_TO_DATE" and calls == []               # 최근(7일 이내) 동기화 → 건너뜀

    stale = db.query(HolidaySyncLog).filter(HolidaySyncLog.year == Y).all()
    for s in stale:
        s.synced_at = datetime.now(timezone.utc) - timedelta(days=8)
    db.commit()
    out = hs.ensure_holidays(db, {Y}, fetcher=lambda url, p: _resp(None))
    assert out[0]["status"] == "SUCCESS"                                   # 오래되면 다시 받음(임시공휴일 대비)

    monkeypatch.setattr(settings, "KASI_SERVICE_KEY", None)
    assert hs.ensure_holidays(db, {Y})[0]["status"] == "SKIPPED"            # 키 없으면 건너뛰고 배치는 계속
    db.close()


def test_sync_api_permissions_and_errors(monkeypatch):
    h = admin_headers()
    spc_id, _ = make_org(h)
    assert client.post("/api/v1/holidays/sync", params={"year": Y}, headers=spc_manager_headers(h, spc_id)).status_code == 403

    monkeypatch.setattr(hs, "http_fetcher", make_fetcher(BASE))
    r = client.post("/api/v1/holidays/sync", params={"year": Y}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "SUCCESS" and r.json()["created"] == 8
    assert r.json()["triggered_by"] == "pytest-admin@example.com"
    listed = client.get("/api/v1/holidays", params={"year": Y}, headers=h).json()
    assert {x["source"] for x in listed} == {"KASI"}
    assert client.get("/api/v1/holidays/sync-logs", params={"year": Y}, headers=h).json()[0]["status"] == "SUCCESS"

    monkeypatch.setattr(hs, "http_fetcher", make_fetcher(BASE, fail_month=1))
    assert client.post("/api/v1/holidays/sync", params={"year": Y}, headers=h).status_code == 502
    monkeypatch.setattr(settings, "KASI_SERVICE_KEY", None)
    r = client.post("/api/v1/holidays/sync", params={"year": Y}, headers=h)
    assert r.status_code == 503 and "KASI_SERVICE_KEY" in r.json()["detail"]
    assert client.get("/api/v1/holidays/sync-status", headers=h).json()["enabled"] is False
