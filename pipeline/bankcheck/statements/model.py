"""Shape shared by the three statement parsers."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

CENTS = 100


def to_cents(amount: Decimal) -> int:
    return int((amount * CENTS).to_integral_value())


@dataclass(frozen=True, slots=True)
class RawLine:
    src_line: int
    booking_date: str
    value_date: str | None
    amount_cents: int
    currency: str | None
    bank_code: str | None = None
    bank_subcode: str | None = None
    ref1: str = ""
    ref2: str = ""
    text: str = ""
    counterparty_name: str | None = None
    counterparty_account: str | None = None
    end_to_end_id: str | None = None
    remittance_info: str | None = None
    orig_amount: Decimal | None = None
    orig_currency: str | None = None


@dataclass(frozen=True, slots=True)
class Control:
    """Figures the file declares about itself, against what was actually read."""

    counted_debits: int
    counted_credits: int
    counted_debit_cents: int
    counted_credit_cents: int
    declared_debits: int | None = None
    declared_credits: int | None = None
    declared_debit_cents: int | None = None
    declared_credit_cents: int | None = None
    opening_cents: int | None = None
    closing_cents: int | None = None

    def problems(self) -> list[str]:
        checks = (
            ("cargos", self.declared_debits, self.counted_debits),
            ("abonos", self.declared_credits, self.counted_credits),
            ("importe de cargos", self.declared_debit_cents, self.counted_debit_cents),
            ("importe de abonos", self.declared_credit_cents, self.counted_credit_cents),
        )
        found = [f"{label}: declara {declared}, leído {counted}" for label, declared, counted in checks if declared is not None and declared != counted]
        if self.opening_cents is not None and self.closing_cents is not None:
            expected = self.opening_cents + self.counted_credit_cents - self.counted_debit_cents
            if expected != self.closing_cents:
                found.append(f"saldo final: declara {self.closing_cents}, calculado {expected}")
        return found


@dataclass(frozen=True, slots=True)
class ParsedStatement:
    format: str
    lines: tuple[RawLine, ...]
    control: Control
    problems: tuple[str, ...] = ()


def control_from(lines: list[RawLine], **declared: int | None) -> Control:
    debits = [line.amount_cents for line in lines if line.amount_cents < 0]
    credits = [line.amount_cents for line in lines if line.amount_cents >= 0]
    return Control(
        counted_debits=len(debits),
        counted_credits=len(credits),
        counted_debit_cents=-sum(debits),
        counted_credit_cents=sum(credits),
        **declared,
    )
