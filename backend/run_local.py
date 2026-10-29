"""로컬 실행기 — API와 화면(web/)을 한 주소(http://127.0.0.1:8000)로 함께 띄운다.

원래 구성(백엔드 8000 + 프론트 개발서버 5173)과 같은 API를 쓰며, 미리 빌드한 화면 파일만 같이 제공한다.
Node.js 없이 Python + PostgreSQL만 있으면 된다.
"""
import os
import sys
import threading
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.chdir(HERE)                      # .env 를 backend 폴더에서 읽도록
sys.path.insert(0, str(HERE))

import uvicorn  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from app.main import app  # noqa: E402

WEB = Path(os.environ.get("WEB_DIR", HERE.parent / "web")).resolve()
if not (WEB / "index.html").exists():
    sys.exit(f"[오류] 화면 파일이 없습니다: {WEB}\\index.html  (frontend 폴더에서 npm run build 후 dist를 web으로 복사)")
app.mount("/", StaticFiles(directory=WEB, html=True), name="web")   # API 경로(/api, /docs, /health)가 먼저 처리된다

if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    url = f"http://127.0.0.1:{port}"
    print(f"\n  기숙사 차입금 관리 시스템 실행 중 →  {url}\n  (끄려면 이 창에서 Ctrl+C)\n")
    if os.environ.get("NO_BROWSER") != "1":
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")
