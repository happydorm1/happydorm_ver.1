"""
입금 확인·승인(Phase 23) E2E — 공문을 발급하고 입금을 등록하므로 '새로 만든 데모 DB'에서 돌린다.
    python e2e/deposits.py http://127.0.0.1:8000 [스크린샷 폴더]
확인: 메뉴(재단 직원만), 입금내역 양식·업로드 → 공문 금액과 같은 입금은 자동 승인(2천만 원 미만) 또는 대기(이상) → 승인 →
상환 실적 반영, 대상 없는 입금 → 대기(대상 공문 없음) → 제외(사유), 되돌리기, 휴대폰 폭.
"""
import datetime as dt
import json
import sys
import tempfile
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from openpyxl import Workbook
from playwright.sync_api import expect, sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
SHOTS = sys.argv[2] if len(sys.argv) > 2 else None
TODAY = dt.date.today()


def shot(page, name):
    if SHOTS:
        page.screenshot(path=f"{SHOTS}/{name}.png", full_page=True)


def api(path, token, body=None, params=None):
    url = f"{BASE}/api/v1{path}" + (("?" + urllib.parse.urlencode(params)) if params else "")
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))


def token(email):
    r = urllib.request.urlopen(urllib.request.Request(f"{BASE}/api/v1/auth/login",
                               data=urllib.parse.urlencode({"username": email, "password": "demo1234!"}).encode()))
    return json.load(r)["access_token"]


def login(page, email):
    page.goto(BASE)
    page.fill("input[type=email]", email)
    page.fill("input[type=password]", "demo1234!")
    page.click("button[type=submit]")
    page.wait_for_selector("text=차입금 현황")
    try:
        page.get_by_role("alertdialog").wait_for(timeout=2500)
        page.get_by_role("button", name="나중에").click()
    except Exception:
        pass
    _m = page.get_by_role("navigation", name="주 메뉴")
    while _m.locator("button[aria-expanded='false']:visible").count():
        _m.locator("button[aria-expanded='false']:visible").first.click()


# 1) 오늘 이후 상환일이 있는 기숙사에 공문 발급(API)
tok = token("demo-admin@example.com")
dash = api("/dashboard", tok, params={"as_of": TODAY.isoformat()})
target = None
for ln in dash["loans"]:
    if ln.get("next_due_date") and ln["next_due_date"] >= TODAY.isoformat() and ln["status"] == "ACTIVE":
        target = ln
        break
assert target, "오늘 이후 상환일이 있는 데모 차입금이 없음"
notice = api(f"/dormitories/{target['dormitory_id']}/notices", tok, body={"due_date": target["next_due_date"]})
total = notice["generated_data"]["totals"]["total"]
dname = notice["generated_data"]["dormitory"]["name"]
over = total >= 20_000_000

tmp = Path(tempfile.mkdtemp())
wb = Workbook()
ws = wb.active
ws.append(["거래일시", "적요", "보낸분", "출금액", "맡기신금액", "잔액"])           # 은행 파일 모양(열 이름 별칭)
ws.append([f"{TODAY:%Y.%m.%d} 10:21", "원리금", f"{dname}(주)", 0, total, 0])
who = f"미확인입금{uuid.uuid4().hex[:4]}"
ws.append([f"{TODAY:%Y.%m.%d} 11:02", "기타", who, 0, 123_456, 0])
ws.append([f"{TODAY:%Y.%m.%d} 12:00", "이체", "출금건", 500_000, 0, 0])
xlsx = tmp / "bank.xlsx"
wb.save(xlsx)

