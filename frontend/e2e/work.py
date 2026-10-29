"""
업무 화면 2차(이벤트·실제 상환·공문·알림·관리자) E2E — 데이터를 바꾸므로 '새로 만든 데모 DB'에서 돌린다.
    (백엔드) python -m app.jobs.seed_demo --as-of 2026-10-28  → 빈 데모 DB
    python e2e/work.py http://127.0.0.1:4173 [스크린샷 폴더]
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
    page.fill("header input[type=date]", "2026-10-28")


def pick(sel, text):
    sel.select_option(sel.locator("option", has_text=text).first.get_attribute("value"))


def nav(page, name):
    page.get_by_role("navigation", name="주 메뉴").get_by_role("link", name=name).click()


with sync_playwright() as p:
    b = p.chromium.launch()
    errors = []
    page = b.new_page(viewport={"width": 1440, "height": 900})
    page.on("pageerror", lambda e: errors.append(str(e)))
    login(page, "demo-fund@example.com")

    # 1) 이벤트: 원리금균등 차입금은 금리변동 불가 안내 → 일부 조기상환 적용(V2) → 이력에서 취소(V3)
    nav(page, "이벤트 관리")
    pick(page.get_by_label("차입금 선택"), "온새누리관")
    page.get_by_role("radiogroup", name="이벤트 종류").get_by_text("금리변동").click()
    expect(page.get_by_text("원금균등(단리) 차입금만 지원")).to_be_visible()
    page.get_by_role("radiogroup", name="이벤트 종류").get_by_text("조기상환").click()
    page.get_by_label("접수일(적용일)").fill("2025-01-15")
    page.get_by_label("조기상환 금액").fill("100000000")
    expect(page.locator("section[aria-label='적용 전 확인']")).to_contain_text("100,000,000원")
    shot(page, "w01_event_new")
    page.get_by_role("button", name="적용하기").click()
    page.get_by_role("dialog").get_by_role("button", name="적용", exact=True).click()
    expect(page.get_by_text(re.compile(r"원리금 테이블 V2을\(를\) 만들었습니다"))).to_be_visible()
    page.get_by_role("tab", name="이벤트 이력").click()
    row = page.locator("tr", has_text="온새누리관").first
    expect(row).to_contain_text("조기상환")
    row.get_by_role("button", name="취소").click()
    page.get_by_role("dialog").get_by_label("취소 사유").fill("E2E 취소 확인")
    page.get_by_role("dialog").get_by_role("button", name="취소 처리").click()
    expect(page.get_by_text("V3")).to_be_visible()
    shot(page, "w02_event_list")

    # 2) 실제 상환: 미납 현황(차입금별) → 바름학사 납부 입력 → 완납 → 취소
    nav(page, "실제 상환관리")
    card = page.locator("section", has_text="납부기한이 지난 미납")
    expect(card).to_contain_text("바름학사")
    expect(card).to_contain_text("누리학사")
    card.locator("tr", has_text="바름학사").get_by_role("link", name="납부 입력 →").click()
    unpaid = page.locator("tr", has_text="미납").filter(has=page.get_by_role("button", name="납부 입력")).first
    unpaid.get_by_role("button", name="납부 입력").click()
    dlg = page.get_by_role("dialog")
    expect(dlg.get_by_role("status")).to_contain_text("완납")                          # 예정액으로 미리 채워짐
    dlg.get_by_label("비고").fill("E2E 입금")
    dlg.get_by_role("button", name="저장").click()
    expect(page.get_by_text(re.compile("납부 .*원을\\(를\\) 입력했습니다"))).to_be_visible()
    shot(page, "w03_payment")
    pays = page.locator("section[aria-label='납부 내역(취소 포함)']")
    pays.locator("tr", has_text="E2E 입금").get_by_role("button", name="취소").click()
    page.get_by_role("dialog").get_by_label("취소 사유").fill("E2E 취소")
    page.get_by_role("dialog").get_by_role("button", name="취소 처리").click()
    expect(pays.locator("tr", has_text="E2E 취소")).to_contain_text("취소")

    # 3) 안내공문: 가람관 → 다음 상환일 버튼 → 미리보기 → 발급 → 이력·Excel
    nav(page, "안내공문")
    pick(page.get_by_label("기숙사", exact=True), "가람관")
    page.locator("section", has_text="1. 기숙사·상환예정일").get_by_role("button", name=re.compile(r"^\d{4}-\d{2}-\d{2}$")).first.click()
    prev = page.locator("section[aria-label='2. 공문 미리보기']")
    expect(prev).to_contain_text("원리금 계산내역")
    expect(prev).to_contain_text("합 계")
    shot(page, "w04_notice")
    # 발급 확인 팝업: 취소하면 발급되지 않고, '발급'을 눌러야 발급된다
    prev.get_by_role("button", name="이 내용으로 발급").click()
    dlg = page.get_by_role("dialog", name="공문 발급 확인")
    expect(dlg).to_contain_text("발급 진행하시겠습니까?")
    dlg.get_by_role("button", name="취소").click()
    expect(dlg).to_be_hidden()
    expect(page.get_by_text("공문을 발급했습니다")).to_have_count(0)
    prev.get_by_role("button", name="이 내용으로 발급").click()
    dlg.get_by_role("button", name="발급", exact=True).click()
    expect(page.get_by_text("공문을 발급했습니다")).to_be_visible()
    issued = page.locator("section", has_text="발급 이력 — 가람관")
    # 수신처: 이메일이 없으면 메일 작성 불가 → 연락처 추가 → 받는 사람이 채워진 메일 작성 링크
    expect(issued.get_by_text("수신처 이메일 없음").first).to_be_visible()
    contacts = page.locator("section", has_text="수신처·연락처 — 가람관")
    contacts.get_by_role("button", name="+ 연락처 추가").click()
    form = page.get_by_role("dialog", name="연락처 추가")
    form.get_by_label("이름").fill("E2E 담당")
    form.get_by_label("소속").fill("가람대학교")
    form.get_by_label("이메일").fill("e2e-garam@example.com")
    form.get_by_role("button", name="저장").click()
    expect(contacts.locator("table")).to_contain_text("e2e-garam@example.com")
    expect(contacts.locator("table")).to_contain_text("주 수신처")
    mail = issued.get_by_role("link", name="메일 작성").first
    assert mail.get_attribute("href").startswith("mailto:e2e-garam@example.com?subject=")
    with page.expect_download() as dl:
        issued.get_by_role("button", name="Excel").first.click()
    assert dl.value.suggested_filename.endswith(".xlsx")

    # 4) 알림: 지금 점검 → 목록 → 모두 읽음
    nav(page, "알림")
    page.get_by_role("button", name="지금 알림 점검").click()
    expect(page.get_by_text("알림을 점검했습니다")).to_be_visible()
    expect(page.locator("section", has_text="내 알림")).to_contain_text("D-")
    shot(page, "w05_notifications")
    page.get_by_role("button", name="모두 읽음").click()
    expect(page.get_by_role("button", name="모두 읽음")).to_be_disabled()

    # 5) 관리자: 공휴일 직접 추가, 사용자 등록(약한 비밀번호 거부 → 등록) → 감사로그에 남음
    a = b.new_page(viewport={"width": 1440, "height": 900})
    a.on("pageerror", lambda e: errors.append(str(e)))
    login(a, "demo-admin@example.com")
    nav(a, "기준정보")
    a.get_by_role("tab", name="공휴일").click()
    expect(a.get_by_text("서비스키 미등록")).to_be_visible()
    a.get_by_label("날짜").fill("2026-12-30")
    a.get_by_label("이름").fill(f"E2E 휴일 {RUN}")
    a.get_by_role("button", name="직접 추가").click()
    expect(a.get_by_text(f"E2E 휴일 {RUN}")).to_be_visible()
    nav(a, "사용자·권한")
    a.get_by_role("button", name="사용자 등록").click()
    d = a.get_by_role("dialog")
    d.get_by_label("이름").fill(f"검증사용자{RUN}")
    d.get_by_label("이메일(로그인 ID)").fill(f"e2e{RUN}@example.com")
    d.get_by_label("역할").select_option("VIEWER")
    d.get_by_label("초기 비밀번호").fill("abc12345")
    expect(d.get_by_text("8자 이상")).to_be_visible()
    expect(d.get_by_role("button", name="저장")).to_be_disabled()
    d.get_by_label("초기 비밀번호").fill("Strong!Pass26")
    d.get_by_role("button", name="저장").click()
    expect(a.get_by_text(f"검증사용자{RUN} 계정을 만들었습니다")).to_be_visible()
    shot(a, "w06_users")
    nav(a, "감사로그")
    expect(a.locator("section[aria-label='변경이력']")).to_contain_text("사용자·권한")
    a.get_by_role("tab", name="접근기록").click()
    expect(a.locator("section[aria-label='접근기록']")).to_contain_text("로그인")
    a.get_by_role("tab", name="백업·보안 점검").click()
    expect(a.get_by_text("보안 설정 점검")).to_be_visible()
    shot(a, "w07_ops")

    # 6) 역할별 메뉴: 결재권자 — 이벤트 관리(결재함)만, 등록 없음 / SPC 담당자 — 관리자·이벤트 메뉴 없음
    ap = b.new_page(viewport={"width": 1280, "height": 900})
    login(ap, "demo-approver@example.com")
    nav(ap, "이벤트 관리")
    expect(ap.get_by_role("tab", name="결재함")).to_be_visible()
    expect(ap.get_by_role("tab", name="이벤트 등록")).to_have_count(0)
    sp = b.new_page(viewport={"width": 1280, "height": 900})
    login(sp, "demo-spc@example.com")
    menu = sp.get_by_role("navigation", name="주 메뉴")
    expect(menu.get_by_role("link", name="이벤트 관리")).to_have_count(0)
    expect(menu.get_by_role("link", name="사용자·권한")).to_have_count(0)

    # 7) 휴대폰 폭
    m = b.new_page(viewport={"width": 390, "height": 844})
    login(m, "demo-admin@example.com")
    for path in ("events", "events?tab=list", "payments", "notices", "notifications", "admin/users", "admin/audit", "admin/reference"):
        m.goto(f"{BASE}/#/{path}")
        m.wait_for_timeout(700)
        sw = m.evaluate("document.documentElement.scrollWidth")
        assert sw <= 390, f"{path} 가로 넘침 {sw}"
    b.close()
    assert not errors, errors
print("E2E OK: 이벤트 등록·취소, 미납 현황·납부 입력·취소, 공문 미리보기·발급·Excel, 알림 점검·읽음, 공휴일·사용자 등록·감사로그, 역할별 메뉴, 휴대폰 폭")
