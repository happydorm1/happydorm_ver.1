"""
공휴일 원천 데이터 — 공공데이터포털 '한국천문연구원 특일 정보'(getRestDeInfo, 공휴일 정보).
DB를 모르는 순수 모듈: 응답을 (날짜, 이름) 목록으로 바꾸는 파서와, 연도별로 받아오는 함수.
네트워크 호출은 fetcher로 주입받는다(운영: urllib, 테스트: 녹화된 응답).

응답 형식(_type=json): response.header.resultCode == "00",
  response.body.items.item 이 [항목...] 또는 항목 1개(dict) 또는 ""(해당 월 없음)
  항목: {"dateKind": "01", "dateName": "설날", "isHoliday": "Y", "locdate": 20260216, "seq": 1}
"""
import json
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import date

Fetcher = Callable[[str, dict], dict]


class HolidaySourceError(Exception):
    pass


def parse_kasi_response(payload: dict) -> list[tuple[date, str]]:
    try:
        resp = payload["response"]
        code = str(resp["header"]["resultCode"])
    except (KeyError, TypeError):
        raise HolidaySourceError("공휴일 API 응답 형식이 예상과 다릅니다.")
    if code != "00":
        msg = resp["header"].get("resultMsg", "")
        raise HolidaySourceError(f"공휴일 API 오류(resultCode={code}) {msg}".strip())
    items = (resp.get("body") or {}).get("items") or {}
    item = items.get("item") if isinstance(items, dict) else None
    if not item:
        return []
    if isinstance(item, dict):
        item = [item]
    out = []
    for it in item:
        if str(it.get("isHoliday", "")).upper() != "Y":
            continue
        s = str(it["locdate"])
        out.append((date(int(s[:4]), int(s[4:6]), int(s[6:8])), str(it.get("dateName", "")).strip() or "공휴일"))
    return out


def http_fetcher(url: str, params: dict) -> dict:
    """운영용: 표준 라이브러리로 호출(프록시 환경변수 자동 적용). 서비스키는 이미 인코딩된 값일 수 있어 그대로 붙인다."""
    key = params.pop("ServiceKey")
    q = urllib.parse.urlencode(params)
    full = f"{url}?ServiceKey={key}&{q}"
    try:
        with urllib.request.urlopen(full, timeout=15) as r:
            body = r.read().decode("utf-8")
    except Exception as e:  # 네트워크/HTTP 오류
        raise HolidaySourceError(f"공휴일 API 호출 실패: {e}")
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        # 키 오류 등은 XML로 돌아온다
        raise HolidaySourceError(f"공휴일 API가 JSON이 아닌 응답을 보냈습니다(서비스키 확인): {body[:200]}")


def fetch_year(year: int, *, service_key: str, url: str, fetcher: Fetcher = http_fetcher) -> list[tuple[date, str]]:
    """월별로 12번 호출해 한 해 공휴일을 모은다. 하나라도 실패하면 전체 실패(부분 반영 방지)."""
    result: dict[date, str] = {}
    for month in range(1, 13):
        payload = fetcher(url, {"ServiceKey": service_key, "solYear": str(year), "solMonth": f"{month:02d}",
                                "_type": "json", "numOfRows": "50"})
        for d, name in parse_kasi_response(payload):
            if d.year == year:
                result[d] = name if d not in result else f"{result[d]}, {name}"
    return sorted(result.items())


def bank_rule_dates(year: int, rules: str) -> list[tuple[date, str]]:
    """'MM-DD:이름,MM-DD:이름' 형식의 은행 휴무 규칙을 해당 연도 날짜로 바꾼다."""
    out = []
    for part in (rules or "").split(","):
        part = part.strip()
        if not part:
            continue
        md, _, name = part.partition(":")
        m, d = (int(x) for x in md.split("-"))
        out.append((date(year, m, d), name.strip() or "은행 휴무"))
    return out
