"""공문 PDF — LibreOffice가 없거나 실패해도 내장 방식(fpdf2)으로 만들어진다(2026-11-23)."""
import pytest

from app.engine import notice_pdf
from app.engine.notice_export import PdfUnavailableError, notice_pdf as make_pdf

DATA = {
    "title": "테스트기숙사 '27년 1분기 원리금 계산내역", "payment_deadline": "2027-03-02",
    "account": {"bank_account": "데모은행 000-000-000000", "account_holder": "데모기금"},
    "interest_periods": [{"label": "주택도시기금", "start": "2026-11-30", "end": "2027-02-28", "days": 90, "ipmt": True,
                          "note": "초일 산입, 말일 불산입"}],
    "balances": [{"label": "주택도시기금", "ipmt": True, "assigned_amount": 1_036_307_500, "balance_before": 900_000_000,
                  "principal": 23_000_000, "balance_after": 877_000_000, "check": True}],
    "payments": [{"label": "주택도시기금", "ipmt": True, "rate": 1.571, "balance_before": 900_000_000, "principal": 23_000_000,
                  "interest": 3_500_000, "total": 26_500_000}],
    "notes": ["※ 테스트 비고"],
}

try:
    notice_pdf._fonts()
    HAS_FONT = True
except notice_pdf.FontMissing:
    HAS_FONT = False


@pytest.mark.skipif(not HAS_FONT, reason="한글 글꼴 없음")
def test_builtin_pdf_has_korean_text():
    pdf = notice_pdf.build_notice_pdf(DATA)
    assert pdf.startswith(b"%PDF") and len(pdf) > 5_000


@pytest.mark.skipif(not HAS_FONT, reason="한글 글꼴 없음")
def test_auto_falls_back_when_libreoffice_missing(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "PDF_ENGINE", "auto")
    monkeypatch.setattr(settings, "LIBREOFFICE_PATH", "/없는/경로/soffice")      # LibreOffice 없음
    assert make_pdf(DATA).startswith(b"%PDF")
    monkeypatch.setattr(settings, "PDF_ENGINE", "libreoffice")
    with pytest.raises(PdfUnavailableError):
        make_pdf(DATA)


def test_clear_message_when_no_font(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "PDF_ENGINE", "builtin")
    monkeypatch.setattr(notice_pdf, "FONT_CANDIDATES", ())
    with pytest.raises(PdfUnavailableError) as e:
        make_pdf(DATA)
    assert "한글 글꼴" in str(e.value)
