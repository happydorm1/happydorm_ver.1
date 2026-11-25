"""
안내문 Excel/PDF 생성 — 첨부2 양식(6개 항목)을 그대로 재현한다.
안내문은 '발송 당시 스냅샷'(GeneratedDocument.generated_data)에서 만든다. 그래서 같은 안내문을 몇 번을
내려받아도 항상 같고, 이후 원리금 테이블이 바뀌어도 이미 발송한 안내문 내용이 변하지 않는다.
원본과 같이 파생 칸(이자기간·상환후잔액·원리금합계·합계·검증)은 수식으로 둔다.
"""
import io
import os
import shutil
import subprocess
import tempfile
from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.page import PageMargins

FONT = "맑은 고딕"
MONEY = '_-* #,##0_-;\\-* #,##0_-;_-* "-"_-;_-@_-'
DATE_FMT = "yyyy-mm-dd"
HEADER_FILL = PatternFill("solid", fgColor="D9D9D9")
IPMT_FILL = PatternFill("solid", fgColor="C6E0B4")      # 초록 음영(IPMT 적용 융자 건)
TOTAL_FILL = PatternFill("solid", fgColor="92D050")     # 합계행(원본과 동일색)
_THIN = Side(style="thin")
BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)


class PdfUnavailableError(Exception):
    pass


def _cell(ws, ref, value=None, *, bold=False, size=11, h="center", fmt=None, fill=None, border=True, wrap=False):
    c = ws[ref]
    if value is not None:
        c.value = value
    c.font = Font(name=FONT, size=size, bold=bold)
    c.alignment = Alignment(horizontal=h, vertical="center", wrap_text=wrap)
    if fmt:
        c.number_format = fmt
    if fill:
        c.fill = fill
    if border:
        c.border = BORDER
    return c


def _label_cell(ws, r, label, fill):
    """구분 칸 — 융자명과 (차입금 코드)를 두 줄로 나누고 줄 수만큼 행 높이를 늘린다."""
    text = split_label(label)
    _cell(ws, f"B{r}", text, fill=fill, wrap=True)
    lines = text.count("\n") + 1
    if lines > 1:
        ws.row_dimensions[r].height = max(ws.row_dimensions[r].height or 0, 16.5 * lines)


def _merge(ws, rng, **kw):
    ws.merge_cells(rng)
    first = rng.split(":")[0]
    _cell(ws, first, **kw)
    # 병합 영역 테두리
    for row in ws[rng]:
        for c in row:
            c.border = BORDER


def split_label(label: str) -> str:
    """'주택도시기금 (2018-12-28_주택-00)' → 두 줄('주택도시기금' / '(2018-12-28_주택-00)') — 구분 칸에서 잘리지 않게."""
    s = str(label or "")
    return s.replace(" (", "\n(", 1) if " (" in s else s


def text_units(s: str) -> float:
    """글자 폭(엑셀 열 너비 단위 근사) — 한글·전각은 2, 나머지는 1."""
    return sum(2 if ord(c) > 0x2E7F else 1 for c in s)


def label_col_width(data: dict, base: float = 17.4) -> float:
    """구분 칸 너비 — 가장 긴 줄이 들어가도록(최소는 원래 양식 너비)."""
    lines = [ln for key in ("interest_periods", "balances", "payments") for x in data.get(key, [])
             for ln in split_label(x.get("label", "")).split("\n")]
    return max(base, max((text_units(ln) for ln in lines), default=0) * 1.05 + 2)


