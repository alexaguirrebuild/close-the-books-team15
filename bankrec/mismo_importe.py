"""Plugin: cobros sin referencia clara, cuando varias partidas del cliente tienen el importe del cobro (POLICIES §5.6).

Un cobro nunca liquida un abono suelto: en el golden de TRAIN no hay ningún cobro cuya única fila sea un abono, y elegirlo
por ser el más antiguo deja la factura abierta y arrastra el error a los cobros siguientes.

Entre facturas de importe exacto manda el mes de servicio que nombra el texto y, si no lo hay, la más antigua. El plugin
`pdfclauses` solo lo mira cuando `resolve_party` da un cliente; aquí vale también si el nombre sale de `scan_names`.
"""
from __future__ import annotations

import re
from functools import partial

from .engine import AMOUNT_TOLERANCE_CENTS, MAX_SUBSET_ITEMS
from .pdfclauses import MONTHS, _text_month

YEAR_DIGITS = (2, 4)
CENTURY = 2000


def prepare(engine) -> None:
    engine._party_amount = partial(_party_amount, engine, engine._party_amount)
    engine._party_rescue = partial(_party_rescue, engine, engine._party_rescue)


def _text_year(text: str) -> int | None:
    if not text.isdigit() or len(text) not in YEAR_DIGITS:
        return None
    return int(text) if len(text) == 4 else CENTURY + int(text)


def _month_with_year(t) -> tuple[int, int] | None:
    """Un nombre de persona puede parecer un mes (JOHN MAY): el que lleva año detrás es el que cuenta."""
    words = re.findall(r"[A-Z]+|\d+", t.up)
    for position, word in enumerate(words[:-1]):
        if word not in MONTHS:
            continue
        following = words[position + 1]
        if following == "DE" and position + 2 < len(words):
            following = words[position + 2]
        year = _text_year(following)
        if year:
            return year, MONTHS[word]
    return None


def _service_month(engine, item) -> tuple[int, int] | None:
    clause = (getattr(engine, "clauses", None) or {}).get(item.id) or {}
    if clause.get("month"):
        return clause["month"]
    if not item.issue:
        return None
    return (item.issue.year, item.issue.month - 1) if item.issue.month > 1 else (item.issue.year - 1, 12)


def pick_same_amount(engine, t, invoices: list):
    """La factura del mes que nombra el texto; si no hay ninguna, la más antigua. Solo desempata: nunca añade candidatas."""
    if len(invoices) == 1:
        return invoices[0]
    named = _month_with_year(t) or _text_month(t)
    if not named:
        return invoices[0]
    year, month = named
    for item in invoices:
        service = _service_month(engine, item)
        if service and service[1] == month and (year is None or service[0] == year):
            return item
    return invoices[0]


def _same_amount(engine, items, t) -> list:
    return [item for item in items if abs(engine.to_bank(item.open, item, t) - abs(t.amount)) <= AMOUNT_TOLERANCE_CENTS]


def _invoices(engine, items, t) -> list:
    return [item for item in items if engine.sign(item, t) > 0]


def _exact_subset(engine, items, t) -> list | None:
    """Mismo orden que el motor: primero lo que suma al cobro y después lo que resta."""
    ordered = [*_invoices(engine, items, t), *(item for item in items if engine.sign(item, t) < 0)]
    return engine.best_subset(ordered, abs(t.amount), t, max_n=MAX_SUBSET_ITEMS)


def _party_amount(engine, original, t, party, how) -> bool:
    if t.amount <= 0:
        return original(t, party, how)
    items = engine.open_items_of(party, t, engine._party_kinds(party, t))
    same = _same_amount(engine, items, t)
    if not same:
        return original(t, party, how)
    invoices = _invoices(engine, same, t)
    if invoices:
        return engine.settle(t, [pick_same_amount(engine, t, invoices).id], f"party_amount_{how}", f"contraparte {party}", party=party)
    subset = _exact_subset(engine, items, t)
    return bool(subset) and engine.settle(t, [item.id for item in subset], f"party_subset_{how}", f"contraparte {party}")


def _party_rescue(engine, original, t, tried) -> bool:
    if t.amount <= 0:
        return original(t, tried)
    exact, subsets = {}, {}
    for party in dict.fromkeys([*engine.resolve_parties(t)[0], *engine.scan_names(t)]):
        if party in tried:
            continue
        items = engine.open_items_of(party, t, engine._party_kinds(party, t))
        invoices = _invoices(engine, _same_amount(engine, items, t), t)
        if invoices:
            exact[party] = [pick_same_amount(engine, t, invoices)]
        elif 1 < len(items) <= MAX_SUBSET_ITEMS:
            subset = _exact_subset(engine, items, t)
            if subset:
                subsets[party] = subset
    for found, rule in ((exact, "party_rescue_exact"), (subsets, "party_rescue_subset")):
        if found:
            if len(found) > 1:
                return False
            party, picks = next(iter(found.items()))
            return engine.settle(t, [item.id for item in picks], rule, f"contraparte {party}", party=party)
    return False
