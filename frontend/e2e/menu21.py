"""
메뉴 재구성(Phase 21) E2E — 공문 발급·차입금 삭제를 하므로 '새로 만든 데모 DB'에서 돌린다(브라우저 시계 2026-10-28).
    python e2e/menu21.py http://127.0.0.1:8000 [스크린샷 폴더]
확인: 새 메뉴 구성, 발송대상 현황 → 행 클릭 → 공문 작성(자동 미리보기) → 기숙사 입금계좌 등록 → 발급 →
발송대상에서 빠지고 발송완료(미수신) → 수신 상태 필터, 자금담당자 차입금 삭제, 이벤트 등록·상환 실적 관리 메뉴 이동.
"""
import re
import sys

from playwright.sync_api import expect, sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
SHOTS = sys.argv[2] if len(sys.argv) > 2 else None


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
    # 메뉴 묶음 펼치기(Phase 22: 토글 메뉴 — 펼친 상태는 브라우저에 기억됨)
    _m = page.get_by_role("navigation", name="주 메뉴")
    while _m.locator("button[aria-expanded='false']:visible").count():
        _m.locator("button[aria-expanded='false']:visible").first.click()


def nav(page, name):
    page.get_by_role("navigation", name="주 메뉴").get_by_role("link", name=name, exact=True).click()