def build_notice_workbook(data: dict) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    fill_notice_sheet(ws, data)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def fill_notice_sheet(ws, data: dict) -> None:
    """안내문 1건을 시트 하나에 그린다(개별 내려받기·일괄 내려받기 공용). 수식은 같은 시트 안만 참조한다."""
    n = len(data["payments"])

    ws.column_dimensions["A"].width = 1.6
    ws.column_dimensions["B"].width = label_col_width(data)
    ws.column_dimensions["C"].width = 19.5
    ws.column_dimensions["D"].width = 16.5
    ws.column_dimensions["E"].width = 16.5
    ws.column_dimensions["F"].width = 16.5
    ws.column_dimensions["G"].width = 18.0
    ws.column_dimensions["H"].width = 2.0

    # 제목
    ws.row_dimensions[3].height = 26.25
    _merge(ws, "B3:G3", value=data["title"], bold=True, size=16)

    # 1. 납부기한
    _cell(ws, "B5", "1. 납부기한", bold=True, size=12, h="left", border=False)
    _cell(ws, "C5", date.fromisoformat(data["payment_deadline"]), fmt=DATE_FMT, border=False)

    # 2. 입금계좌
    _cell(ws, "B7", "2. 입금계좌", bold=True, size=12, h="left", border=False)
    _merge(ws, "B8:F8", value="계좌번호", fill=HEADER_FILL)
    _cell(ws, "G8", "예금주", fill=HEADER_FILL)
    _merge(ws, "B9:F9", value=data["account"]["bank_account"])
    _cell(ws, "G9", data["account"]["account_holder"])

    # 3. 이자기간
    int_start = 13
    int_end = 12 + n
    _cell(ws, "B11", "3. 이자기간", bold=True, size=12, h="left", border=False)
    for col, title in zip("BCDE", ["구 분", "시작일", "종료일", "이자기간"]):
        _cell(ws, f"{col}12", title, fill=HEADER_FILL)
    _merge(ws, "F12:G12", value="비고", fill=HEADER_FILL)

    bal_sec = int_end + 2
    bal_hdr = bal_sec + 1
    bal_start = bal_hdr + 1
    bal_end = bal_start + n - 1
    pay_sec = bal_end + 2
    pay_hdr = pay_sec + 1
    pay_start = pay_hdr + 1
    pay_end = pay_start + n - 1
    total_row = pay_end + 1
    note_sec = total_row + 2
    note_start = note_sec + 1
    note_end = note_start + 2

    for i, p in enumerate(data["interest_periods"]):
        r = int_start + i
        fill = IPMT_FILL if p["ipmt"] else None
        _label_cell(ws, r, p["label"], fill)
        _cell(ws, f"C{r}", date.fromisoformat(p["start"]), fmt=DATE_FMT, fill=fill)
        _cell(ws, f"D{r}", date.fromisoformat(p["end"]), fmt=DATE_FMT, fill=fill)
        _cell(ws, f"E{r}", f"=D{r}-C{r}", fmt="General", fill=fill)
    _merge(ws, f"F{int_start}:G{int_end}", value=data["interest_periods"][0]["note"], wrap=True)

    # 4. 대출잔액
    _cell(ws, f"B{bal_sec}", "4. 대출잔액", bold=True, size=12, h="left", border=False)
    for col, title in zip("BCDEFG", ["구 분", "융자액", "상환 전 대출잔액", "금번 상환원금", "상환 후 대출잔액", "-"]):
        _cell(ws, f"{col}{bal_hdr}", title, fill=HEADER_FILL)
    for i, b in enumerate(data["balances"]):
        r, pr = bal_start + i, pay_start + i
        fill = IPMT_FILL if b["ipmt"] else None
        _label_cell(ws, r, b["label"], fill)
        _cell(ws, f"C{r}", b["assigned_amount"], fmt=MONEY, fill=fill, h="right")
        _cell(ws, f"D{r}", f"=C{pr}", fmt=MONEY, fill=fill, h="right")
        _cell(ws, f"E{r}", f"=E{pr}", fmt=MONEY, fill=fill, h="right")
        _cell(ws, f"F{r}", f"=D{r}-E{r}", fmt=MONEY, fill=fill, h="right")
        _cell(ws, f"G{r}", f'=IF(C{pr}-(E{pr})=F{r},"TRUE","FALSE")', bold=True, fill=fill)

    # 5. 상환금액
    _cell(ws, f"B{pay_sec}", "5. 상환금액", bold=True, size=12, h="left", border=False)
    ws.row_dimensions[pay_hdr].height = 28.5
    for col, title in zip("BCDEFG", ["구 분", "상환잔액\n(직전 상환일 기준)", "적용이율", "원 금", "이 자", "원리금 합계"]):
        _cell(ws, f"{col}{pay_hdr}", title, fill=HEADER_FILL, wrap=True)
    for i, p in enumerate(data["payments"]):
        r = pay_start + i
        fill = IPMT_FILL if p["ipmt"] else None
        _label_cell(ws, r, p["label"], fill)
        _cell(ws, f"C{r}", p["balance_before"], fmt=MONEY, fill=fill, h="right")
        _cell(ws, f"D{r}", round(p["rate"] / 100, 8), fmt="0.000%", fill=fill)
        _cell(ws, f"E{r}", p["principal"], fmt=MONEY, fill=fill, h="right")
        _cell(ws, f"F{r}", p["interest"], fmt=MONEY, fill=fill, h="right")
        _cell(ws, f"G{r}", f"=E{r}+F{r}", fmt=MONEY, fill=fill, h="right")
    _cell(ws, f"B{total_row}", "합 계", bold=True, fill=TOTAL_FILL)
    _cell(ws, f"C{total_row}", f"=SUM(C{pay_start}:C{pay_end})", bold=True, fmt=MONEY, fill=TOTAL_FILL, h="right")
    _cell(ws, f"D{total_row}", "-", bold=True, fill=TOTAL_FILL)
    for col in "EFG":
        _cell(ws, f"{col}{total_row}", f"=SUM({col}{pay_start}:{col}{pay_end})", bold=True, fmt=MONEY, fill=TOTAL_FILL, h="right")

    # 6. 비고
    _cell(ws, f"B{note_sec}", "6. 비고", bold=True, size=12, h="left", border=False)
    _merge(ws, f"B{note_start}:G{note_end}", value="\n".join(data["notes"]) or None, h="left", wrap=True)

    ws.print_area = f"A1:H{note_end + 1}"
    ws.page_setup.orientation = "portrait"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_margins = PageMargins(left=0.6, right=0.6, top=0.8, bottom=0.8)
    ws.print_options.horizontalCentered = True


