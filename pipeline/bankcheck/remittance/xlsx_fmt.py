"""XLSX advice. There are two layouts: per document, and the customer-voucher one with our invoice number and PO."""
from __future__ import annotations

from pathlib import Path

import openpyxl

from .common import Advice, AdviceLine, clean, nonzero, parse_amount, parse_date

HEADER_ROW, DOCUMENT_LAYOUT_COLUMNS = 2, ("Document", "Date", "Gross", "Deduction", "Reason", "Paid")
VOUCHER_LAYOUT_COLUMNS = ("Voucher", "Invoice", "PO", "Inv. date", "Amount", "Deduction", "Net paid")
TOTAL_LABEL = "TOTAL"


def _document_line(row: tuple) -> AdviceLine:
    document, doc_date, gross, deduction, reason, paid = row[:6]
    return AdviceLine(
        document=str(document).strip(),
        doc_date=parse_date(doc_date),
        gross=parse_amount(gross),
        deduction=nonzero(parse_amount(deduction)),
        reason=clean(reason),
        paid=parse_amount(paid),
        your_order=None,
        voucher=None,
    )


def _voucher_line(row: tuple) -> AdviceLine:
    voucher, invoice, order, doc_date, amount, deduction, paid = row[:7]
    return AdviceLine(
        document=str(invoice).strip(),
        doc_date=parse_date(doc_date),
        gross=parse_amount(amount),
        deduction=nonzero(parse_amount(deduction)),
        reason=None,
        paid=parse_amount(paid),
        your_order=clean(order),
        voucher=clean(voucher),
    )


LAYOUTS = {
    DOCUMENT_LAYOUT_COLUMNS: ("xlsx_document", _document_line),
    VOUCHER_LAYOUT_COLUMNS: ("xlsx_voucher", _voucher_line),
}


def _read_rows(path: Path) -> list[tuple]:
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        return list(workbook.worksheets[0].iter_rows(values_only=True))
    finally:
        workbook.close()


def _total_of(row: tuple):
    return next((parse_amount(cell) for cell in row[1:] if isinstance(cell, (int, float))), None)


def parse_xlsx_advice(path: Path) -> Advice:
    rows = _read_rows(path)
    header = tuple(cell for cell in rows[HEADER_ROW] if cell is not None) if len(rows) > HEADER_ROW else ()
    if header not in LAYOUTS:
        raise ValueError(f"cabecera de aviso XLSX desconocida: {header}")
    layout, read_line = LAYOUTS[header]
    meta = rows[1]
    body = [row for row in rows[HEADER_ROW + 1:] if row and row[0] is not None]
    totals = [row for row in body if str(row[0]).strip().upper() == TOTAL_LABEL]
    return Advice(
        payment_ref=clean(meta[1]),
        payment_date=parse_date(meta[2]),
        currency=None,
        control_amount=_total_of(totals[-1]) if totals else None,
        layout=layout,
        lines=tuple(read_line(row) for row in body if str(row[0]).strip().upper() != TOTAL_LABEL),
    )
