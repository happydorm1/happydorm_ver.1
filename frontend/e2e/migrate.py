"""
기존 차입금 일괄 이관 E2E — 데이터를 만들고(끝에 이관 취소) 지우므로 '새로 만든 데모 DB'에서 돌린다.
    python e2e/migrate.py http://127.0.0.1:8000 [스크린샷 폴더]
확인: 관리자만 메뉴, 양식 내려받기, 오류 파일 → 시트·행별 오류(실행 버튼 비활성), 고친 파일 → 검사 통과 → 이관 실행(확인 창)
→ 차입금 관리 목록에 나타남·지난 회차 납부완료(예외 회차는 미납), 대조표 내려받기, 이관 취소 → 목록에서 빠짐, 휴대폰 폭.
"""
import re
import sys
import tempfile
from pathlib import Path

from openpyxl import load_workbook
from playwright.sync_api import expect, sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
SHOTS = sys.argv[2] if len(sys.argv) > 2 else None
TMP = Path(tempfile.mkdtemp())


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


def nav(page, name):
    page.get_by_role("navigation", name="주 메뉴").get_by_role("link", name=name).click()


def fill(template: Path, out: Path, *, broken: bool):
    wb = load_workbook(template)
    wb["기숙사"].append(["이관테스트관", "이관대학교", "이관테스트(주)", "행복기숙사(사립)", None])
    loan = ["이관테스트관", None, "MIG-E2E-01", "공공자금관리기금", 2_000_000_000, "2019-03-29", "변동", "분기", "원금균등",
            None, None, None, "2019-06-28", 120, "반올림", 2.21, None]
    if broken:
        loan[3] = "없는재원"
        loan[5] = "2019/13/40"
    wb["차입금"].append(loan)
    wb["금리이력"].append(["MIG-E2E-01", "2022-01-01", 2.98])
    wb["납부예외"].append(["MIG-E2E-01", "2024-03-31", 0, 0, None])
    wb.save(out)


with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 1400, "height": 900}, accept_downloads=True)

    login(page, "demo-fund@example.com")
    expect(page.get_by_role("navigation", name="주 메뉴").get_by_role("link", name="기존 차입금 이관")).to_have_count(0)
    page.close()

    page = b.new_page(viewport={"width": 1400, "height": 900}, accept_downloads=True)
    login(page, "demo-admin@example.com")
    nav(page, "기존 차입금 이관")
    expect(page.get_by_role("main").get_by_role("heading", name="기존 차입금 이관")).to_be_visible()

    with page.expect_download() as d:
        page.get_by_role("button", name="이관 양식 내려받기 (.xlsx)").click()
    tpl = TMP / d.value.suggested_filename
    d.value.save_as(tpl)
    assert tpl.name.endswith(".xlsx") and "이관" in tpl.name, tpl.name

    bad, good = TMP / "bad.xlsx", TMP / "good.xlsx"
    fill(tpl, bad, broken=True)
    fill(tpl, good, broken=False)

    page.get_by_label("이관 기준일").fill("2024-06-30")
    page.get_by_label("이관 파일").set_input_files(str(bad))
    page.get_by_role("button", name="검사", exact=True).click()
    errs = page.get_by_role("table", name="검사 오류")
    expect(errs).to_contain_text("없는 재원")
    expect(errs).to_contain_text("없는 날짜")
    expect(errs.locator("tbody tr").first).to_contain_text("2")
    expect(errs.locator("tbody tr").first).to_contain_text("차입금")
    expect(page.get_by_role("button", name="이관 실행")).to_be_disabled()
    shot(page, "m01_errors")

    page.get_by_label("이관 파일").set_input_files(str(good))
    page.get_by_role("button", name="검사", exact=True).click()
    expect(page.get_by_text("검사 통과")).to_be_visible()
    expect(page.get_by_label("검사 결과")).to_contain_text("미납으로 둔 회차 1")
    shot(page, "m02_checked")
    page.get_by_role("button", name="이관 실행").click()
    dlg = page.get_by_role("dialog", name="이관 실행 확인")
    expect(dlg).to_contain_text("2024-06-30")
    dlg.get_by_role("button", name="이관 실행").click()
    expect(page.get_by_text("이관을 마쳤습니다")).to_be_visible()
    hist = page.get_by_role("list", name="이관 이력")
    expect(hist.locator("li")).to_have_count(1)
    expect(hist).to_contain_text("good.xlsx")

    with page.expect_download() as d:
        hist.get_by_role("button", name="대조표 내려받기").click()
    rep = TMP / "report.xlsx"
    d.value.save_as(rep)
    ws = load_workbook(rep)["이관 대조표"]
    assert ws.cell(row=2, column=1).value == "MIG-E2E-01" and ws.cell(row=2, column=14).value == 1, [c.value for c in ws[2]]
    shot(page, "m03_done")

    # 차입금 관리에 나타나고, 지난 회차는 납부완료·예외 회차는 미납
    nav(page, "차입금 관리")
    row = page.locator("table tbody tr", has_text="이관테스트관").first
    expect(row).to_be_visible()
    row.get_by_role("link").first.click()
    expect(page).to_have_url(re.compile(r"#/loans/[0-9a-f-]{36}"))
    page.get_by_role("tab", name="원리금 테이블").click()
    expect(page.get_by_label("원리금 테이블 버전")).to_contain_text("V1 최초생성")

    # 이관 취소
    nav(page, "기존 차입금 이관")
    hist = page.get_by_role("list", name="이관 이력")
    hist.get_by_role("button", name="이관 취소").click()
    c = page.get_by_role("dialog", name="이관 취소")
    expect(c.get_by_role("button", name="이관 취소")).to_be_disabled()
    c.get_by_label("취소 사유").fill("E2E 되돌림")
    c.get_by_role("button", name="이관 취소").click()
    expect(page.get_by_text(re.compile("이관을 취소했습니다 — 차입금 1건, 기숙사 1곳"))).to_be_visible()
    expect(hist).to_contain_text("취소됨")
    nav(page, "차입금 관리")
    expect(page.locator("table tbody")).not_to_contain_text("이관테스트관")

    page.set_viewport_size({"width": 390, "height": 800})
    page.goto(BASE + "/#/admin/migration")
    page.wait_for_timeout(800)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "가로 넘침"
    shot(page, "m04_mobile")
    b.close()

print("E2E OK: 관리자만 메뉴, 양식 내려받기, 오류 파일 시트·행별 오류·실행 비활성, 검사 통과 → 이관 실행(확인 창), 대조표(미납 1회차), "
      "차입금 목록·원리금 테이블 V1, 이관 취소(사유 필수) → 목록에서 빠짐, 휴대폰 폭")
