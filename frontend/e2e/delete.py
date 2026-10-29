"""
관리자 삭제(숨김)·복구 E2E — 데이터를 바꾸므로(끝에 복구함) '새로 만든 데모 DB'에서 돌린다.
    python e2e/delete.py http://127.0.0.1:4173 [스크린샷 폴더]
확인: 관리자만 버튼·메뉴가 보임, 원리금 테이블 삭제 → 재생성(번호 이어짐), 차입금 삭제 → 목록에서 빠짐 → 복구,
기숙사 삭제 → 대시보드 기숙사 수 감소 → 복구, 안내공문 삭제 → 발급 이력에서 빠짐, 사유 없으면 삭제 버튼 비활성.
"""
import re
import sys

from playwright.sync_api import expect, sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:4173"
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
    page.fill("header input[type=date]", "2026-10-28")


def nav(page, name):
    page.get_by_role("navigation", name="주 메뉴").get_by_role("link", name=name).click()


def confirm_delete(page, title, reason):
    dlg = page.get_by_role("dialog", name=title)
    expect(dlg.get_by_role("button", name="삭제", exact=True)).to_be_disabled()      # 사유 없으면 못 지움
    dlg.get_by_label("삭제 사유").fill(reason)
    dlg.get_by_role("button", name="삭제", exact=True).click()
    expect(dlg).to_be_hidden()


def open_loan(page, dorm):
    nav(page, "차입금 관리")
    page.locator("table tbody tr", has_text=dorm).first.get_by_role("link").first.click()
    expect(page).to_have_url(re.compile(r"#/loans/[0-9a-f-]{36}"))
    expect(page.get_by_role("heading", name=re.compile(dorm)).first).to_be_visible()


def kpi_dorms(page):
    nav(page, "대시보드")
    return page.locator("section[aria-label='주요 지표']")


