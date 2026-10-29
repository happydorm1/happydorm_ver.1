#!/usr/bin/env bash
cd "$(dirname "$0")/backend"
[ -x venv/bin/python ] || { echo "먼저 ./1_setup_mac_linux.sh 를 실행하세요."; exit 1; }
exec venv/bin/python run_local.py
