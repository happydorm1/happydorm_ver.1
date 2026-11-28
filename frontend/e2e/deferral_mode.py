"""
상환유예(원금) — '유예기간 이자 납부' 선택(2026-11-28). 새 데모 DB에서 실행.
    python e2e/deferral_mode.py http://127.0.0.1:8010 [스크린샷 폴더]
원금+이자 유예에는 선택칸이 없고, 원금 유예에서만 보임 → '정규 상환일에만'으로 적용(저장값은 DB deferral.interest_pay_mode).
"""
import sys

from playwright.sync_api import expect, sync_playwright

sys.path.insert(0, __file__.rsplit("/", 1)[0])
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8010"
SHOTS = sys.argv[2] if len(sys.argv) > 2 else None


def login(page, email):
    page.goto(BASE)
    page.fill("input[type=email]", email); page.fill("input[type=password]", "demo1234!")
    page.click("button[type=submit]"); page.wait_for_selector("text=차입금 현황")
    try:
        page.get_by_role("alertdialog").wait_for(timeout=2500); page.get_by_role("button", name="나중에").click()
    except Exception:
        pass
    m = page.get_by_role("navigation", name="주 메뉴")
    while m.locator("button[aria-expanded='false']:visible").count():
        m.locator("button[aria-expanded='false']:visible").first.click()


with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 1440, "height": 1000})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    login(page, "demo-fund@example.com")
    page.get_by_role("navigation", name="주 메뉴").get_by_role("link", name="이벤트 등록").click()
    sel = page.get_by_label("차입금 선택")
    sel.select_option(sel.locator("option", has_text="온새누리관").first.get_attribute("value"))
    page.get_by_role("radiogroup", name="이벤트 종류").get_by_text("상환유예").click()
    expect(page.get_by_label("유예기간 이자 납부")).to_have_count(0)           # 기본 원금+이자 → 선택칸 없음
    page.get_by_label("유예대상").select_option("PRINCIPAL")
    mode = page.get_by_label("유예기간 이자 납부")
    expect(mode).to_have_value("CYCLE")                                        # 기본값 = 회수 주기마다
    expect(page.get_by_text("유예원금 이자는 유예된 상환일부터 계산합니다.")).to_be_visible()
    mode.select_option("REGULAR")
    page.get_by_label("접수일(적용일)").fill("2026-11-01")
    box = page.locator("section[aria-label='적용 전 확인']")
    expect(box).to_contain_text("정규 상환일에만")
    if SHOTS:
        page.screenshot(path=f"{SHOTS}/d1_유예_선택.png", full_page=True)
    page.get_by_role("button", name="적용하기").click()
    page.get_by_role("dialog", name="상환유예 적용 확인").get_by_role("button", name="예, 적용합니다").click()
    done = page.get_by_role("dialog", name="적용 완료")
    expect(done).to_contain_text("원리금 테이블 V2")
    done.get_by_role("button", name="확인").click()

    assert not errors, errors
    b.close()
print("E2E OK: 원금 유예에서만 '유예기간 이자 납부' 선택(기본 회수 주기마다) → 정규 상환일에만 적용")