with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 1400, "height": 900})

    # 0) 자금담당: 삭제 버튼·메뉴가 없다
    login(page, "demo-fund@example.com")
    expect(page.locator("section[aria-label='주요 지표']")).to_contain_text("8곳")
    expect(page.get_by_role("navigation", name="주 메뉴").get_by_role("link", name="삭제·복구")).to_have_count(0)
    open_loan(page, "미르기숙사")
    expect(page.get_by_role("button", name="차입금 삭제")).to_have_count(0)
    page.get_by_role("tab", name="원리금 테이블").click()
    expect(page.get_by_role("button", name="원리금 테이블 삭제")).to_have_count(0)

    # 1) 관리자: 원리금 테이블 삭제 → 다시 생성(번호 이어짐)
    page.close()
    page = b.new_page(viewport={"width": 1400, "height": 900})
    login(page, "demo-admin@example.com")
    open_loan(page, "미르기숙사")
    page.get_by_role("tab", name="원리금 테이블").click()
    page.get_by_role("button", name="원리금 테이블 삭제").click()
    confirm_delete(page, "원리금 테이블 삭제", "E2E 조건 수정 후 재생성")
    expect(page.get_by_text("아직 원리금 테이블이 없습니다")).to_be_visible()
    expect(page.get_by_text("원리금 테이블을 삭제했습니다")).to_be_visible()
    page.get_by_role("button", name="원리금 테이블 생성").click()
    expect(page.get_by_label("원리금 테이블 버전")).to_contain_text("V2 최초생성")
    shot(page, "d01_regenerated")

    # 2) 차입금 삭제 → 목록에서 빠짐
    page.get_by_role("button", name="차입금 삭제").click()
    confirm_delete(page, "차입금 삭제", "E2E 차입금 삭제")
    expect(page.get_by_text(re.compile("차입금 '.+'을\\(를\\) 삭제했습니다"))).to_be_visible()
    expect(page.locator("table tbody")).not_to_contain_text("미르기숙사")

    # 3) 기숙사 삭제(삭제·복구 화면) → 대시보드 기숙사 수: (차입금 삭제로 미르기숙사 빠져) 7 → 6
    expect(kpi_dorms(page)).to_contain_text("7곳")
    nav(page, "삭제·복구")
    page.locator("table[aria-label='기숙사 목록'] tr", has_text="바름학사").get_by_role("button", name="삭제").click()
    confirm_delete(page, "기숙사 삭제", "E2E 가상 기숙사 정리")
    expect(page.get_by_text("'바름학사' 기숙사를 삭제했습니다")).to_be_visible()
    hist = page.locator("ul[aria-label='삭제 내역']")
    expect(hist).to_contain_text("바름학사")
    expect(hist).to_contain_text("E2E 차입금 삭제")
    shot(page, "d02_deletions")
    expect(kpi_dorms(page)).to_contain_text("6곳")

    # 4) 안내공문 발급 → 삭제
    nav(page, "안내공문")
    sel = page.get_by_label("기숙사", exact=True)
    sel.select_option(sel.locator("option", has_text="가람관").first.get_attribute("value"))
    page.locator("section", has_text="1. 기숙사·상환예정일").get_by_role("button", name=re.compile(r"^\d{4}-\d{2}-\d{2}$")).first.click()
    page.locator("section[aria-label='2. 공문 미리보기']").get_by_role("button", name="이 내용으로 발급").click()
    page.get_by_role("dialog", name="공문 발급 확인").get_by_role("button", name="발급", exact=True).click()
    issued = page.locator("section", has_text="발급 이력 — 가람관")
    expect(issued.locator("li")).to_have_count(1)
    issued.get_by_role("button", name="삭제").first.click()
    confirm_delete(page, "안내공문 삭제", "E2E 잘못 발급")
    expect(page.get_by_text("공문을 삭제했습니다")).to_be_visible()
    expect(issued.get_by_text("발급한 공문이 없습니다")).to_be_visible()

    # 4b) 원리금 테이블 버전 건별 삭제(라온하우스 V2 = 이벤트 결과) → V1이 다시 현재, 이벤트도 함께 숨김
    open_loan(page, "라온하우스")
    page.get_by_role("tab", name="원리금 테이블").click()
    expect(page.get_by_label("원리금 테이블 버전")).to_contain_text("V2")
    page.get_by_role("button", name="이 버전(V2) 삭제").click()
    confirm_delete(page, "원리금 테이블 버전 삭제", "E2E 버전 삭제")
    expect(page.get_by_label("원리금 테이블 버전")).to_contain_text("V1 최초생성 (현재)")
    expect(page.get_by_label("원리금 테이블 버전").locator("option")).to_have_count(1)
    page.get_by_role("tab", name="이벤트·변경이력").click()
    expect(page.get_by_text("적용된 이벤트가 없습니다")).to_be_visible()

    # 4c) 이벤트 건별 삭제(이벤트 관리 › 이벤트 이력, 새봄관)
    nav(page, "이벤트 관리")
    page.get_by_role("tab", name="이벤트 이력").click()
    row = page.locator("table tbody tr", has_text="새봄관").first
    row.get_by_role("button", name=re.compile("삭제$")).click()
    confirm_delete(page, "이벤트 삭제", "E2E 이벤트 삭제")
    expect(page.get_by_text("이벤트를 삭제했습니다")).to_be_visible()
    expect(page.locator("table tbody")).not_to_contain_text("새봄관")
    expect(page.locator("table tbody")).not_to_contain_text("라온하우스")
    shot(page, "d03_events")

    # 5) 복구: 기숙사·차입금·공문·버전(이벤트)·이벤트 → 원래대로
    nav(page, "삭제·복구")
    for label in ("바름학사", "미르기숙사", "가람관", "2015-05-29_공자-30", "2018-11-30_공자-60"):
        hist.locator("li", has_text=label).get_by_role("button", name="복구").click()
        expect(page.get_by_text("복구했습니다")).to_be_visible()
    # 남은 것은 원리금 테이블 V1 — V2를 새로 만들었으므로 복구하면 이유를 안내하고 막는다
    expect(hist.locator("li")).to_have_count(1)
    expect(hist).to_contain_text("원리금 테이블 V1")
    hist.get_by_role("button", name="복구").click()
    expect(page.get_by_text("지금 쓰는 원리금 테이블이 있어 복구할 수 없습니다")).to_be_visible()
    page.get_by_label("복구한 것도 보기").check()
    expect(page.locator("ul[aria-label='삭제 내역']")).to_contain_text("복구됨")
    expect(kpi_dorms(page)).to_contain_text("8곳")
    nav(page, "차입금 관리")
    expect(page.locator("table tbody")).to_contain_text("미르기숙사")
    open_loan(page, "라온하우스")
    page.get_by_role("tab", name="원리금 테이블").click()
    expect(page.get_by_label("원리금 테이블 버전")).to_contain_text("V2")
    nav(page, "이벤트 관리")
    page.get_by_role("tab", name="이벤트 이력").click()
    expect(page.locator("table tbody")).to_contain_text("새봄관")

    # 6) 휴대폰 폭: 삭제·복구 화면 가로 넘침 없음
    page.set_viewport_size({"width": 390, "height": 800})
    page.goto(BASE + "/#/admin/deletions")
    page.wait_for_timeout(800)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "가로 넘침"
    b.close()

print("E2E OK: 관리자만 삭제 버튼·메뉴, 원리금 테이블 삭제·재생성(V2)·옛 테이블 복구 차단 안내, 차입금 삭제·복구, 차입금·기숙사 삭제가 대시보드에 반영(KPI 8→7→6)·복구 후 8, 안내공문 삭제·복구, 원리금 테이블 버전 건별 삭제(V2→V1)·이벤트 건별 삭제·복구, 사유 필수, 휴대폰 폭")
