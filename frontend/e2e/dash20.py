"""
대시보드 개편(Phase 20) E2E — 공문을 발급하므로 '새로 만든 데모 DB'에서 돌린다(브라우저 시계 2026-10-28).
    python e2e/dash20.py http://127.0.0.1:8000 [스크린샷 폴더]
확인: 이름 변경, 분기별 차트(1~4분기), 공문 발급 → 발송예정 '발급완료·미수신' + 미수신 1건(노란 KPI)
→ SPC 담당자 '안내공문 수신'에서 새 공문 내려받기 → 수신 확인 → 재단 미수신 0, 발송예정 '수신',
더보기는 패널이 늘어나지 않고 팝업으로 전체 목록.
"""
import json
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
    page.get_by_role("navigation", name="주 메뉴").get_by_role("link", name=name).click()


UP = "section[aria-label='안내공문 발송예정(상환예정)']"
UNREC = "section[aria-label='안내공문 미수신']"

with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 1600, "height": 1000}, accept_downloads=True)
    login(page, "demo-admin@example.com")
    expect(page.get_by_role("navigation", name="주 메뉴").locator("..")).to_contain_text("행복기숙사 원리금 시스템")
    assert page.title() == "행복기숙사 원리금 시스템"
    chart = page.locator("section[aria-label^='분기별 원리금 상환 현황']")
    expect(chart).to_contain_text("(계획·실적, 2026년)")
    chart.get_by_role("button", name="표로 보기").click()
    expect(chart.locator("tbody tr")).to_have_count(5)                       # 1~4분기 + 합계
    q3 = chart.locator("tbody tr", has_text="3분기")
    expect(q3).to_contain_text("230,336,520원")                                # 3분기 미상환 = 데모 미납 2건
    expect(chart.locator("tbody tr", has_text="1분기")).to_contain_text("100%")
    expect(chart.locator("tbody tr", has_text="4분기").locator("td").last).to_have_text("-")   # 도래 회차 없음
    kpis = page.locator("section[aria-label='주요 지표']")
    expect(kpis).to_contain_text("안내공문 미수신0건")
    expect(page.locator(UP)).to_contain_text("미발급")

    # 미르기숙사 상환일(2026-10-31) 공문 발급
    page.evaluate("location.hash = \'#/notices\'")
    sel = page.get_by_label("기숙사", exact=True)
    sel.select_option(sel.locator("option", has_text="미르기숙사").first.get_attribute("value"))
    page.locator("section", has_text="1. 기숙사·상환예정일").get_by_role("button", name="2026-10-31").click()
    page.locator("section[aria-label='2. 공문 미리보기']").get_by_role("button", name="이 내용으로 발급").click()
    page.get_by_role("dialog", name="공문 발급 확인").get_by_role("button", name="발급", exact=True).click()
    issued = page.locator("section", has_text="발급 이력 — 미르기숙사")
    expect(issued.locator("li")).to_have_count(1)
    expect(issued).to_contain_text("미수신")

    nav(page, "대시보드")
    expect(kpis).to_contain_text("안내공문 미수신1건")
    expect(page.locator(UP)).to_contain_text("발급완료 · 미수신")
    expect(page.locator(UNREC)).to_contain_text("미르기숙사")
    shot(page, "p01_admin_issued")

    # SPC 담당자(미르기숙사 SPC가 아닌 가람관 SPC) → 이 공문이 보이지 않아야 함
    spc = b.new_page(viewport={"width": 1400, "height": 900}, accept_downloads=True)
    login(spc, "demo-spc@example.com")
    rec = spc.locator("section[aria-label='안내공문 수신']")
    expect(rec).to_be_visible()
    expect(rec).not_to_contain_text("미르기숙사")
    spc.close()

    # 미르기숙사 SPC 담당자를 만들어 확인(관리자 › 사용자·권한)
    nav(page, "사용자·권한설정")
    page.get_by_role("button", name="사용자 등록").click()
    d = page.get_by_role("dialog")
    d.get_by_label("이름").fill("미르담당")
    d.get_by_label("이메일(로그인 ID)").fill("mir-spc@example.com")
    d.get_by_label("역할").select_option("SPC_MANAGER")
    spc_sel = d.get_by_label("소속 SPC")
    spc_sel.select_option(spc_sel.locator("option", has_text="미르").first.get_attribute("value"))
    d.get_by_label("초기 비밀번호").fill("Mirspc2026!x")
    d.get_by_role("button", name="저장").click()
    expect(page.get_by_text("미르담당 계정을 만들었습니다")).to_be_visible()

    mir = b.new_page(viewport={"width": 1400, "height": 900}, accept_downloads=True)
    mir.goto(BASE)
    mir.fill("input[type=email]", "mir-spc@example.com")
    mir.fill("input[type=password]", "Mirspc2026!x")
    mir.click("button[type=submit]")
    mir.wait_for_selector("text=차입금 현황")
    try:
        mir.get_by_role("alertdialog").wait_for(timeout=2500)
        mir.get_by_role("button", name="나중에").click()
    except Exception:
        pass
    rec = mir.locator("section[aria-label='안내공문 수신']")
    expect(rec).to_contain_text("미르기숙사")
    expect(rec).to_contain_text("새 공문")
    with mir.expect_download():
        rec.get_by_role("button", name="내려받기").first.click()
    expect(rec).to_contain_text("수신 확인")
    expect(rec).not_to_contain_text("새 공문")
    shot(mir, "p02_spc_received")
    mir.close()

    nav(page, "대시보드")
    expect(kpis).to_contain_text("안내공문 미수신0건")
    expect(page.locator(UP)).to_contain_text("발급완료 · 수신")

    # 더보기 → 팝업(패널 높이 그대로). 미납 목록을 12건으로 늘린 응답으로 확인
    def fake(route):
        r = route.fetch()
        data = r.json()
        items = data["alerts"]["overdue"]["items"]
        if items:
            data["alerts"]["overdue"]["items"] = [dict(items[0], due_date=f"2025-{m:02d}-28") for m in range(1, 13)]
            data["alerts"]["overdue"]["count"] = 12
        route.fulfill(response=r, body=json.dumps(data), headers={**r.headers, "content-type": "application/json"})
    page.route(re.compile(r".*/api/v1/dashboard\?.*"), fake)
    page.reload()
    page.wait_for_selector("text=차입금 현황")
    over = page.locator("section[aria-label='미납']")
    expect(over.locator("li")).to_have_count(5)
    h0 = over.bounding_box()["height"]
    over.get_by_role("button", name="외 7건 더보기").click()
    dlg = page.get_by_role("dialog", name="미납 — 전체 12건")
    expect(dlg.locator("li")).to_have_count(12)
    assert abs(over.bounding_box()["height"] - h0) < 1, "패널 높이가 바뀜"
    shot(page, "p03_more_popup")
    dlg.get_by_role("button", name="닫기").click()
    expect(dlg).to_be_hidden()
    b.close()

print("E2E OK: 시스템명, 분기별 원리금 상환 현황(계획·실적, 미상환·달성률), 공문 발급 → 발송예정 발급완료·미수신 + 미수신 KPI, 다른 SPC에는 안 보임, "
      "해당 SPC 담당자 내려받기 → 수신 확인 → 재단 미수신 0·발송예정 '수신', 더보기 팝업(패널 높이 유지)")
