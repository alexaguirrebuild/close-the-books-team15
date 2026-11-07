"""Plugin: cobros sin referencia clara, cuando varias partidas del cliente tienen el importe del cobro (POLICIES §5.6).

Un cobro nunca liquida un abono suelto: en el golden de TRAIN no hay ningún cobro cuya única fila sea un abono, y elegirlo
por ser el más antiguo deja la factura abierta y arrastra el error a los cobros siguientes.

Entre facturas de importe exacto manda el mes de servicio que nombra el texto y, si no lo hay, la más antigua. El plugin
`pdfclauses` solo lo mira cuando `resolve_party` da un cliente; aquí vale también si el nombre sale de `scan_names`.

Cuando un cobro de varias facturas deja una abierta, de esa factura llega el 40, 50 o 60 % exacto (POLICIES §3 PARTIAL;
medido en TRAIN: 925 de 925 en ar_n_to_m, casi nunca en otros segmentos). Es evidencia exacta de cuál es la parcial.
"""
from __future__ import annotations

from functools import partial
from itertools import combinations

from .engine import AMOUNT_TOLERANCE_CENTS, DUE_MARGIN_DAYS, MAX_SUBSET_ITEMS
from .pdfclauses import MONTHS, month_words, text_month, year_after
from .sumas import exact_subsets

PARTIAL_FRACTIONS = (0.4, 0.5, 0.6)
RECEIVABLE_KINDS = ("ARI", "ARC")
CENTS = 100
PARTIAL_TOLERANCE_CENTS = 1  # solo el redondeo de la fracción: con más, una suma casual pasaría por solución única
MAX_PARTIAL_CANDIDATES = 18


def prepare(engine) -> None:
    engine._party_amount = partial(_party_amount, engine, engine._party_amount)
    engine._party_rescue = partial(_party_rescue, engine, engine._party_rescue)
    engine.settle = partial(_settle, engine, engine.settle)
    engine.m_party_amount = partial(_m_party_amount, engine, engine.m_party_amount)
    if hasattr(engine, "m2_rows"):
        engine.m2_rows = _advices_with_partial(engine)


def partial_position(values: list[int], shortfall: int) -> int | None:
    """La única posición cuyo importe, menos lo que falta, es la fracción exacta de una parcial. Con dos o más, ninguna."""
    if shortfall <= AMOUNT_TOLERANCE_CENTS:
        return None
    hits = [position for position, value in enumerate(values) if value - shortfall > 0
            and any(abs(value - shortfall - value * fraction) <= AMOUNT_TOLERANCE_CENTS for fraction in PARTIAL_FRACTIONS)]
    return hits[0] if len(hits) == 1 else None


def _line_cents(line: dict) -> int:
    return int(round(float(line["amount"]) * CENTS))


def _is_receivable_in(item, t) -> bool:
    return bool(item) and item.kind in RECEIVABLE_KINDS and item.ccy == t.ccy


def _emit_with_partial(engine, t, full: list, partial_item, paid: int, rule: str, evidence: str) -> bool:
    for item in full:
        engine.emit(t, item, item.open, "", rule, evidence)
    engine.emit(t, partial_item, paid, "PARTIAL", rule, evidence)
    return True


def _credit_notes_cover(engine, t, party, cited: list, gap: int) -> bool:
    """Lo que `settle` resuelve con abonos sin citar (POLICIES §2): si cuadran la diferencia, no hay parcial."""
    known = {item.id for item in cited}
    values = [c.open for c in engine.open_items_of(party, t, {"ARC"}) if c.id not in known]
    return any(abs(value - gap) <= AMOUNT_TOLERANCE_CENTS for value in values) or any(
        abs(a + b - gap) <= AMOUNT_TOLERANCE_CENTS for a, b in combinations(values, 2))