with sync_playwright() as p:
    b = p.chromium.launch()
    errors = []
    page = b.new_page(viewport={"width": 1500, "height": 950})
    page.on("pageerror", lambda e: errors.append(str(e)))
    login(page, "demo-admin@example.com")
    menu = page.get_by_role("navigation", name="주 메뉴")
    # Phase 22: 토글 메뉴 — 상위를 누르면 하위가 접히고 펼쳐짐, 글자 크기 1>2>3수준, 1·2수준 굵게, 나눔스퀘어
    grp = menu.get_by_role("button", name="차입금 마스터")
    expect(grp).to_have_attribute("aria-expanded", "true")
    grp.click()
    expect(grp).to_have_attribute("aria-expanded", "false")
    expect(menu.get_by_role("link", name="신규등록", exact=True)).to_be_hidden()
    grp.click()
    expect(menu.get_by_role("link", name="신규등록", exact=True)).to_be_visible()
    st = lambda loc: loc.evaluate("e => [parseFloat(getComputedStyle(e).fontSize), Number(getComputedStyle(e).fontWeight)]")  # noqa: E731
    l1, l2, l3 = st(grp), st(menu.get_by_role("button", name="차입금 등록")), st(menu.get_by_role("link", name="신규등록", exact=True))
    assert l1[0] > l2[0] > l3[0] and l1[1] >= 700 and l2[1] >= 700 and l3[1] < 700, (l1, l2, l3)
    assert page.evaluate("document.fonts.check('16px NanumSquare')") and "NanumSquare" in page.evaluate("getComputedStyle(document.body).fontFamily")
    for t in ("차입금 마스터", "차입금 등록", "원리금 상환", "안내공문", "마이페이지", "관리자"):
        expect(menu).to_contain_text(t)
    for link in ("신규등록", "기존 차입금 이관", "이벤트 등록", "차입금 관리", "발송대상 현황", "발송완료", "상환 실적 관리",
                 "알림", "기준정보", "사용자·권한설정", "감사로그", "삭제·복구"):
        expect(menu.get_by_role("link", name=link, exact=True)).to_have_count(1)
    expect(menu.get_by_role("link", name="원리금 테이블", exact=True)).to_have_count(0)

    # 발송대상 현황 → 미르기숙사(상환일 2026-10-31) → 공문 작성 화면(미리보기 자동)
    nav(page, "발송대상 현황")
    tbl = page.get_by_role("table", name="발송대상 목록")
    row = tbl.locator("tbody tr", has_text="미르기숙사")
    expect(row).to_contain_text("2026-10-31")
    expect(row).to_contain_text("미발급")
    shot(page, "m21_01_targets")
    row.get_by_role("button", name="공문 작성").click()
    expect(page).to_have_url(re.compile(r"#/notices\?dormitory=.+&due=2026-10-31"))
    pv = page.locator("section[aria-label='2. 공문 미리보기']")
    expect(pv).to_contain_text("[공통 계좌]")

    # 기숙사 입금계좌 등록 → 미리보기에 반영
    acc = page.locator("section", has_text="입금계좌 — 미르기숙사").last
    acc.get_by_role("button", name="기숙사 계좌 등록").click()
    acc.get_by_label("계좌번호").fill("미르은행 777-888-999000")
    acc.get_by_label("예금주").fill("미르기숙사(주)")
    acc.get_by_role("button", name="저장").click()
    expect(acc.get_by_label("입금계좌 상태")).to_contain_text("미르은행 777-888-999000")
    expect(pv).to_contain_text("미르은행 777-888-999000")
    expect(pv).to_contain_text("[기숙사 계좌]")
    shot(page, "m21_02_issue")
    pv.get_by_role("button", name="이 내용으로 발급").click()
    page.get_by_role("dialog", name="공문 발급 확인").get_by_role("button", name="발급", exact=True).click()
    expect(page.get_by_text("공문을 발급했습니다")).to_be_visible()

    # 발송대상에서 빠지고, '발급한 건도 보기'면 발급완료·미수신
    nav(page, "발송대상 현황")
    expect(tbl.locator("tbody tr", has_text="미르기숙사")).to_have_count(0)
    page.get_by_label("발급한 건도 보기").check()
    page.get_by_role("button", name="조회").click()
    expect(tbl.locator("tbody tr", has_text="미르기숙사")).to_contain_text("발급완료·미수신")

    # 발송완료: 미수신 1건, 수신 상태 필터
    nav(page, "발송완료")
    done = page.get_by_role("table", name="발송완료 목록")
    expect(done.locator("tbody tr", has_text="미르기숙사")).to_contain_text("미수신")
    page.get_by_label("수신 상태").select_option("true")
    page.get_by_role("button", name="조회").click()
    expect(page.get_by_text("조건에 맞는 발급 공문이 없습니다")).to_be_visible()
    shot(page, "m21_03_issued")

    # 메뉴 이동 확인
    nav(page, "이벤트 등록")
    expect(page.get_by_role("main").get_by_role("heading", name="이벤트 등록")).to_be_visible()
    nav(page, "상환 실적 관리")
    expect(page.get_by_role("main").get_by_role("heading", name="상환 실적 관리")).to_be_visible()
    nav(page, "기존 차입금 이관")
    expect(page.get_by_role("main").get_by_role("heading", name="기존 차입금 이관")).to_be_visible()

    # 자금담당자: 차입금 관리 목록에서 삭제(사유 필수) → 목록에서 빠짐, 삭제·복구 메뉴는 없음
    fm = b.new_page(viewport={"width": 1500, "height": 950})
    fm.on("pageerror", lambda e: errors.append(str(e)))
    login(fm, "demo-fund@example.com")
    expect(fm.get_by_role("navigation", name="주 메뉴").get_by_role("link", name="삭제·복구")).to_have_count(0)
    nav(fm, "차입금 관리")
    r = fm.locator("section[aria-label='차입금 목록'] tbody tr", has_text="2019-12-30_주택-50")
    r.get_by_role("button", name="삭제").click()
    dlg = fm.get_by_role("dialog", name="차입금 삭제")
    expect(dlg.get_by_role("button", name="삭제", exact=True)).to_be_disabled()
    dlg.get_by_label("삭제 사유").fill("E2E 자금담당 삭제")
    dlg.get_by_role("button", name="삭제", exact=True).click()
    expect(fm.get_by_text("차입금 '2019-12-30_주택-50'을(를) 삭제했습니다")).to_be_visible()
    expect(fm.locator("section[aria-label='차입금 목록'] tbody")).not_to_contain_text("2019-12-30_주택-50")

    # 관리자 복구
    nav(page, "삭제·복구")
    page.locator("ul[aria-label='삭제 내역'] li", has_text="2019-12-30_주택-50").get_by_role("button", name="복구").click()
    expect(page.get_by_text("복구했습니다")).to_be_visible()

    # 휴대폰 폭
    page.set_viewport_size({"width": 390, "height": 800})
    for path in ("#/notices/targets", "#/notices/issued", "#/loans"):
        page.goto(f"{BASE}/{path}")
        page.wait_for_timeout(700)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), f"{path} 가로 넘침"
    b.close()
    assert not errors, errors

print("E2E OK: 새 메뉴 구성·토글·글자 위계·나눔스퀘어, 발송대상 → 공문 작성(자동 미리보기) → 기숙사 입금계좌 반영 → 발급 → 발송대상에서 빠짐·발송완료(미수신)·수신 필터, "
      "이벤트 등록·상환 실적 관리·기존 차입금 이관 이동, 자금담당자 차입금 삭제 → 관리자 복구, 휴대폰 폭")
