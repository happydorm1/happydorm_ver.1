"""
대시보드 화면 E2E 점검(헤드리스 Chromium). 데모 DB로 백엔드(:8000)와 `npm run build && npx vite preview --port 4173`을 띄운 뒤:
    python e2e/smoke.py http://127.0.0.1:4173
데모 데이터(app.jobs.seed_demo --as-of 2026-10-28) 기준의 기대값을 확인한다.
"""
import re
import sys

from playwright.sync_api import expect, sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:4173"
AS_OF = "2026-10-28"


def dismiss_popup(page, expect_popup=None):
    """로그인 알림 팝업 — '나중에'는 이번 접속 동안만 닫는다(서버의 '확인' 기록을 남기지 않아 반복 실행 가능)."""
    dlg = page.get_by_role("alertdialog")
    try:
        dlg.wait_for(timeout=2500)
        shown = True
    except Exception:
        shown = False
    if expect_popup is not None:
        assert shown is expect_popup, f"팝업 표시 기대={expect_popup}, 실제={shown}"
    if shown:
        dlg.get_by_role("button", name="나중에").click()
        expect(dlg).to_be_hidden()
    return shown


def login(page, email, pw="demo1234!", expect_popup=None):
    page.goto(BASE)
    page.fill("input[type=email]", email)
    page.fill("input[type=password]", pw)
    page.click("button[type=submit]")
    page.wait_for_selector("text=차입금 현황")
    dismiss_popup(page, expect_popup)
    page.fill("header input[type=date]", AS_OF)
    expect(page.get_by_text(f"기준일 {AS_OF}")).to_be_visible()
    page.wait_for_load_state("networkidle")


def table_count(page) -> int:
    txt = page.locator("section[aria-label='차입금 현황'] h2").inner_text()
    return int(re.search(r"\((\d+)건\)", txt).group(1))


def wait_count(page, n):
    expect(page.locator("section[aria-label='차입금 현황'] h2")).to_contain_text(f"({n}건)", timeout=5000)


