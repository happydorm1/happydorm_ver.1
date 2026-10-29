"""
Phase 17 증빙자료·상환일 메모 E2E — 데이터를 바꾸므로 '새로 만든 데모 DB'에서 돌린다.
    python e2e/attach.py http://127.0.0.1:4173 [스크린샷 폴더]
"""
import re
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:4173"
SHOTS = sys.argv[2] if len(sys.argv) > 2 else None
TMP = Path(tempfile.mkdtemp())
PDF = TMP / "상환유예_승인공문.pdf"
PDF.write_bytes(b"%PDF-1.4\n% e2e evidence\n" + b"x" * 3000)
EXE = TMP / "setup.exe"
EXE.write_bytes(b"MZ" + b"\0" * 100)
RECEIPT = TMP / "입금확인증.png"
RECEIPT.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 500)


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
    page = b.new_page(viewport={"width": 1440, "height": 900}, accept_downloads=True)
    page.on("pageerror", lambda e: errors.append(str(e)))
    login(page, "demo-fund@example.com")

    # 1) 차입금 상세 → 증빙자료 탭: 실행파일 거부, PDF 올리기·내려받기(원본과 같음)
    nav(page, "차입금 관리")
    page.get_by_role("link", name=re.compile("가람관")).first.click()
    page.get_by_role("tab", name="증빙자료").click()
    panel = page.locator("section[aria-label='증빙자료 — 이 차입금 전체']")
    expect(panel).to_contain_text("첨부된 증빙자료가 없습니다")
    panel.get_by_label("증빙 파일").set_input_files(str(EXE))
    expect(panel.get_by_role("alert")).to_contain_text("올릴 수 없는 형식")
    expect(panel.get_by_role("button", name="올리기")).to_be_disabled()
    panel.get_by_label("증빙 파일").set_input_files(str(PDF))
    panel.get_by_label("첨부 설명").fill("상환유예 승인 공문(E2E)")
    panel.get_by_role("button", name="올리기").click()
    expect(panel).to_contain_text("상환유예_승인공문.pdf을(를) 올렸습니다")
    expect(panel.locator("li", has_text="상환유예_승인공문.pdf")).to_contain_text("상환유예 승인 공문(E2E)")
    with page.expect_download() as dl:
        panel.get_by_role("button", name="상환유예_승인공문.pdf 내려받기").click()
    assert Path(dl.value.path()).read_bytes() == PDF.read_bytes()
    assert dl.value.suggested_filename == "상환유예_승인공문.pdf"
    shot(page, "a01_loan_evidence")

    # 2) 원리금 테이블 행 메모: 추가 → 비고에 보임 → Excel 비고에 반영
    page.get_by_role("tab", name="원리금 테이블").click()
    table = page.locator("section[aria-label='원리금 테이블']")
    first_btn = table.get_by_role("button", name=re.compile(r"^\d{4}-\d{2}-\d{2} 메모$")).nth(4)
    due = first_btn.get_attribute("aria-label").split()[0]
    first_btn.click()
    d = page.get_by_role("dialog")
    expect(d).to_contain_text("남긴 메모가 없습니다")
    d.get_by_label("새 메모").fill("입금 지연 협의(E2E 통화)")
    d.get_by_role("button", name="메모 저장").click()
    expect(d).to_contain_text("V1 때 작성")
    shot(page, "a02_note_dialog")
    d.locator("footer").get_by_role("button", name="닫기").click()
    row = table.locator("tr", has_text=due).first
    expect(row).to_contain_text("📝 입금 지연 협의(E2E 통화)")
    expect(row.get_by_role("button", name=f"{due} 메모")).to_have_text("메모 1")
    with page.expect_download() as dl:
        page.get_by_role("button", name="Excel 다운로드").click()
    from openpyxl import load_workbook
    xl = TMP / "schedule.xlsx"
    dl.value.save_as(str(xl))
    ws = load_workbook(xl).active
    assert any(str(r[2].value)[:10] == due and "메모: 입금 지연 협의(E2E 통화)" in str(r[16].value) for r in ws.iter_rows(min_row=2))
    shot(page, "a03_schedule_note")

    # 3) 이벤트 등록 직후 증빙 첨부 → 이벤트 이력의 '증빙'에서 확인 → 차입금 증빙 탭에 '이벤트'로 함께 보임
    nav(page, "이벤트 관리")
    pick(page.get_by_label("차입금 선택"), "온새누리관")
    page.get_by_role("radiogroup", name="이벤트 종류").get_by_text("조기상환").click()
    page.get_by_label("접수일(적용일)").fill("2025-01-15")
    page.get_by_label("조기상환 금액").fill("50000000")
    page.get_by_role("button", name="적용하기").click()
    page.get_by_role("dialog").get_by_role("button", name="적용", exact=True).click()
    ev = page.locator("section[aria-label='이 이벤트의 증빙자료']")
    expect(ev).to_be_visible()
    ev.get_by_label("증빙 파일").set_input_files(str(PDF))
    ev.get_by_role("button", name="올리기").click()
    expect(ev.locator("li")).to_have_count(1)
    shot(page, "a04_event_evidence")
    page.get_by_role("tab", name="이벤트 이력").click()
    page.locator("tr", has_text="온새누리관").first.get_by_role("button", name=re.compile("증빙$")).click()
    d = page.get_by_role("dialog", name=re.compile("^증빙자료 —"))
    expect(d.locator("li", has_text="상환유예_승인공문.pdf")).to_have_count(1)
    # 사용중지(사유 필수) → 목록에서 빠지고 '사용중지 포함'으로 보임
    d.get_by_role("button", name="상환유예_승인공문.pdf 사용중지").click()
    stop = page.get_by_role("dialog", name=re.compile("첨부 사용중지"))
    stop.get_by_label("사용중지 사유").fill("E2E 잘못 올림")
    stop.get_by_role("button", name="사용중지").click()
    expect(d).to_contain_text("첨부된 증빙자료가 없습니다")
    d.get_by_label("사용중지 포함").check()
    expect(d).to_contain_text("사용중지 사유: E2E 잘못 올림")
    d.locator("footer").get_by_role("button", name="닫기").click()

    # 4) 납부 입금증
    nav(page, "실제 상환관리")
    card = page.locator("section", has_text="납부기한이 지난 미납")
    card.locator("tr", has_text="바름학사").get_by_role("link", name="납부 입력 →").click()
    page.locator("tr", has_text="미납").filter(has=page.get_by_role("button", name="납부 입력")).first.get_by_role("button", name="납부 입력").click()
    dlg = page.get_by_role("dialog")
    dlg.get_by_label("비고").fill("E2E 입금증")
    dlg.get_by_role("button", name="저장").click()
    pays = page.locator("section[aria-label='납부 내역(취소 포함)']")
    pays.locator("tr", has_text="E2E 입금증").get_by_role("button", name=re.compile("입금증$")).click()
    d = page.get_by_role("dialog")
    d.get_by_label("증빙 파일").set_input_files(str(RECEIPT))
    d.get_by_role("button", name="올리기").click()
    expect(d.locator("li", has_text="입금확인증.png")).to_have_count(1)
    shot(page, "a05_payment_receipt")
    d.locator("footer").get_by_role("button", name="닫기").click()

    # 5) 조회자: 증빙 탭 보이지만 올리기·사용중지 없음, 메모는 읽기만
    v = b.new_page(viewport={"width": 1280, "height": 900})
    v.on("pageerror", lambda e: errors.append(str(e)))
    login(v, "demo-viewer@example.com")
    nav(v, "차입금 관리")
    v.get_by_role("link", name=re.compile("가람관")).first.click()
    v.get_by_role("tab", name="증빙자료").click()
    vp = v.locator("section[aria-label='증빙자료 — 이 차입금 전체']")
    expect(vp).to_contain_text("상환유예_승인공문.pdf")
    expect(vp.get_by_role("button", name="올리기")).to_have_count(0)
    expect(vp.get_by_role("button", name=re.compile("사용중지$"))).to_have_count(0)
    v.get_by_role("tab", name="원리금 테이블").click()
    v.get_by_role("button", name=f"{due} 메모").click()
    expect(v.get_by_role("dialog")).to_contain_text("입금 지연 협의(E2E 통화)")
    expect(v.get_by_role("dialog").get_by_label("새 메모")).to_have_count(0)

    # 6) 휴대폰 폭: 증빙 탭 가로 넘침 없음
    m = b.new_page(viewport={"width": 390, "height": 844})
    login(m, "demo-fund@example.com")
    m.goto(f"{BASE}/#/loans")
    m.get_by_role("link", name=re.compile("가람관")).first.click()
    m.get_by_role("tab", name="증빙자료").click()
    m.wait_for_timeout(600)
    sw = m.evaluate("document.documentElement.scrollWidth")
    assert sw <= 390, f"증빙 탭 가로 넘침 {sw}"
    shot(m, "a06_mobile_evidence")
    b.close()
    assert not errors, errors
print("E2E OK: 증빙 올리기(형식 거부)·내려받기 원본 일치, 상환일 메모·Excel 비고, 이벤트 직후 증빙·이력 증빙·사용중지, 납부 입금증, 조회자 읽기 전용, 휴대폰 폭")
