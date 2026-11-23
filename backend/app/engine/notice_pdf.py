"""
안내공문 PDF — LibreOffice 없이 Python(fpdf2)만으로 그리는 내장 방식(2026-11-23).
LibreOffice가 없거나(Windows 노트북·새 Codespace) 메모리·CPU가 작은 서버(Render 무료)에서 변환이 실패할 때 쓴다.
Excel 공문(notice_export.build_notice_workbook)과 같은 6개 항목·열 비율·음영(머리 회색, IPMT 연두, 합계 초록)을 그대로 따른다.
값은 발급 당시 스냅샷(generated_data)에서 읽으므로 몇 번을 내려받아도 같다.
"""
from datetime import date
from pathlib import Path

from fpdf import FPDF

# 한글 글꼴(일반, 굵게) — 서버마다 있는 것을 쓴다
FONT_CANDIDATES = (
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),  # Render·Codespaces
    ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"),
    (r"C:\Windows\Fonts\malgun.ttf", r"C:\Windows\Fonts\malgunbd.ttf"),                                                  # Windows(맑은 고딕)
    ("/System/Library/Fonts/AppleSDGothicNeo.ttc", "/System/Library/Fonts/AppleSDGothicNeo.ttc"),                     # Mac
)

HEADER = (217, 217, 217)   # D9D9D9
IPMT = (198, 224, 180)     # C6E0B4
TOTAL = (146, 208, 80)     # 92D050
# Excel 열 너비 B~G(17.4, 19.5, 16.5, 16.5, 16.5, 18.0)를 A4 본문 폭에 맞춰 줄인 값(mm)
_RATIO = (17.4, 19.5, 16.5, 16.5, 16.5, 18.0)


class FontMissing(Exception):
    pass


def _fonts(extra: str = "") -> tuple[str, str]:
    cands = ([(extra, extra)] if extra else []) + list(FONT_CANDIDATES)
    for reg, bold in cands:
        if reg and Path(reg).exists():
            return reg, (bold if bold and Path(bold).exists() else reg)
    raise FontMissing("PDF에 쓸 한글 글꼴을 찾지 못했습니다(Noto Sans CJK·나눔고딕·맑은 고딕).")


def _won(v) -> str:
    if v is None:
        return ""
    v = int(v)
    return "-" if v == 0 else f"{v:,}"


class _Doc(FPDF):
    def __init__(self, reg: str, bold: str):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.set_margins(15, 18, 15)
        self.set_auto_page_break(True, 15)
        self.add_font("K", "", reg)
        self.add_font("K", "B", bold)
        self.cols = [w * (210 - 30) / sum(_RATIO) for w in _RATIO]

    def section(self, text: str):
        self.ln(3)
        self.set_font("K", "B", 11)
        self.cell(0, 7, text, new_x="LMARGIN", new_y="NEXT")

    def row(self, cells: list[tuple], h: float = 7.0, size: float = 9):
        """cells: (열 개수, 글자, 정렬 L/C/R, 채움색 또는 None, 굵게). 여러 줄 글자는 높이를 늘린다."""
        lh = size * 0.45
        need = h
        for span, text, _a, _f, b in cells:
            self.set_font("K", "B" if b else "", size)
            w = sum(self.cols[:span]) if span else 0
            lines = self.multi_cell(w - 2, lh, str(text), dry_run=True, output="LINES") if text else [""]
            need = max(need, len(lines) * lh + 2.5)
        if self.get_y() + need > self.h - self.b_margin:
            self.add_page()
        x0, y0, i = self.l_margin, self.get_y(), 0
        for span, text, align, fill, b in cells:
            w = sum(self.cols[i:i + span]); i += span
            if fill:
                self.set_fill_color(*fill)
            self.rect(x0, y0, w, need, style="DF" if fill else "D")
            self.set_font("K", "B" if b else "", size)
            lines = self.multi_cell(w - 2, lh, str(text), dry_run=True, output="LINES") if text else [""]
            self.set_xy(x0 + 1, y0 + (need - len(lines) * lh) / 2)
            self.multi_cell(w - 2, lh, str(text), align=align)
            x0 += w
        self.set_xy(self.l_margin, y0 + need)


