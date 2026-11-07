"""ISO 20022 camt.053: one `Ntry` per movement, parsed incrementally to keep memory flat."""
from __future__ import annotations

import io
import xml.etree.ElementTree as ET
from decimal import Decimal

from .model import ParsedStatement, RawLine, control_from, to_cents

CREDIT = "CRDT"
OPENING, CLOSING = "OPBD", "CLBD"


def _text(element: ET.Element, path: str) -> str | None:
    value = element.findtext(path)
    return value.strip() if value else None


def _free_text(element: ET.Element, path: str) -> str | None:
    """Free text is kept byte for byte, as the normalised view does."""
    return element.findtext(path) or None


def _bank_code(entry: ET.Element) -> str | None:
    domain = _text(entry, "{*}BkTxCd/{*}Domn/{*}Cd")
    family = _text(entry, "{*}BkTxCd/{*}Domn/{*}Fmly/{*}Cd")
    sub = _text(entry, "{*}BkTxCd/{*}Domn/{*}Fmly/{*}SubFmlyCd")
    parts = [part for part in (domain, family, sub) if part]
    return "/".join(parts) or None


def _counterparty(entry: ET.Element, credit: bool) -> tuple[str | None, str | None]:
    party, account = ("Dbtr", "DbtrAcct") if credit else ("Cdtr", "CdtrAcct")
    parties = "{*}NtryDtls/{*}TxDtls/{*}RltdPties"
    return (
        _text(entry, f"{parties}/{{*}}{party}/{{*}}Nm"),
        _text(entry, f"{parties}/{{*}}{account}/{{*}}Id/{{*}}IBAN"),
    )


def _original(entry: ET.Element) -> tuple[Decimal | None, str | None]:
    amount = entry.find("{*}NtryDtls/{*}TxDtls/{*}AmtDtls/{*}InstdAmt/{*}Amt")
    if amount is None or not amount.text:
        return None, None
    return Decimal(amount.text.strip()), amount.get("Ccy")


def _read_entry(entry: ET.Element, position: int) -> RawLine:
    amount = entry.find("{*}Amt")
    if amount is None or not amount.text:
        raise ValueError("entrada sin importe")
    credit = _text(entry, "{*}CdtDbtInd") == CREDIT
    cents = to_cents(Decimal(amount.text.strip()))
    name, account = _counterparty(entry, credit)
    orig_amount, orig_currency = _original(entry)
    booking = _text(entry, "{*}BookgDt/{*}Dt")
    if booking is None:
        raise ValueError("entrada sin fecha de reserva")
    return RawLine(
        src_line=position,
        booking_date=booking,
        value_date=_text(entry, "{*}ValDt/{*}Dt"),
        amount_cents=cents if credit else -cents,
        currency=amount.get("Ccy"),
        bank_code=_bank_code(entry),
        bank_subcode=_text(entry, "{*}BkTxCd/{*}Prtry/{*}Cd"),
        ref1=_text(entry, "{*}NtryRef") or "",
        text=_free_text(entry, "{*}AddtlNtryInf") or "",
        counterparty_name=name,
        counterparty_account=account,
        end_to_end_id=_text(entry, "{*}NtryDtls/{*}TxDtls/{*}Refs/{*}EndToEndId"),
        remittance_info=_free_text(entry, "{*}NtryDtls/{*}TxDtls/{*}RmtInf/{*}Ustrd"),
        orig_amount=orig_amount,
        orig_currency=orig_currency,
    )


def _balance(element: ET.Element) -> tuple[str | None, int]:
    amount = element.findtext("{*}Amt") or "0"
    cents = to_cents(Decimal(amount.strip()))
    signed = cents if _text(element, "{*}CdtDbtInd") == CREDIT else -cents
    return _text(element, "{*}Tp/{*}CdOrPrtry/{*}Cd"), signed


def parse_camt053(data: bytes) -> ParsedStatement:
    lines: list[RawLine] = []
    problems: list[str] = []
    opening: int | None = None
    closing: int | None = None
    position = 0
    for _, element in ET.iterparse(io.BytesIO(data), events=("end",)):
        tag = element.tag.rsplit("}", 1)[-1]
        if tag == "Ntry":
            position += 1
            try:
                lines.append(_read_entry(element, position))
            except (ValueError, ArithmeticError) as error:
                problems.append(f"entrada {position}: {error}")
            element.clear()
        elif tag == "Bal":
            code, cents = _balance(element)
            opening = cents if code == OPENING and opening is None else opening
            closing = cents if code == CLOSING else closing
    control = control_from(lines, opening_cents=opening, closing_cents=closing)
    return ParsedStatement("camt053", tuple(lines), control, tuple(problems))
