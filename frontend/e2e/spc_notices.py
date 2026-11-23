"""SPC 담당자 — 원리금 상환 › 안내공문 › 접수공문(2026-11-22 요청)."""
import sys, json, urllib.request, urllib.parse
from playwright.sync_api import sync_playwright, expect
B=sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8010"; OUT=sys.argv[2] if len(sys.argv) > 2 else None
def api(path, tok, body=None, params=None):
    url=B+"/api/v1"+path+("?"+urllib.parse.urlencode(params) if params else "")
    req=urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers={"Authorization":f"Bearer {tok}","Content-Type":"application/json"}, method="POST" if body is not None else "GET")
    return json.load(urllib.request.urlopen(req))
def login(u):
    d=urllib.parse.urlencode({"username":u,"password":"demo1234!"}).encode()
    return json.load(urllib.request.urlopen(B+"/api/v1/auth/login", data=d))["access_token"]
spc=login("demo-spc@example.com"); adm=login("demo-admin@example.com")
dorms=api("/dormitories", spc); dorm=dorms[0]; print("SPC 기숙사", [d["dormitory_name"] for d in dorms])
tg=api("/notices/targets", adm, params={"dormitory_id": dorm["id"], "include_issued": "true", "date_from": "2026-01-01", "date_to": "2027-12-31"})
items=tg["items"] if isinstance(tg, dict) else tg
due=items[0]["due_date"]; n=api(f"/dormitories/{dorm['id']}/notices", adm, body={"due_date": due}); print("발급", n["title"])
with sync_playwright() as p:
    b=p.chromium.launch(); pg=b.new_page(viewport={"width":1600,"height":1000})
    pg.goto(B); pg.fill("input[type=email]","demo-spc@example.com"); pg.fill("input[type=password]","demo1234!"); pg.click("button[type=submit]")
    pg.wait_for_timeout(2500)
    try: pg.get_by_role("button", name="나중에").click(timeout=1500)
    except Exception: pass
    nav=pg.get_by_role("navigation")
    for t in ("원리금 상환","안내공문"):
        btn=nav.get_by_role("button", name=t)
        if btn.get_attribute("aria-expanded")=="false": btn.click()
    (OUT and pg.screenshot(path=f"{OUT}/0_메뉴.png"))
    nav.get_by_role("link", name="접수공문").click(); pg.wait_for_timeout(2000)
    tbl=pg.get_by_role("table", name="접수공문 목록"); expect(tbl).to_contain_text(n["title"]); expect(tbl.locator("tr", has_text=n["title"])).to_contain_text("새 공문")
    assert pg.get_by_text("권한이 없습니다").count()==0
    (OUT and pg.screenshot(path=f"{OUT}/1_접수공문.png"))
    # 알림 → 공문 링크
    pg.goto(B+"/#/notifications"); pg.wait_for_timeout(2000)
    pg.locator("li", has_text=n["title"].split(" ")[0]).first.get_by_role("link", name="공문").click()
    dlg=pg.get_by_role("dialog", name=n["title"]); dlg.wait_for(timeout=10000)
    assert pg.get_by_text("권한이 없습니다").count()==0
    (OUT and pg.screenshot(path=f"{OUT}/2_알림에서_공문열기.png"))
    dlg.get_by_role("button", name="닫기").last.click(); pg.wait_for_timeout(1500)
    expect(pg.get_by_role("table", name="접수공문 목록").locator("tr", has_text=n["title"])).to_contain_text("접수 완료")
    (OUT and pg.screenshot(path=f"{OUT}/3_접수완료.png"))
    # 옛 경로(재단용 작성 화면)로 들어와도 SPC 화면
    pg.goto(B+f"/#/notices?tab=issue&dormitory={dorm['id']}"); pg.wait_for_timeout(1500)
    assert pg.get_by_role("table", name="접수공문 목록").count()==1 and pg.get_by_text("권한이 없습니다").count()==0
    print("E2E OK: SPC 접수공문 메뉴, 새 공문 → 알림 공문 링크로 열기 → 접수 완료, 재단용 작성 화면 대신 접수공문, 권한 오류 없음")
iss=api("/notices/issued", adm, params={"dormitory_id": dorm["id"]})
print("재단 화면 수신:", [ (x["title"], x["received"], x["received_via"]) for x in iss["items"] if x["notice_id"]==n["id"]])