_BAD_SHEET_CHARS = str.maketrans({c: " " for c in '[]:*?/\\'})


def _sheet_name(i: int, label: str, used: set[str]) -> str:
    base = f"{i}_{label}".translate(_BAD_SHEET_CHARS).strip()[:31]
    name, k = base, 2
    while name in used:
        tail = f"({k})"
        name, k = base[:31 - len(tail)] + tail, k + 1
    used.add(name)
    return name


SUMMARY_HEAD = ("No", "상환예정일", "납부기한", "기숙사", "학교", "안내문", "납부금액", "발송일시", "수신확인", "입금확인")


def build_notices_workbook(items: list[tuple[dict, dict]], *, summary: bool = True, subtitle: str = "") -> bytes:
    """
    일괄 내려받기(발송완료 조회 결과 전체) — items: [(목록 행, 안내문 스냅샷)].
    summary=True면 첫 시트 '목록'에 조회 결과 표, 이어서 안내문 1건당 시트 1장(개별 내려받기와 같은 양식).
    PDF(LibreOffice) 변환용으로는 summary=False(안내문 시트만).
    """
    wb = Workbook()
    used: set[str] = set()
    first = wb.active
    if summary:
        ws = first
        ws.title = "목록"
        used.add("목록")
        _cell(ws, "A1", "발송완료 안내문 목록", bold=True, size=14, h="left", border=False)
        if subtitle:
            _cell(ws, "A2", subtitle, size=9, h="left", border=False)
        for j, h in enumerate(SUMMARY_HEAD):
            _cell(ws, f"{chr(65 + j)}4", h, bold=True, fill=HEADER_FILL)
        for i, (row, data) in enumerate(items, start=1):
            r = 4 + i
            vals = (i, row["due_date"], row["payment_deadline"], row["dormitory_name"], row.get("school_name") or "",
                    row["title"], row.get("total"), row.get("issued_at") or "", row.get("received_label", ""), row.get("pay_label", ""))
            for j, v in enumerate(vals):
                col = chr(65 + j)
                fmt = MONEY if j == 6 else DATE_FMT if j in (1, 2) else None
                _cell(ws, f"{col}{r}", v, fmt=fmt, h="right" if j == 6 else "left" if j in (3, 4, 5) else "center")
        if items:
            tr = 5 + len(items)
            _cell(ws, f"A{tr}", "합계", bold=True, fill=TOTAL_FILL)
            for col in "BCDEF":
                _cell(ws, f"{col}{tr}", None, fill=TOTAL_FILL)
            ws.merge_cells(f"A{tr}:F{tr}")
            _cell(ws, f"G{tr}", f"=SUM(G5:G{tr - 1})", bold=True, fmt=MONEY, fill=TOTAL_FILL, h="right")
            for col in "HIJ":
                _cell(ws, f"{col}{tr}", None, fill=TOTAL_FILL)
        for col, w in zip("ABCDEFGHIJ", (5, 12, 12, 22, 16, 40, 16, 18, 22, 18)):
            ws.column_dimensions[col].width = w
        ws.freeze_panes = "A5"
        ws.page_setup.orientation = "landscape"
        ws.page_setup.paperSize = ws.PAPERSIZE_A4
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True
    for i, (row, data) in enumerate(items, start=1):
        ws = first if (not summary and i == 1) else wb.create_sheet()
        ws.title = _sheet_name(i, row["dormitory_name"] + "_" + str(row["due_date"]), used)
        fill_notice_sheet(ws, data)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


