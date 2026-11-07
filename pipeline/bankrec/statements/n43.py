"""Norma 43 (AEB cuaderno 43): fixed 80-byte records, latin-1, CRLF."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal

from .model import ParsedStatement, RawLine, control_from

FIELD_WIDTH = 38
MOVEMENT_MIN_LENGTH = 42
CURRENCY_CODES = {
    "036": "AUD", "124": "CAD", "156": "CNY", "484": "MXN", "504": "MAD", "710": "ZAR",
    "756": "CHF", "826": "GBP", "840": "USD", "978": "EUR", "985": "PLN", "986": "BRL",
}
CREDIT_MARK = "2"


def _iso(yymmdd: str) -> str:
    return f"20{yymmdd[:2]}-{yymmdd[2:4]}-{yymmdd[4:6]}"


def _signed(mark: str, digits: str) -> int:
    return int(digits) if mark == CREDIT_MARK else -int(digits)


@dataclass
class _Reader:
    """Mutable while one file is being walked; the parsed result is immutable."""

    currency: str | None = None
    opening: int | None = None
    closing: int | None = None
    declared: dict[str, int] = field(default_factory=lambda: {"debits": 0, "credits": 0, "debit_cents": 0, "credit_cents": 0})
    trailers: int = 0
    lines: list[RawLine] = field(default_factory=list)
    fields: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def close_movement(self) -> None:
        if self.lines and self.fields:
            self.lines[-1] = replace(self.lines[-1], text=" | ".join(self.fields))
        self.fields = []


def _read_header(reader: _Reader, record: str) -> None:
    reader.currency = CURRENCY_CODES.get(record[47:50], record[47:50])
    if reader.opening is None:
        reader.opening = _signed(record[32], record[33:47])


def _read_movement(reader: _Reader, record: str, number: int) -> None:
    if len(record) < MOVEMENT_MIN_LENGTH:
        reader.problems.append(f"línea {number}: movimiento cortado ({len(record)} caracteres)")
        return
    reader.lines.append(
        RawLine(
            src_line=number,
            booking_date=_iso(record[10:16]),
            value_date=_iso(record[16:22]),
            amount_cents=_signed(record[27], record[28:42]),
            currency=reader.currency,
            bank_code=record[22:24],
            bank_subcode=record[24:27],
            ref1=record[52:64].strip(),
            ref2=record[64:80].strip(),
        )
    )


def _read_concept(reader: _Reader, record: str) -> None:
    body = record[4:]
    pieces = (body[:FIELD_WIDTH].rstrip(), body[FIELD_WIDTH:FIELD_WIDTH * 2].rstrip())
    reader.fields = [*reader.fields, *(piece for piece in pieces if piece)]


def _read_equivalence(reader: _Reader, record: str) -> None:
    if not reader.lines:
        return
    code = record[4:7]
    reader.lines[-1] = replace(
        reader.lines[-1],
        orig_currency=CURRENCY_CODES.get(code, code),
        orig_amount=Decimal(int(record[7:21])).scaleb(-2),
    )


def _read_trailer(reader: _Reader, record: str) -> None:
    reader.trailers += 1
    reader.declared = {
        "debits": reader.declared["debits"] + int(record[20:25]),
        "debit_cents": reader.declared["debit_cents"] + int(record[25:39]),
        "credits": reader.declared["credits"] + int(record[39:44]),
        "credit_cents": reader.declared["credit_cents"] + int(record[44:58]),
    }
    reader.closing = _signed(record[58], record[59:73])


def parse_n43(data: bytes) -> ParsedStatement:
    reader = _Reader()
    for number, raw in enumerate(data.decode("latin-1").splitlines(), start=1):
        kind = raw[:2]
        if kind == "22":
            reader.close_movement()
            _read_movement(reader, raw, number)
        elif kind == "23":
            _read_concept(reader, raw)
        elif kind == "24":
            _read_equivalence(reader, raw)
        elif kind == "11":
            _read_header(reader, raw)
        elif kind == "33":
            reader.close_movement()
            _read_trailer(reader, raw)
        elif kind != "88" and raw.strip():
            reader.problems.append(f"línea {number}: registro desconocido '{kind}'")
    reader.close_movement()
    declared = reader.declared if reader.trailers else {}
    control = control_from(
        reader.lines,
        declared_debits=declared.get("debits"),
        declared_credits=declared.get("credits"),
        declared_debit_cents=declared.get("debit_cents"),
        declared_credit_cents=declared.get("credit_cents"),
        opening_cents=reader.opening,
        closing_cents=reader.closing,
    )
    return ParsedStatement("n43", tuple(reader.lines), control, tuple(reader.problems))
