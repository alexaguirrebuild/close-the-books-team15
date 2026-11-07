"""E-mail text advice: a sentence with the total and an indented table."""
from __future__ import annotations

import re
from decimal import Decimal

from .common import Advice, AdviceLine, clean, parse_amount, parse_date

SUBJECT = re.compile(r"^Subject:\s*(?:Payment advice|Aviso de pago)\s+(.+?)\s*$", re.MULTILINE)
SENTENCE_EN = re.compile(r"payment of\s+(-?[\d.,]+)\s+([A-Z]{3})\s+on\s+(\d{2}/\d{2}/\d{4})")
SENTENCE_ES = re.compile(r"importe de\s+(-?[\d.,]+)\s+([A-Z]{3})\s+con fecha\s+(\d{2}/\d{2}/\d{4})")
TABLE_LINE = re.compile(r"^\s{2,}(\S+)\s+(\d{2}/\d{2}/\d{2,4})\s+(-?[\d.,]+)(?:\s+(-?[\d.,]+)\s*(.*?))?\s*$")


def _line(match: re.Match[str]) -> AdviceLine:
    gross = parse_amount(match.group(3))
    deduction = parse_amount(match.group(4))
    paid = (gross or Decimal(0)) + (deduction or Decimal(0))
    return AdviceLine(
        document=match.group(1),
        doc_date=parse_date(match.group(2)),
        gross=gross,
        deduction=deduction,
        reason=clean(match.group(5)),
        paid=paid,
        your_order=None,
        voucher=None,
    )


def parse_txt_advice(text: str) -> Advice:
    english = SENTENCE_EN.search(text)
    spanish = SENTENCE_ES.search(text)
    sentence = english or spanish
    if sentence is None:
        raise ValueError("aviso de texto sin frase de importe reconocible")
    subject = SUBJECT.search(text)
    lines: list[AdviceLine] = []
    problems: list[str] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        if not raw.startswith("  "):
            continue
        match = TABLE_LINE.match(raw)
        if match:
            lines.append(_line(match))
        else:
            problems.append(f"línea {number} sin leer: {raw.strip()[:60]}")
    return Advice(
        payment_ref=subject.group(1) if subject else None,
        payment_date=parse_date(sentence.group(3), month_first=english is not None),
        currency=sentence.group(2),
        control_amount=parse_amount(sentence.group(1)),
        layout="txt_en" if english else "txt_es",
        lines=tuple(lines),
        problems=tuple(problems),
    )
