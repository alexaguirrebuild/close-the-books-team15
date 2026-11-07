"""Shape and number/date reading shared by the advice parsers."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

CONTROL_TOLERANCE = Decimal("0.01")
MAX_STORABLE_AMOUNT = Decimal(10) ** 16  # The tables use DECIMAL(18,2).
THOUSANDS_DIGITS = 3
TWO_DIGIT_YEAR_BASE = 2000
NUMERIC_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})$")
ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


@dataclass(frozen=True, slots=True)
class AdviceLine:
    document: str
    doc_date: str | None
    gross: Decimal | None
    deduction: Decimal | None
    reason: str | None
    paid: Decimal | None
    your_order: str | None
    voucher: str | None


@dataclass(frozen=True, slots=True)
class Advice:
    payment_ref: str | None
    payment_date: str | None
    currency: str | None
    control_amount: Decimal | None
    layout: str
    lines: tuple[AdviceLine, ...]
    problems: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ControlResult:
    status: str
    lines_sum: Decimal | None
    repeated: frozenset[int]


def _plain_number(text: str) -> str:
    """Whichever separator comes last is the decimal one; a lone comma before 3 digits groups thousands."""
    last_comma, last_dot = text.rfind(","), text.rfind(".")
    if last_comma >= 0 and last_dot >= 0:
        decimal_mark, group_mark = (",", ".") if last_comma > last_dot else (".", ",")
        return text.replace(group_mark, "").replace(decimal_mark, ".")
    if last_comma >= 0:
        digits_after = len(text) - last_comma - 1
        if digits_after == THOUSANDS_DIGITS or text.count(",") > 1:
            return text.replace(",", "")
        return text.replace(",", ".")
    if text.count(".") > 1:
        return text.replace(".", "")
    return text


def parse_amount(value: str | int | float | Decimal | None) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = value.strip()
    if not text:
        return None
    try:
        return Decimal(_plain_number(text))
    except InvalidOperation:
        return None


def check_amounts_storable(advice: Advice) -> None:
    """NaN or 1E+30 parse fine and would only blow up later, when the whole table is loaded."""
    amounts = (advice.control_amount, *(value for line in advice.lines for value in (line.gross, line.deduction, line.paid)))
    for amount in amounts:
        if amount is not None and not (amount.is_finite() and abs(amount) < MAX_STORABLE_AMOUNT):
            raise ValueError(f"importe que no se puede guardar: {amount}")


def _valid_date(year: int, month: int, day: int) -> str | None:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def parse_date(value: str | date | datetime | None, month_first: bool = False) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = (value or "").strip()
    iso = ISO_DATE.match(text)
    if iso:
        return _valid_date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
    numeric = NUMERIC_DATE.match(text)
    if numeric is None:
        return None
    first, second, year_text = int(numeric.group(1)), int(numeric.group(2)), numeric.group(3)
    year = int(year_text) + (TWO_DIGIT_YEAR_BASE if len(year_text) == 2 else 0)
    orders = ((second, first), (first, second)) if not month_first else ((first, second), (second, first))
    for month, day in orders:
        found = _valid_date(year, month, day)
        if found:
            return found
    return None


def clean(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def nonzero(value: Decimal | None) -> Decimal | None:
    """Layouts write no deduction as blank or as 0: both mean the same."""
    return None if value is None or value == 0 else value


def _repeated_positions(lines: tuple[AdviceLine, ...]) -> frozenset[int]:
    seen: set[tuple] = set()
    repeated: set[int] = set()
    for position, line in enumerate(lines):
        key = (line.document, line.gross, line.paid)
        if key in seen:
            repeated.add(position)
        seen.add(key)
    return frozenset(repeated)


def check_control(advice: Advice) -> ControlResult:
    """Does the table add up to what the advice announces? A repeated line only counts as noise if removing it makes it add up."""
    if not advice.lines:
        return ControlResult("no_lines", None, frozenset())
    repeated = _repeated_positions(advice.lines)
    paid = [line.paid or Decimal(0) for line in advice.lines]
    total = sum(paid, Decimal(0))
    if advice.control_amount is None:
        return ControlResult("no_control", total, repeated)
    if abs(total - advice.control_amount) <= CONTROL_TOLERANCE:
        return ControlResult("ok", total, frozenset())
    unique_total = sum((amount for position, amount in enumerate(paid) if position not in repeated), Decimal(0))
    if repeated and abs(unique_total - advice.control_amount) <= CONTROL_TOLERANCE:
        return ControlResult("ok_without_repeats", unique_total, repeated)
    return ControlResult("sum_mismatch", total, repeated)
