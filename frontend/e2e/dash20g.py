"""
대시보드 Phase 20g E2E — 데모 DB(seed_demo --as-of 2026-10-28), 브라우저 시계 2026-10-28.
    python e2e/dash20g.py http://127.0.0.1:8000 [스크린샷 폴더]
확인: KPI 순서·'다음 분기 상환예정(2027년 1분기)', 차트|재원별 경계 = 미납|확인 필요 경계,
차입금 현황 기숙사 단위(건수·재원 내역) → 기숙사명 클릭 팝업 → 차입금 코드 → 상세 → 뒤로 가기 시 팝업 다시 열림.
"""
import re
import sys

from playwright.sync_api import expect, sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
SHOTS = sys.argv[2] if len(sys.argv) > 2 else None


def shot(page, name, full=True):
    if SHOTS:
        page.screenshot(path=f"{SHOTS}/{name}.png", full_page=full)


with sync_playwright() as p:
    browser = p.chromium.launch()
    errors = []
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(BASE)
    page.fill("input[type=email]", "demo-admin@example.com")
    page.fill("input[type=password]", "demo1234!")
    page.click("button[type=submit]")
    page.wait_for_selector("text=차입금 현황")
    try:
        page.get_by_role("alertdialog").wait_for(timeout=2500)
        page.get_by_role("button", name="나중에").click()
    except Exception:
        pass
    page.wait_for_load_state("networkidle")

    # 1) KPI 순서 + 다음 분기
    kpis = page.locator("section[aria-label='주요 지표'] > div")
    labels = [re.sub(r"\(.*?\)|●|\s+", " ", kpis.nth(i).locator("div").first.inner_text()).strip() for i in range(kpis.count())]
    assert labels == ["전체 기숙사", "총 차입금", "총 잔여원금", "금월 상환예정", "다음 분기 상환예정",
                      "안내공문 미수신", "미납", "금리변동 예정"], labels
    nq = kpis.nth(4)
    expect(nq).to_contain_text("(2027년 1분기)")
    assert nq.locator("span.text-\\[10px\\]").evaluate("e => getComputedStyle(e).fontSize") == "10px"
    expect(page.locator("section[aria-label='주요 지표']")).not_to_contain_text("익월")

    # 2) 경계 맞춤: '미납' 오른쪽 끝 ≈ 차트 카드 오른쪽 끝, '확인 필요' 왼쪽 ≈ 재원별 왼쪽
    box = lambda sel: page.locator(sel).first.bounding_box()
    over, check = box("section[aria-label='미납']"), box("section[aria-label='확인 필요']")
    fund = box("section[aria-label='재원별 잔여원금']")
    chart = page.locator("section[aria-label='재원별 잔여원금']").locator("xpath=preceding-sibling::*[1]").bounding_box()
    assert abs((over["x"] + over["width"]) - (chart["x"] + chart["width"])) <= 1, (over, chart)
    assert abs(check["x"] - fund["x"]) <= 1 and abs(check["width"] - fund["width"]) <= 1, (check, fund)
    shot(page, "g01_dashboard")

    # 3) 차입금 현황: 기숙사 단위
    sec = page.locator("section[aria-label='차입금 현황']")
    expect(sec.locator("h2")).to_contain_text("기숙사 8곳 · 차입금 9건")
    heads = sec.locator("thead th").all_inner_texts()
    assert [h.replace(" ▲", "").replace(" ▼", "") for h in heads] == ["기숙사명 · 유형", "차입금", "상환완료", "잔여원금", "다음 상환액", "차입금 건수"], heads
    expect(sec.locator("tbody tr")).to_have_count(8)
    garam = sec.locator("tbody tr", has_text="가람관")
    expect(garam).to_contain_text("2건")
    expect(garam).to_contain_text(re.compile(r"\(.+ 1건 / .+ 1건\)|\(.+ 2건\)"))
    expect(garam).to_contain_text("납부기한")

    # 4) 팝업 → 차입금 코드 → 상세 → 뒤로 → 팝업
    garam.get_by_role("button", name=re.compile("가람관")).click()
    dlg = page.get_by_role("dialog")
    expect(dlg).to_contain_text("차입금 2건")
    dh = dlg.locator("thead th").all_inner_texts()
    assert dh == ["차입금 코드", "재원 · 상환방식", "차입금", "상환완료", "잔여원금", "다음 상환액", "다음 납부기한", "상태"], dh
    expect(dlg.locator("tbody tr")).to_have_count(2)
    expect(dlg.locator("tfoot")).to_contain_text("합계")
    assert "dorm=" in page.url
    shot(page, "g02_popup", full=False)
    code = dlg.locator("tbody tr").first.get_by_role("link").inner_text()
    dlg.locator("tbody tr").first.get_by_role("link").click()
    expect(page).to_have_url(re.compile(r"#/loans/[0-9a-f-]{36}"))
    expect(page.get_by_text(code).first).to_be_visible()
    page.go_back()
    expect(page.get_by_role("dialog")).to_contain_text("가람관")
    page.get_by_role("dialog").get_by_role("button", name="닫기").click()
    expect(page.get_by_role("dialog")).to_be_hidden()
    assert "dorm=" not in page.url, page.url
    page.reload()
    page.wait_for_selector("text=차입금 현황")
    expect(page.get_by_role("dialog")).to_have_count(0)

    # 5) 좁은 화면에서도 깨지지 않음(1열)
    page.set_viewport_size({"width": 820, "height": 900})
    shot(page, "g03_narrow")
    assert not errors, errors
    browser.close()
print("E2E OK: KPI 순서·다음 분기·경계 맞춤·기숙사 단위 표·팝업·상세 이동·뒤로 가기")