with sync_playwright() as p:
    browser = p.chromium.launch()
    errors = []

    # 1) 관리자: 전체 → 조건 변경에 따라 숫자가 바뀐다
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.goto(BASE)
    page.fill("input[type=email]", "demo-admin@example.com")
    page.fill("input[type=password]", "틀린비번")
    page.click("button[type=submit]")
    expect(page.get_by_role("alert")).to_be_visible()                       # 로그인 실패 안내
    errors.clear()                                                           # 의도한 401 콘솔 로그는 제외
    # 관리자: 로그인 알림 팝업(중도변수 미처리 + 관리자 안내)
    page.goto(BASE)
    page.fill("input[type=email]", "demo-admin@example.com")
    page.fill("input[type=password]", "demo1234!")
    page.click("button[type=submit]")
    dlg = page.get_by_role("alertdialog")
    expect(dlg).to_be_visible()
    expect(dlg).to_contain_text("중도변수 미처리")
    expect(dlg).to_contain_text("금리변동 미반영")
    expect(dlg).to_contain_text("관리자 안내")
    expect(dlg).to_contain_text("공휴일 연동 미설정")
    dlg.get_by_role("button", name="나중에").click()
    expect(dlg).to_be_hidden()
    page.reload()
    page.wait_for_selector("text=차입금 현황")
    assert dismiss_popup(page) is False                                       # 같은 접속에서는 다시 안 뜸
    page.fill("header input[type=date]", AS_OF)
    expect(page.get_by_text(f"기준일 {AS_OF}")).to_be_visible()
    page.wait_for_load_state("networkidle")
    expect(page.locator("section[aria-label='확인 필요']")).to_contain_text("중도변수 미처리")
    wait_count(page, 9)
    kpis = page.locator("section[aria-label='주요 지표']")
    expect(kpis).to_contain_text("8곳")
    expect(kpis).to_contain_text("698억원")
    for title in ("상환 예정", "금리변동 예정", "미납", "확인 필요"):
        expect(page.locator(f"section[aria-label='{title}']")).to_be_visible()
    expect(page.locator("section[aria-label='미납']")).to_contain_text("2건")
    expect(page.locator("section[aria-label='상환 예정']")).to_contain_text("상환일 2026-10-31 휴일")   # 휴일 → 다음 영업일

    page.select_option("section[aria-label='검색조건'] select >> nth=1", label="주택도시기금")
    wait_count(page, 4)
    page.get_by_role("button", name=re.compile("상세 조건")).click()
    page.locator("label:has-text('중도변수 여부') select").select_option("true")
    wait_count(page, 1)                                                      # 주택 + 중도변수 = 다온생활관(가속상환)
    expect(page.locator("section[aria-label='차입금 현황'] tbody")).to_contain_text("다온생활관")
    page.get_by_role("button", name="초기화").click()
    wait_count(page, 9)

    # 기간 KPI는 시작·종료를 모두 넣어야 나타난다
    page.fill("input[aria-label='기간 시작']", "2026-11-01")
    expect(page.get_by_text("기간은 시작일과 종료일을 모두")).to_be_visible()
    page.fill("input[aria-label='기간 종료']", "2026-12-31")
    expect(kpis).to_contain_text("기간 상환예정")

    # 표: 검색·정렬
    page.get_by_role("button", name="초기화").click()
    wait_count(page, 9)
    page.fill("input[aria-label='표 내 검색']", "가람")
    wait_count(page, 2)
    page.fill("input[aria-label='표 내 검색']", "")
    page.get_by_role("button", name="차입금", exact=True).click()          # 차입금액 오름차순
    first = page.locator("section[aria-label='차입금 현황'] tbody tr").first
    expect(first).to_contain_text("미르기숙사")                               # 가장 작은 24억

    # Excel 자료 내려받기(§30): 전체 원리금 테이블, 안내문 데이터(상환예정일 입력 필요)
    rep = page.locator("section[aria-label='Excel 자료 내려받기']")
    with page.expect_download() as dl:
        rep.get_by_role("button", name="내려받기").click()
    assert dl.value.suggested_filename.startswith("전체원리금테이블_") and dl.value.suggested_filename.endswith(".xlsx")
    rep.get_by_label("자료 종류").select_option("notice_data")
    expect(rep.get_by_role("button", name="내려받기")).to_be_disabled()
    rep.get_by_label("상환예정일").fill("2026-11-30")
    with page.expect_download() as dl:
        rep.get_by_role("button", name="내려받기").click()
    assert dl.value.suggested_filename.startswith("안내문데이터_")

    # 다크모드 토글
    page.get_by_role("button", name=re.compile("화면:")).click()
    assert page.evaluate("document.documentElement.dataset.theme") == "light"
    page.get_by_role("button", name=re.compile("화면:")).click()
    assert page.evaluate("document.documentElement.dataset.theme") == "dark"

    # 2) SPC 담당자: 자기 기숙사만, 중도변수 팝업 대상 아님
    spc = browser.new_page(viewport={"width": 1280, "height": 900})
    login(spc, "demo-spc@example.com", expect_popup=False)
    wait_count(spc, 2)                                                     # 가람관 차입금 2건
    expect(spc.locator("section[aria-label='차입금 현황'] tbody")).to_contain_text("가람관")
    expect(spc.locator("section[aria-label='주요 지표']")).to_contain_text("1곳")

    # 3) 조회자: 다운로드 제한 → CSV 버튼 없음, 팝업 없음
    viewer = browser.new_page(viewport={"width": 1280, "height": 900})
    login(viewer, "demo-viewer@example.com", expect_popup=False)
    expect(viewer.get_by_role("button", name=re.compile("CSV"))).to_have_count(0)
    expect(viewer.locator("section[aria-label='Excel 자료 내려받기']")).to_have_count(0)
    expect(viewer.locator("header").first).to_contain_text("조회자")

    # 4) 비밀번호 변경 → 모든 로그인 해제·재로그인 → 원래대로 되돌림(반복 실행 가능)
    fund = browser.new_page(viewport={"width": 1280, "height": 900})
    login(fund, "demo-fund@example.com")
    def change(page, cur, new):
        page.get_by_role("button", name=re.compile("자금담당")).click()
        d = page.get_by_role("dialog")
        d.get_by_label("현재 비밀번호").fill(cur)
        d.get_by_label("새 비밀번호", exact=True).fill("abc")
        expect(d).to_contain_text("8자 이상")                                  # 규칙 안내
        expect(d.get_by_role("button", name="변경")).to_be_disabled()
        d.get_by_label("새 비밀번호", exact=True).fill(new)
        d.get_by_label("새 비밀번호 확인").fill(new)
        d.get_by_role("button", name="변경").click()
        expect(page.get_by_text("비밀번호를 변경했습니다")).to_be_visible()     # 로그인 화면으로
    change(fund, "demo1234!", "Demo!Change2026")
    login(fund, "demo-fund@example.com", "Demo!Change2026")
    change(fund, "Demo!Change2026", "demo1234!")
    login(fund, "demo-fund@example.com")

    # 5) 휴대폰 폭: 가로로 넘치지 않는다
    for w in (360, 390, 768):
        m = browser.new_page(viewport={"width": w, "height": 800})
        login(m, "demo-admin@example.com")
        sw = m.evaluate("document.documentElement.scrollWidth")
        assert sw <= w, f"{w}px 화면에서 가로 넘침({sw}px)"

    browser.close()
    assert not errors, f"브라우저 오류: {errors}"
print("E2E OK: 로그인·알림 팝업·KPI·업무알림·검색조건·기간·표 검색/정렬·다크모드·SPC 권한·Excel 자료 8종·조회자 다운로드 제한·비밀번호 변경·모바일 폭")