def build_notice_pdf(data: dict, font_path: str = "") -> bytes:
    reg, bold = _fonts(font_path)
    pdf = _Doc(reg, bold)
    pdf.set_title(data.get("title", "안내공문"))
    pdf.add_page()
    n = len(data["payments"])

    # 제목(테두리 상자)
    pdf.set_font("K", "B", 15)
    pdf.set_line_width(0.3)
    pdf.cell(0, 12, data["title"], border=1, align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_line_width(0.2)

    # 1. 납부기한
    pdf.ln(2)
    pdf.set_font("K", "B", 11)
    pdf.cell(pdf.cols[0], 8, "1. 납부기한")
    pdf.set_font("K", "", 10)
    pdf.cell(0, 8, data["payment_deadline"], new_x="LMARGIN", new_y="NEXT")

    # 2. 입금계좌
    pdf.section("2. 입금계좌")
    pdf.row([(5, "계좌번호", "C", HEADER, False), (1, "예금주", "C", HEADER, False)])
    pdf.row([(5, data["account"]["bank_account"], "C", None, False), (1, data["account"]["account_holder"], "C", None, False)])

    # 3. 이자기간 — 비고는 여러 줄을 합친 한 칸(Excel 병합과 같게 첫 줄에만 쓰고 아래 줄은 비움)
    pdf.section("3. 이자기간")
    pdf.row([(1, "구 분", "C", HEADER, False), (1, "시작일", "C", HEADER, False), (1, "종료일", "C", HEADER, False),
             (1, "이자기간", "C", HEADER, False), (2, "비고", "C", HEADER, False)])
    periods = data["interest_periods"]
    note = periods[0]["note"] if periods else ""
    y_top = pdf.get_y()
    for p in periods:
        f = IPMT if p["ipmt"] else None
        days = (date.fromisoformat(p["end"]) - date.fromisoformat(p["start"])).days
        pdf.row([(1, p["label"], "C", f, False), (1, p["start"], "C", f, False), (1, p["end"], "C", f, False),
                 (1, str(days), "C", f, False)])
    # 비고 병합 칸
    x = pdf.l_margin + sum(pdf.cols[:4]); w = sum(pdf.cols[4:6]); hh = pdf.get_y() - y_top
    pdf.rect(x, y_top, w, hh)
    pdf.set_font("K", "", 9)
    pdf.set_xy(x + 1, y_top + max(0, hh / 2 - 2.5))
    pdf.multi_cell(w - 2, 4.5, note, align="C")
    pdf.set_xy(pdf.l_margin, y_top + hh)

    # 4. 대출잔액
    pdf.section("4. 대출잔액")
    pdf.row([(1, t, "C", HEADER, False) for t in ("구 분", "융자액", "상환 전 대출잔액", "금번 상환원금", "상환 후 대출잔액", "-")])
    pays = data["payments"]
    for i, b in enumerate(data["balances"]):
        p = pays[i] if i < len(pays) else {"balance_before": b["balance_before"], "principal": b["principal"]}
        f = IPMT if b["ipmt"] else None
        before, prin = p["balance_before"], p["principal"]
        after = before - prin
        pdf.row([(1, b["label"], "C", f, False), (1, _won(b["assigned_amount"]), "R", f, False), (1, _won(before), "R", f, False),
                 (1, _won(prin), "R", f, False), (1, _won(after), "R", f, False),
                 (1, "TRUE" if before - prin == after else "FALSE", "C", f, True)])

    # 5. 상환금액
    pdf.section("5. 상환금액")
    pdf.row([(1, t, "C", HEADER, False) for t in ("구 분", "상환잔액\n(직전 상환일 기준)", "적용이율", "원 금", "이 자", "원리금 합계")])
    tot = {"c": 0, "e": 0, "f": 0}
    for p in pays:
        f = IPMT if p["ipmt"] else None
        tot["c"] += p["balance_before"]; tot["e"] += p["principal"]; tot["f"] += p["interest"]
        pdf.row([(1, p["label"], "C", f, False), (1, _won(p["balance_before"]), "R", f, False), (1, f"{p['rate']:.3f}%", "C", f, False),
                 (1, _won(p["principal"]), "R", f, False), (1, _won(p["interest"]), "R", f, False),
                 (1, _won(p["principal"] + p["interest"]), "R", f, False)])
    pdf.row([(1, "합 계", "C", TOTAL, True), (1, _won(tot["c"]), "R", TOTAL, True), (1, "-", "C", TOTAL, True),
             (1, _won(tot["e"]), "R", TOTAL, True), (1, _won(tot["f"]), "R", TOTAL, True), (1, _won(tot["e"] + tot["f"]), "R", TOTAL, True)])

    # 6. 비고
    pdf.section("6. 비고")
    pdf.row([(6, "\n".join(data.get("notes") or []), "L", None, False)], h=18)
    _ = n
    return bytes(pdf.output())
