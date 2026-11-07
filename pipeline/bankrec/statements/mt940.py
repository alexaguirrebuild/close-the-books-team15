"""SWIFT MT940: `:61:` carries the movement and the `:86:` lines that follow carry its text."""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal

from .model import ParsedStatement, RawLine, control_from, to_cents

MOVEMENT = re.compile(r"^:61:(\d{6})(\d{4})?(RC|RD|C|D)[A-Z]?([\d,]+)([NFS][A-Z0-9]{3})([^/]*)(?://(.*))?$")
BALANCE = re.compile(r"^:6[02][FM]:([CD])\d{6}([A-Z]{3})([\d,]+)$")
ORIGINAL = re.compile(r"/OCMT/([A-Z]{3})([\d,]+)")
FIELD_TAG = re.compile(r"^:\d{2}[A-Z]?:")
BLOCK_END = "-}"
JANUARY, DECEMBER = 1, 12


def _decimal(text: str) -> Decimal:
    return Decimal(text.replace(",", "."))


def _value_date(yymmdd: str) -> date:
    return date(2000 + int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:6]))


def _booking_date(value: date, mmdd: str | None) -> date:
    """The entry date has no year: an entry in December valued in January belongs to the year before."""
    if not mmdd:
        return value
    month, day = int(mmdd[:2]), int(mmdd[2:])
    if month == DECEMBER and value.month == JANUARY:
        return date(value.year - 1, month, day)
    if month == JANUARY and value.month == DECEMBER:
        return date(value.year + 1, month, day)
    return date(value.year, month, day)


def _balance_cents(match: re.Match[str]) -> int:
    cents = to_cents(_decimal(match.group(3)))
    return cents if match.group(1) == "C" else -cents


@dataclass
class _Reader:
    currency: str | None = None
    opening: int | None = None
    closing: int | None = None
    lines: list[RawLine] = field(default_factory=list)
    text: list[str] = field(default_factory=list)
    in_text: bool = False
    problems: list[str] = field(default_factory=list)

    def close_movement(self) -> None:
        if self.lines and self.text:
            self.lines[-1] = _with_text(self.lines[-1], "".join(self.text))
        self.text = []
        self.in_text = False


def _with_text(line: RawLine, text: str) -> RawLine:
    original = ORIGINAL.search(text)
    if original is None:
        return replace(line, text=text)
    return replace(line, text=text, orig_currency=original.group(1), orig_amount=_decimal(original.group(2)))


def _read_movement(reader: _Reader, match: re.Match[str], number: int) -> None:
    value = _value_date(match.group(1))
    cents = to_cents(_decimal(match.group(4)))
    credit = match.group(3).endswith("C")  # In this data RC is a credit and RD a debit: the last letter is the sign.
    reader.lines.append(
        RawLine(
            src_line=number,
            booking_date=_booking_date(value, match.group(2)).isoformat(),
            value_date=value.isoformat(),
            amount_cents=cents if credit else -cents,
            currency=reader.currency,
            bank_code=match.group(5),
            ref1=match.group(6).strip(),
            ref2=(match.group(7) or "").strip(),
        )
    )


def _read_balance(reader: _Reader, raw: str) -> None:
    match = BALANCE.match(raw)
    if match is None:
        reader.problems.append(f"saldo ilegible: {raw[:40]}")
        return
    reader.currency = match.group(2)
    if raw.startswith(":60") and reader.opening is None:
        reader.opening = _balance_cents(match)
    if raw.startswith(":62"):
        reader.closing = _balance_cents(match)


def _read_line(reader: _Reader, raw: str, number: int) -> None:
    if raw.startswith(":61:"):
        reader.close_movement()
        match = MOVEMENT.match(raw)
        if match is None:
            reader.problems.append(f"línea {number}: movimiento ilegible: {raw[:60]}")
            return
        _read_movement(reader, match, number)
    elif raw.startswith(":86:"):
        reader.in_text = True
        reader.text = [raw[4:]]
    elif raw.startswith((":60", ":62")):
        reader.close_movement()
        _read_balance(reader, raw)
    elif FIELD_TAG.match(raw) or raw.startswith("{") or raw.strip() == BLOCK_END:
        reader.close_movement()
    elif reader.in_text:
        reader.text = [*reader.text, raw]
    elif raw.strip():
        reader.problems.append(f"línea {number}: contenido inesperado: {raw[:40]}")


def parse_mt940(data: bytes) -> ParsedStatement:
    reader = _Reader()
    for number, raw in enumerate(data.decode("utf-8").splitlines(), start=1):
        _read_line(reader, raw, number)
    reader.close_movement()
    control = control_from(reader.lines, opening_cents=reader.opening, closing_cents=reader.closing)
    return ParsedStatement("mt940", tuple(reader.lines), control, tuple(reader.problems))
