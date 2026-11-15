# Render(클라우드) 배포용 — 화면(web) + 서버(backend)를 한 컨테이너로 실행한다. DB는 Render PostgreSQL을 쓴다.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Seoul

# 공문 PDF 변환(LibreOffice) + 한글 글꼴 + 한국 시간대
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      libreoffice-calc-nogui fonts-noto-cjk tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend backend
COPY web web

CMD ["bash", "backend/deploy_start.sh"]
