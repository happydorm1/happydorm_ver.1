#!/usr/bin/env bash
# Codespace 최초 생성 때 한 번 실행된다(postCreateCommand).
set -e
cd "$(dirname "$0")/../backend"
PGHOST="${PGHOST:-db}"; PGPORT="${PGPORT:-5432}"

echo "▶ 1/3 Python 패키지 설치"
[ -x venv/bin/python ] || python3 -m venv venv
venv/bin/python -m pip install --upgrade pip -q
venv/bin/python -m pip install -r requirements.txt -q

echo "▶ 2/3 PostgreSQL 준비 대기"
for i in $(seq 1 60); do
  if venv/bin/python - <<PY 2>/dev/null
import os, psycopg
psycopg.connect(host="$PGHOST", port="$PGPORT", user=os.environ.get("PG_SUPERUSER","postgres"),
                password=os.environ["PG_SUPERUSER_PASSWORD"], dbname="postgres", connect_timeout=2).close()
PY
  then echo "  DB 연결 확인"; break; fi
  [ "$i" = 60 ] && { echo "  [오류] DB가 2분 안에 켜지지 않았습니다"; exit 1; }
  sleep 2
done

echo "▶ 3/3 DB 계정·테이블·데모 데이터 생성"
venv/bin/python local_setup.py --mode demo --pg-host "$PGHOST" --pg-port "$PGPORT" --pg-user "${PG_SUPERUSER:-postgres}"
echo "✔ 준비 완료 — 서버는 자동으로 켜집니다(포트 8000)."
