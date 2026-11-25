"""발송완료 › 조회 결과 전체 Excel·PDF 내려받기 + 문구(안내문·발송) — 2026-11-25 요청. 새 데모 DB에서 실행."""
import io, sys, json, urllib.request, urllib.parse
from openpyxl import load_workbook
from playwright.sync_api import sync_playwright, expect
B=sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8010"; OUT=sys.argv[2] if len(sys.argv) > 2 else None
def api(path, tok, body=None, params=None):
    url=B+"/api/v1"+path+("?"+urllib.parse.urlencode(params) if params else "")
    req=urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers={"Authorization":f"Bearer {tok}","Content-Type":"application/json"}, method="POST" if body is not None else "GET")
    return json.load(urllib.request.urlopen(req))
adm=json.load(urllib.request.urlopen(B+"/api/v1/auth/login", data=urllib.parse.urlencode({"username":"demo-admin@example.com","password":"demo1234!"}).encode()))["access_token"]
tg=api("/notices/targets", adm, params={"include_issued": "true", "date_from": "2026-10-01", "date_to": "2027-03-31"})["items"]
picked, seen = [], set()
for x in tg:                                   # 서로 다른 기숙사 3곳
    if x["dormitory_id"] not in seen and not x["issued"]:
        seen.add(x["dormitory_id"]); picked.append(x)
    if len(picked) == 3: break
titles=[api(f"/dormitories/{x['dormitory_id']}/notices", adm, body={"due_date": x["due_date"]})["title"] for x in picked]
print("발송", titles)

with sync_playwright() as p:
    b=p.chromium.launch(); pg=b.new_page(viewport={"width":1600,"height":1000}, accept_downloads=True)
    errors=[]; pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(B); pg.fill("input[type=email]","demo-admin@example.com"); pg.fill("input[type=password]","demo1234!"); pg.click("button[type=submit]")
    pg.wait_for_timeout(2500)
    try: pg.get_by_role("button", name="나중에").click(timeout=1500)
    except Exception: pass
    nav=pg.get_by_role("navigation")
    for t in ("원리금 상환","안내문"):
        btn=nav.get_by_role("button", name=t)
        if btn.get_attribute("aria-expanded")=="false": btn.click()
    assert nav.get_by_text("안내공문").count()==0
    nav.get_by_role("link", name="발송완료").click(); pg.wait_for_timeout(1500)
    expect(pg.get_by_role("banner")).to_contain_text("안내문 · 발송완료")
    form=pg.get_by_role("search", name="발송완료 검색조건")
    form.get_by_label("기간 시작").fill("2026-10-01"); form.get_by_label("기간 종료").fill("2027-03-31")
    grp=form.get_by_role("group", name="조회 결과 전체 내려받기")
    expect(grp.get_by_role("button", name="Excel")).to_be_disabled()          # 조건을 바꾸고 조회 전에는 막힘
    form.get_by_role("button", name="조회").click(); pg.wait_for_timeout(1500)
    tbl=pg.get_by_role("table", name="발송완료 목록")
    n=tbl.locator("tbody tr").count(); assert n >= 3, n
    expect(grp).to_contain_text(f"{n}건")
    expect(tbl.locator("thead")).to_contain_text("발송일시"); expect(tbl.locator("thead")).to_contain_text("안내문")
    expect(pg.get_by_role("button", name="발송 전체")).to_be_visible()
    assert tbl.get_by_role("button", name="Excel").count()==n                 # 개별 Excel 유지
    (OUT and pg.screenshot(path=f"{OUT}/b1_발송완료.png"))

    with pg.expect_download() as dl: grp.get_by_role("button", name="Excel").click()
    d=dl.value; assert d.suggested_filename.startswith(f"발송완료_안내문_{n}건") and d.suggested_filename.endswith(".xlsx"), d.suggested_filename
    wb=load_workbook(io.BytesIO(open(d.path(),"rb").read())); assert wb.sheetnames[0]=="목록" and len(wb.sheetnames)==n+1, wb.sheetnames
    listed=[wb["목록"].cell(r,6).value for r in range(5,5+n)]
    shown=[tbl.locator("tbody tr").nth(i).locator("td").nth(3).inner_text().split("\n")[0] for i in range(n)]
    assert [s.replace("\n","") for s in shown]==[x for x in listed], (shown, listed)
    with pg.expect_download() as dl: grp.get_by_role("button", name="PDF").click()
    d=dl.value; raw=open(d.path(),"rb").read(); assert raw[:4]==b"%PDF" and d.suggested_filename.endswith(".pdf")
    import re; pages=max(int(m) for m in re.findall(rb"/Count (\d+)", raw)); assert pages>=n, (pages, n)

    # 진행 단계 카드로 거르면 그 목록만
    pg.get_by_role("button", name="기한 임박 미입금").click(); pg.wait_for_timeout(500)
    m=tbl.locator("tbody tr").count(); expect(grp).to_contain_text(f"{m}건")
    with pg.expect_download() as dl: grp.get_by_role("button", name="Excel").click()
    assert len(load_workbook(io.BytesIO(open(dl.value.path(),"rb").read())).sheetnames)==m+1

    # 작성 화면 문구
    pg.goto(B+"/#/notices/targets"); pg.wait_for_timeout(1500)
    expect(pg.get_by_role("banner")).to_contain_text("안내문 · 발송대상 현황")
    pg.goto(B+f"/#/notices?dormitory={picked[0]['dormitory_id']}&due={picked[0]['due_date']}"); pg.wait_for_timeout(2500)
    expect(pg.get_by_text("2. 안내문 미리보기")).to_be_visible()
    expect(pg.get_by_role("button", name="이 내용으로 발송")).to_be_visible()
    body=pg.locator("main").inner_text(); assert "공문" not in body and "발급" not in body, [l for l in body.split("\n") if "공문" in l or "발급" in l]
    (OUT and pg.screenshot(path=f"{OUT}/b2_작성.png"))
    assert not errors, errors
    b.close()
print("E2E OK: 발송완료 조회 결과 전체 Excel(목록+안내문별 시트)·PDF, 단계 필터 반영, 개별 Excel 유지, 안내문·발송 문구")
