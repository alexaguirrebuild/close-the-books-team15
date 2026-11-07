"""CSV advice: a key/value line, a fixed header, the documents and a TOTAL row. Separator `,` or `;`."""
from __future__ import annotations

import csv
import io

from .common import Advice, AdviceLine, clean, nonzero, parse_amount, parse_date

EXPECTED_HEADER = ["document", "doc_date", "gross", "discount_or_deduction", "reason", "paid", "your_order"]
TOTAL_LABEL = "TOTAL"


def _delimiter(first_line: str) -> str:
    return ";" if first_line.count(";") >= first_line.count(",") else ","


def _metadata(row: list[str]) -> dict[str, str]:
    return {row[index].strip().lower(): row[index + 1].strip() for index in range(0, len(row) - 1, 2)}


def _line(row: list[str]) -> AdviceLine:
    document, doc_date, gross, deduction, reason, paid, order = (*row, *[""] * len(EXPECTED_HEADER))[:len(EXPECTED_HEADER)]
    return AdviceLine(
        document=document.strip(),
        doc_date=parse_date(doc_date),
        gross=parse_amount(gross),
        deduction=nonzero(parse_amount(deduction)),
        reason=clean(reason),
        paid=parse_amount(paid),
        your_order=clean(order),
        voucher=None,
    )


def parse_csv_advice(text: str) -> Advice:
    source = text.lstrip("﻿")
    first_line = source.split("\n", 1)[0]
    rows = [row for row in csv.reader(io.StringIO(source), delimiter=_delimiter(first_line)) if any(cell.strip() for cell in row)]
    if len(rows) < 2 or [cell.strip().lower() for cell in rows[1]] != EXPECTED_HEADER:
        raise ValueError(f"cabecera de aviso CSV desconocida: {rows[1] if len(rows) > 1 else 'sin cabecera'}")
    meta = _metadata(rows[0])
    body = rows[2:]
    totals = [row for row in body if row[0].strip().upper() == TOTAL_LABEL]
    lines = tuple(_line(row) for row in body if row[0].strip().upper() != TOTAL_LABEL)
    control = parse_amount(totals[-1][5]) if totals and len(totals[-1]) > 5 else None
    return Advice(
        payment_ref=meta.get("payment_ref"),
        payment_date=parse_date(meta.get("payment_date")),
        currency=meta.get("currency"),
        control_amount=control,
        layout="csv",
        lines=lines,
    )