def _partial_solutions(invoices: list, cash: int) -> list[tuple]:
    """Las soluciones (parcial, fracción, enteras) que suman el cobro, hasta dos: con dos ya se sabe que no identifica."""
    found = []
    for position, item in enumerate(invoices):
        others = [*invoices[:position], *invoices[position + 1:]]
        values = [other.open for other in others]
        for fraction in PARTIAL_FRACTIONS:
            paid = int(round(item.open * fraction))
            for chosen in exact_subsets(values, cash - paid, PARTIAL_TOLERANCE_CENTS):
                found.append((item, paid, [others[index] for index in chosen]))
            if len(found) > 1:
                return found[:2]
    return found


def _due_invoices(engine, party, t) -> list:
    items = engine.open_items_of(party, t, {"ARI"})
    return [item for item in items if item.ccy == t.ccy and (item.due is None or (item.due - t.date).days <= DUE_MARGIN_DAYS)]


def _party_due_partial(engine, t) -> bool:
    """Cobro de varias facturas que deja una abierta (POLICIES §3 PARTIAL): solo con solución única y un único cliente."""
    if t.amount <= 0:
        return False
    answers = {}
    for party in dict.fromkeys([*engine.resolve_parties(t)[0], *engine.scan_names(t)]):
        invoices = _due_invoices(engine, party, t) if "ARI" in engine._party_kinds(party, t) else []
        if 2 <= len(invoices) <= MAX_PARTIAL_CANDIDATES:
            solutions = _partial_solutions(invoices, t.amount)
            if solutions:
                answers[party] = solutions
    if len(answers) != 1:
        return False
    party, solutions = next(iter(answers.items()))
    if len(solutions) != 1:
        return False
    item, paid, full = solutions[0]
    return _emit_with_partial(engine, t, full, item, paid, "party_due_partial", f"contraparte {party} · parcial {item.number}")


def _m_party_amount(engine, original, t) -> bool:
    return original(t) or _party_due_partial(engine, t)


def _settle(engine, original, t, cands, rule, ev="", party=None) -> bool:
    items = [engine.items[c] for c in dict.fromkeys(cands)]
    invoices = [item for item in items if item.kind == "ARI"]
    if t.amount <= 0 or len(invoices) < 2 or not all(_is_receivable_in(item, t) for item in items):
        return original(t, cands, rule, ev, party)
    credits = [item for item in items if item.kind == "ARC"]
    values = [max(item.open, 0) for item in invoices]
    shortfall = sum(values) - sum(max(c.open, 0) for c in credits) - t.amount
    position = partial_position(values, shortfall)
    if position is None or (party and _credit_notes_cover(engine, t, party, items, shortfall)):
        return original(t, cands, rule, ev, party)
    full = [*credits, *(item for index, item in enumerate(invoices) if index != position)]
    return _emit_with_partial(engine, t, full, invoices[position], values[position] - shortfall, rule, ev)


def _advice_with_partial(engine, t, lines: list[dict]) -> list[dict]:
    """Las líneas de un aviso que suma más que el cobro, con la parcial marcada. Si no se identifica, las mismas líneas."""
    items = [engine.items.get(line["item_id"]) for line in lines]
    if t.amount <= 0 or any(line["exception"] for line in lines) or not all(_is_receivable_in(item, t) for item in items):
        return lines
    signed = [_line_cents(line) * engine.sign(item, t) for line, item in zip(lines, items)]
    shortfall = sum(signed) - abs(t.amount)
    position = partial_position(signed, shortfall)
    if position is None:
        return lines
    paid = {"amount": f"{(signed[position] - shortfall) / CENTS:.2f}", "exception": "PARTIAL"}
    return [{**line, **paid} if index == position else line for index, line in enumerate(lines)]


def _advices_with_partial(engine) -> dict:
    movements = {t.id: t for t in engine.ph.txns if t.id in engine.m2_rows}
    return {txn_id: _advice_with_partial(engine, movements[txn_id], lines) if txn_id in movements else lines
            for txn_id, lines in engine.m2_rows.items()}


def _month_with_year(t) -> tuple[int, int] | None:
    """Un nombre de persona puede parecer un mes (JOHN MAY): el que lleva año detrás es el que cuenta."""
    words = month_words(t)
    for position, word in enumerate(words):
        year = year_after(words, position) if word in MONTHS else None
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
    named = _month_with_year(t) or text_month(t)
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
