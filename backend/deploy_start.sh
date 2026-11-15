#!/usr/bin/env bash
# Render(클라우드) 시작 스크립트 — Dockerfile의 CMD. DB 준비 → (비어 있으면) 데모 데이터 → 서버 실행.
set -e
cd "$(dirname "$0")"
export HOST=0.0.0.0
export PORT="${PORT:-10000}"
export NO_BROWSER=1
# 화면과 서버가 같은 주소라 CORS는 자기 주소만 허용(Render가 RENDER_EXTERNAL_URL을 넣어 준다)
export CORS_ORIGINS="${CORS_ORIGINS:-${RENDER_EXTERNAL_URL:-}}"

python -m app.jobs.deploy_boot

# 하루 1회 배치(알림·공휴일 갱신·계산 검증)를 서버가 켜질 때마다 한 번 돌린다 — 무료 서버는 자주 잠들었다 깨므로 사실상 매일 실행된다
( sleep 30; python -m app.jobs.daily_scan > /tmp/daily_scan.log 2>&1 || echo "[알림 배치 실패] /tmp/daily_scan.log 참고" ) &

exec python run_local.py
