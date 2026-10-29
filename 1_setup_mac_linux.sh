#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/backend"
PY=""
for c in python3.13 python3.12 python3; do
  if command -v $c >/dev/null && $c -c "import sys;sys.exit(0 if sys.version_info[:2] in ((3,12),(3,13)) else 1)"; then PY=$c; break; fi
done
[ -z "$PY" ] && { echo "[오류] Python 3.12 또는 3.13이 필요합니다."; exit 1; }
echo "사용: $PY"
[ -x venv/bin/python ] || $PY -m venv venv
venv/bin/python -m pip install --upgrade pip -q
venv/bin/python -m pip install -r requirements.txt
venv/bin/python local_setup.py "$@"
echo; echo "설치 완료. ./2_start_mac_linux.sh 로 실행하세요."
