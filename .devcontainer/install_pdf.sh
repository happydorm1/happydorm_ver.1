#!/usr/bin/env bash
# 안내공문 PDF 변환기(LibreOffice) + 한글 글꼴 설치. Codespace에서 한 번 실행(약 3~8분).
#   bash .devcontainer/install_pdf.sh
set -u
if command -v soffice >/dev/null 2>&1; then echo "✔ LibreOffice가 이미 설치되어 있습니다: $(command -v soffice)"; exit 0; fi
echo "▶ LibreOffice(Calc)·한글 글꼴 설치 중… 몇 분 걸립니다."
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends libreoffice-calc-nogui fonts-noto-cjk \
  || sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends libreoffice-calc fonts-noto-cjk
if command -v soffice >/dev/null 2>&1; then
  echo "✔ 설치 완료: $(command -v soffice) — 서버를 다시 켜면(Ctrl+C → bash .devcontainer/start.sh) PDF 버튼이 동작합니다."
else
  echo "✖ 설치하지 못했습니다. 위 오류 메시지를 확인하세요."; exit 1
fi