with sync_playwright() as p:
    b = p.chromium.launch()
    errors = []

    # SPC 담당자에게는 메뉴 없음
    spc = b.new_page(viewport={"width": 1300, "height": 900})
    login(spc, "demo-spc@example.com")
    expect(spc.get_by_role("navigation", name="주 메뉴").get_by_role("link", name="입금 확인·승인")).to_have_count(0)
    spc.close()

    page = b.new_page(viewport={"width": 1500, "height": 950}, accept_downloads=True)
    page.on("pageerror", lambda e: errors.append(str(e)))
    login(page, "demo-admin@example.com")
    page.get_by_role("navigation", name="주 메뉴").get_by_role("link", name="입금 확인·승인").click()
    expect(page.get_by_role("main").get_by_role("heading", name="입금 확인·승인")).to_be_visible()
    with page.expect_download() as d:
        page.get_by_role("button", name="입금내역 양식 내려받기").click()
    assert d.value.suggested_filename.endswith(".xlsx")

    page.get_by_label("입금내역 파일").set_input_files(str(xlsx))
    page.get_by_role("button", name="올리고 공문과 맞추기").click()
    expect(page.get_by_text("입금 2건 등록")).to_be_visible()
    expect(page.get_by_text(f"자동 승인 {0 if over else 1}건, 대기 {2 if over else 1}건")).to_be_visible()
    tbl = page.get_by_role("table", name="입금내역")
    unk = tbl.locator("tr", has_text=who)
    expect(unk).to_contain_text("대상 공문 없음")
    shot(page, "dep01_pending")

    # 같은 파일 다시 → 중복으로 건너뜀
    page.get_by_label("입금내역 파일").set_input_files(str(xlsx))
    page.get_by_role("button", name="올리고 공문과 맞추기").click()
    expect(page.get_by_text("입금 0건 등록")).to_be_visible()
    expect(page.get_by_text("이미 등록된 거래 2건은 건너뜀")).to_be_visible()

    mine = tbl.locator("tr", has_text=f"{dname}(주)")
    if over:
        expect(mine).to_contain_text("2,000만 원 이상")
        mine.get_by_role("button", name="승인").click()
        dlg = page.get_by_role("dialog", name="입금 승인")
        expect(dlg).to_contain_text("이 입금을 승인하시겠습니까?")
        dlg.get_by_label("승인 메모").fill("통장 확인")
        dlg.get_by_role("button", name="예, 승인합니다").click()
        expect(page.get_by_text("승인했습니다")).to_be_visible()

    # 대상 없는 입금 → 제외(사유 필수)
    unk.get_by_role("button", name="제외").click()
    ex = page.get_by_role("dialog", name="입금 제외")
    expect(ex.get_by_role("button", name="예, 제외합니다")).to_be_disabled()
    ex.get_by_label("사유").fill("임대료 입금")
    ex.get_by_role("button", name="예, 제외합니다").click()
    expect(page.get_by_text("제외했습니다")).to_be_visible()
    expect(page.get_by_role("main")).not_to_contain_text(who)              # 대기 목록에서 빠짐

    page.get_by_role("tab", name="완료", exact=False).first.click()
    done_row = page.get_by_role("table", name="입금내역").locator("tr", has_text=f"{dname}(주)")
    expect(done_row).to_contain_text("완료 · 승인" if over else "완료 · 자동 승인")
    expect(done_row).to_contain_text("납부 기록 1건")
    shot(page, "dep02_done")
    summ = api(f"/loans/{target['loan_id']}/payments/summary", tok, params={"as_of": TODAY.isoformat()})
    row = next(r for r in summ["rows"] if r["due_date"] == target["next_due_date"])
    assert row["paid_total"] > 0, row

    # 되돌리기 → 대기, 납부 기록 취소
    done_row.get_by_role("button", name="되돌리기").click()
    rv = page.get_by_role("dialog", name="되돌리기")
    rv.get_by_label("사유").fill("E2E 되돌림")
    rv.get_by_role("button", name="예, 되돌립니다").click()
    expect(page.get_by_text("대기로 되돌렸습니다")).to_be_visible()
    summ = api(f"/loans/{target['loan_id']}/payments/summary", tok, params={"as_of": TODAY.isoformat()})
    assert next(r for r in summ["rows"] if r["due_date"] == target["next_due_date"])["paid_total"] == 0

    page.set_viewport_size({"width": 390, "height": 800})
    page.goto(BASE + "/#/deposits")
    page.wait_for_timeout(800)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "가로 넘침"
    b.close()
    assert not errors, errors

print(f"E2E OK: SPC 메뉴 없음, 양식 내려받기, 은행 모양 파일 업로드(출금 제외) → {'대기(2천만 원 이상) → 승인' if over else '자동 승인'}, "
      "대상 없음 → 제외(사유), 재업로드 중복 건너뜀, 완료 → 상환 실적 반영, 되돌리기 → 납부 기록 취소, 휴대폰 폭")