_DEFAULT_SOFFICE = (
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "/usr/bin/soffice", "/usr/lib/libreoffice/program/soffice", "/opt/libreoffice/program/soffice",
)
INSTALL_HINT = ("서버(이 프로그램이 실행되는 PC)에 LibreOffice를 설치하면 PDF로 받을 수 있습니다. "
                "Windows: libreoffice.org에서 내려받아 설치 후 프로그램 재시작 / "
                "Codespaces·Linux: bash .devcontainer/install_pdf.sh. 그전까지는 Excel로 내려받으세요.")


def find_soffice() -> str | None:
    """LibreOffice 실행파일 찾기: 설정값(LIBREOFFICE_PATH) → PATH → 기본 설치 위치."""
    from app.core.config import settings
    if settings.LIBREOFFICE_PATH:
        return settings.LIBREOFFICE_PATH if Path(settings.LIBREOFFICE_PATH).exists() else None
    found = shutil.which("soffice") or shutil.which("libreoffice")
    if found:
        return found
    return next((p for p in _DEFAULT_SOFFICE if Path(p).exists()), None)


def convert_xlsx_to_pdf(xlsx_bytes: bytes, timeout: int = 120) -> bytes:
    """LibreOffice(headless)로 PDF 변환. 서버에 LibreOffice가 없으면 PdfUnavailableError."""
    soffice = find_soffice()
    if not soffice:
        raise PdfUnavailableError("PDF 변환기(LibreOffice)를 찾지 못했습니다. " + INSTALL_HINT)
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "notice.xlsx"
        src.write_bytes(xlsx_bytes)
        profile = (Path(tmp) / "lo_profile").resolve().as_uri()   # 매번 새 프로필 → 다른 LibreOffice 창·동시 변환과 충돌하지 않음
        env = {**os.environ, "HOME": tmp}
        try:
            subprocess.run(
                [soffice, f"-env:UserInstallation={profile}", "--headless", "--convert-to", "pdf", "--outdir", tmp, str(src)],
                check=True, capture_output=True, timeout=timeout, env=env,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
            raise PdfUnavailableError(f"PDF 변환에 실패했습니다: {e}")
        out = Path(tmp) / "notice.pdf"
        if not out.exists():
            raise PdfUnavailableError("PDF 변환 결과 파일이 없습니다.")
        return out.read_bytes()


def notice_pdf(data: dict, xlsx_bytes: bytes | None = None) -> bytes:
    """
    안내문 PDF 만들기. PDF_ENGINE 설정:
      auto(기본)  — LibreOffice가 있으면 Excel 안내문을 그대로 PDF로 바꾸고, 없거나 실패하면 내장 방식으로 그린다
      builtin     — 항상 내장 방식(Render 무료처럼 메모리·CPU가 작은 서버 권장)
      libreoffice — LibreOffice만 사용(실패하면 오류)
    """
    from app.core.config import settings
    from app.engine.notice_pdf import FontMissing, build_notice_pdf
    engine = (settings.PDF_ENGINE or "auto").lower()
    lo_error = None
    if engine in ("auto", "libreoffice"):
        try:
            return convert_xlsx_to_pdf(xlsx_bytes if xlsx_bytes is not None else build_notice_workbook(data),
                                       timeout=60 if engine == "auto" else 120)
        except PdfUnavailableError as e:
            if engine == "libreoffice":
                raise
            lo_error = e
    try:
        return build_notice_pdf(data, settings.PDF_FONT_PATH)
    except FontMissing as e:
        raise PdfUnavailableError(f"{e} " + (str(lo_error) if lo_error else INSTALL_HINT))


def notices_pdf(items: list[tuple[dict, dict]]) -> bytes:
    """일괄 PDF — 안내문 1건이 1페이지(길면 이어서)씩 한 파일에 이어진다. 엔진 선택 규칙은 notice_pdf와 같다."""
    from app.core.config import settings
    from app.engine.notice_pdf import FontMissing, build_notices_pdf
    engine = (settings.PDF_ENGINE or "auto").lower()
    lo_error = None
    if engine in ("auto", "libreoffice"):
        try:   # 시트마다 '한 페이지에 맞춤' 인쇄 설정 → 시트 순서대로 페이지가 된다
            return convert_xlsx_to_pdf(build_notices_workbook(items, summary=False),
                                       timeout=(60 if engine == "auto" else 120) + 2 * len(items))
        except PdfUnavailableError as e:
            if engine == "libreoffice":
                raise
            lo_error = e
    try:
        return build_notices_pdf([d for _, d in items], settings.PDF_FONT_PATH)
    except FontMissing as e:
        raise PdfUnavailableError(f"{e} " + (str(lo_error) if lo_error else INSTALL_HINT))
