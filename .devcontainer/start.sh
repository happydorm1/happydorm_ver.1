#!/usr/bin/env bash
# Codespace에 접속할 때마다 실행된다(postAttachCommand). 이 터미널을 닫으면 서버도 꺼진다.
cd "$(dirname "$0")/../backend"
if [ ! -f .env ] || [ ! -x venv/bin/python ]; then
  echo "준비가 끝나지 않았습니다. 'bash .devcontainer/setup.sh' 를 먼저 실행하세요."; exit 1
fi
if (exec 3<>/dev/tcp/127.0.0.1/8000) 2>/dev/null; then
  echo "서버가 이미 실행 중입니다(포트 8000). 포트(PORTS) 탭에서 8000번의 지구본 아이콘을 누르세요."; exit 0
fi
echo "로그인: demo-admin@example.com / demo1234!   (끄기: Ctrl+C, 다시 켜기: bash .devcontainer/start.sh)"
HOST=0.0.0.0 NO_BROWSER=1 exec venv/bin/python run_local.py
