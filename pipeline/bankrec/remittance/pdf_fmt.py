"""PDF advice: English or Spanish template, read from the embedded text layer."""
from __future__ import annotations

import re
from pathlib import Path

import pypdfium2

from .common import Advice, AdviceLine, clean, nonzero, parse_amount, parse_date

PAYMENT_REF = re.compile(r"^(?:Payment ref\.|Ref\.)\s+(.+?)\s*$", re.MULTILINE)
PAYMENT_DATE = re.compile(r"^(?:Date|Fecha)\s+(\d{2}/\d{2}/\d{4})\s*$", re.MULTILINE)
TOTAL = re.compile(r"^(?:Amount|Importe)\s+(-?[\d.,]+)\s+([A-Z]{3})\s*$", re.MULTILINE)
TABLE_HEADERS = ("Document Date Gross Deduction Paid", "Documento Fecha Bruto Dto./ded. Pagado")
AMOUNT = r"-?\d[\d.,]*"
# A narrow column can glue a negative deduction to the gross amount: "26.425,09-2.642,50".
TABLE_LINE = re.compile(rf"^(\S+)\s+(\d{{2}}/\d{{2}}/\d{{2,4}})\s+({AMOUNT})(?:(?:\s*(?=-)|\s+)({AMOUNT}))?(?:\s+(\D.*?))?\s+({AMOUNT})$")
FOOTER_MARK = "Documento sintético"


def _line(match: re.Match[str]) -> AdviceLine:
    return AdviceLine(
        document=match.group(1),
        doc_date=parse_date(match.group(2)),
        gross=parse_amount(match.group(3)),
        deduction=nonzero(parse_amount(match.group(4))),
        reason=clean(match.group(5)),
        paid=parse_amount(match.group(6)),
        your_order=None,
        voucher=None,
    )


def _table_rows(rows: list[str]) -> list[tuple[int, str]]:
    start = next((index for index, row in enumerate(rows) if row in TABLE_HEADERS), None)
    if start is None:
        raise ValueError("aviso PDF sin tabla reconocible")
    return [(index + 1, row) for index, row in enumerate(rows) if index > start and row not in TABLE_HEADERS and not row.startswith(FOOTER_MARK)]


def parse_pdf_text(text: str) -> Advice:
    source = text.replace("\r", "")
    rows = [row.strip() for row in source.split("\n") if row.strip()]
    lines: list[AdviceLine] = []
    problems: list[str] = []
    for number, row in _table_rows(rows):
        match = TABLE_LINE.match(row)
        if match:
            lines.append(_line(match))
        else:
            problems.append(f"fila {number} sin leer: {row[:60]}")
    reference = PAYMENT_REF.search(source)
    payment_date = PAYMENT_DATE.search(source)
    total = TOTAL.search(source)
    return Advice(
        payment_ref=reference.group(1) if reference else None,
        payment_date=parse_date(payment_date.group(1)) if payment_date else None,
        currency=total.group(2) if total else None,
        control_amount=parse_amount(total.group(1)) if total else None,
        layout="pdf",
        lines=tuple(lines),
        problems=tuple(problems),
    )


def read_pdf_text(path: Path) -> str:
    document = pypdfium2.PdfDocument(path)
    try:
        return "\n".join(document[index].get_textpage().get_text_bounded() for index in range(len(document)))
    finally:
        document.close()


def parse_pdf_advice(path: Path) -> Advice:
    return parse_pdf_text(read_pdf_text(path))
