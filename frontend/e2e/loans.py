"""
업무 화면 1차(차입금 관리·원리금 테이블) E2E — 데모 DB에 '검증용' 기숙사·차입금을 새로 만든다(이름에 실행 시각을 붙여 반복 실행 가능).
    python e2e/loans.py http://127.0.0.1:4173 [스크린샷 폴더]
"""
import re
import sys
import time

from playwright.sync_api import expect, sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:4173"
SHOTS = sys.argv[2] if len(sys.argv) > 2 else None
RUN = time.strftime("%H%M%S")


def shot(page, name):
    if SHOTS:
        page.screenshot(path=f"{SHOTS}/{name}.png", full_page=True)


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


with sync_playwright() as p:
    b = p.chromium.launch()
    errors = []
    page = b.new_page(viewport={"width": 1440, "height": 900})
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    login(page, "demo-fund@example.com")

    # 1) 메뉴 → 차입금 관리 목록
    page.get_by_role("navigation", name="주 메뉴").get_by_role("link", name="차입금 관리").click()
    expect(page.get_by_role("heading", name="차입금 관리").first).to_be_visible()
    expect(page.locator("section[aria-label='차입금 목록']")).to_contain_text("가람관")
    shot(page, "01_loans_list")

    # 2) 기숙사 등록(새 SPC 포함)
    page.get_by_role("button", name="기숙사 등록").click()
    dlg = page.get_by_role("dialog", name="기숙사 등록")
    dlg.get_by_label("기숙사명").fill(f"검증관{RUN}")
    dlg.get_by_label("학교명").fill("검증대학교")
    dlg.get_by_label(re.compile(r"^SPC")).select_option("__new")
    dlg.get_by_label(re.compile(r"^새 SPC명")).fill(f"검증관{RUN}(주)")
    dlg.get_by_role("button", name="등록").click()
    expect(page.get_by_role("status")).to_contain_text(f"검증관{RUN}")

    # 3) 차입금 등록 — 필수값 비우고 등록 → 안내, 채우면 미리보기·등록·원리금 테이블 생성
    page.get_by_role("button", name="차입금 등록").click()
    expect(page.get_by_role("heading", name="차입금 등록")).to_be_visible()
    page.get_by_role("button", name="등록", exact=True).click()
    expect(page.get_by_text("입력값을 확인하세요")).to_be_visible()
    page.get_by_label(re.compile(r"^기숙사")).select_option(label=f"검증관{RUN} (검증대학교)")
    page.get_by_label("재원").select_option(label="공공자금관리기금")
    page.get_by_label("배정일").fill("2015-08-21")
    page.get_by_label("배정금액").fill("1768000000")
    expect(page.get_by_label("배정금액")).to_have_value("1,768,000,000")                 # 천 단위 표시
    expect(page.get_by_text("17.7억원")).to_be_visible()
    code = page.get_by_label("차입금 코드")
    expect(code).to_have_value("2015-08-21_공자")                                         # 자동 제안
    code.fill(f"2015-08-21_공자-{RUN}")
    page.get_by_text("변동금리").click()
    page.get_by_label("최초 금리").fill("2.246")
    page.get_by_label("적용종료일").fill("2022-02-28")
    page.get_by_label("거치유형").select_option(label="건설거치")
    page.get_by_label("거치시작일").fill("2015-08-21")
    page.get_by_label("거치종료일").fill("2022-08-31")
    page.get_by_label("원금상환시작일").fill("2022-11-30")
    page.get_by_label("상환기간(개월)").fill("300")
    page.get_by_label("상환주기").select_option(label="분기")
    page.get_by_label("상환유형").select_option(label="원금균등")
    preview = page.locator("section[aria-label='상환 개요(미리보기)']")
    expect(preview).to_contain_text("100회")
    expect(preview).to_contain_text("17,680,000원")
    shot(page, "02_loan_form")
    page.get_by_role("button", name="등록", exact=True).click()

    # 4) 상세 — 원리금 테이블 탭으로 이동, 검증 정상, 행 수
    expect(page.get_by_role("status").first).to_contain_text("원리금 테이블 V1")
    expect(page.get_by_role("tab", name="원리금 테이블")).to_have_attribute("aria-selected", "true")
    ver = page.locator("section[aria-label='계산 검증']")
    expect(ver).to_contain_text("정상")
    table = page.locator("section[aria-label='원리금 테이블']")
    expect(table).to_contain_text("총 원금 1,768,000,000원")
    shot(page, "03_schedule")

    # 5) 금리이력 → 금리변동 적용(2022-02-28, 2.32%) → V2, 버전 비교
    page.get_by_role("tab", name=re.compile("금리이력")).click()
    page.get_by_role("button", name="금리 등록·변경").click()
    rd = page.get_by_role("dialog", name="금리 등록·변경")
    rd.get_by_label("적용시작일").fill("2022-02-28")
    rd.get_by_label("금리", exact=True).fill("2.32")
    rd.get_by_role("button", name="금리변동 적용").click()
    expect(page.get_by_role("status").first).to_contain_text("V2")
    page.get_by_role("tab", name="원리금 테이블").click()
    expect(page.get_by_label("원리금 테이블 버전")).to_contain_text("V2 금리변동 (현재)")
    page.get_by_role("tab", name="버전 비교").click()
    cmp = page.locator("section[aria-label='버전 비교']")
    expect(cmp).to_contain_text("바뀐 상환일")
    shot(page, "04_compare")
    page.get_by_role("tab", name="테이블", exact=True).click()
    with page.expect_download() as dl:
        page.get_by_role("button", name="Excel 다운로드").click()
    assert dl.value.suggested_filename.endswith("_v2.xlsx")

    # 6) 이벤트·변경이력
    page.get_by_role("tab", name="이벤트·변경이력").click()
    expect(page.get_by_text("금리변동").first).to_be_visible()
    expect(page.locator("section[aria-label='변경이력(누가·언제·무엇을)']")).to_contain_text("자금담당(데모)")
    shot(page, "05_history")

    # 7) 원리금 테이블 메뉴에서 차입금 고르기
    page.get_by_role("navigation", name="주 메뉴").get_by_role("link", name="원리금 테이블").click()
    sel = page.get_by_label("차입금 선택")
    sel.select_option(sel.locator("option", has_text="가람관 · 2017-08-21").first.get_attribute("value"))
    expect(page.locator("section[aria-label='계산 검증']")).to_contain_text("검토 필요")         # 데모의 금리 미반영 예시
    shot(page, "06_schedule_menu")

    # 8) 조회자: 등록 버튼 없음
    v = b.new_page(viewport={"width": 1280, "height": 900})
    login(v, "demo-viewer@example.com")
    v.goto(f"{BASE}/#/loans")
    expect(v.get_by_role("heading", name="차입금 관리").first).to_be_visible()
    expect(v.get_by_role("button", name="차입금 등록")).to_have_count(0)
    v.goto(f"{BASE}/#/loans/new")
    expect(v.get_by_text("차입금 등록 권한이 없습니다")).to_be_visible()

    # 9) 휴대폰 폭: 등록 화면·상세 가로 넘침 없음
    m = b.new_page(viewport={"width": 390, "height": 844})
    login(m, "demo-fund@example.com")
    for path in ("#/loans", "#/loans/new"):
        m.goto(f"{BASE}/{path}")
        m.wait_for_timeout(600)
        sw = m.evaluate("document.documentElement.scrollWidth")
        assert sw <= 390, f"{path} 가로 넘침 {sw}"
    b.close()
    assert not errors, errors
print("E2E OK: 차입금 목록·기숙사 등록·차입금 등록(검증·천단위·자동코드·미리보기)·원리금 테이블·금리변동 V2·버전비교·Excel·이력·원리금 메뉴·조회자 권한·휴대폰 폭")
