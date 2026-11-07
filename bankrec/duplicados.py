"""Pago duplicado sin referencia (POLICIES §3 DUPLICATE_PAYMENT).

m_refs solo los ve si el texto cita la factura. Si no, un cobro de un cliente conocido que repite el importe de una factura
ya liquidada acababa como UNIDENTIFIED. Sube el holdout de 82,39 a 82,92.

Se probó también imputar otros cobros de clientes con partidas abiertas (sobrepago, parcial, reparto entre facturas). En
sample subía a 85,49, pero en el holdout bajaba a 81,49: 1.054 UNIDENTIFIED verdaderos pasaban a tener partida y aparecían
unas 34.000 filas de más. No forzar coincidencias es justo lo que pide POLICIES.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import TypeVar

Invoice = TypeVar("Invoice")
AMOUNT_TOLERANCE_CENTS = 2
MAX_PARTIES = 4
DIRECT_DEBIT_TEXT = re.compile(r"ADEUDO|RECIBO|DIRECT DEBIT|LASTSCHRIFT|PRELEV")


def find_duplicate(closed: Sequence[Invoice], cash: int, value_of: Callable[[Invoice], float]) -> Invoice | None:
    """La factura ya cerrada más reciente con ese mismo importe. `closed` va de la más antigua a la más nueva."""
    same = [inv for inv in closed if abs(value_of(inv) - cash) <= AMOUNT_TOLERANCE_CENTS]
    return same[-1] if same else None


def _closed_invoices(engine, party: str, t) -> list:
    family = {party} | engine.ph.children.get(party, set())
    found = []
    for pid in family:
        for iid in engine.ph.by_party.get(pid, ()):
            it = engine.items[iid]
            if it.open <= 0 and it.kind == "ARI" and engine.entity_ok(it, t) and (it.issue is None or it.issue <= t.date):
                found.append(it)
    return sorted(found, key=lambda it: (it.issue or t.date, it.id))


def settle_duplicate(engine, t) -> bool:
    """Devuelve True si ha emitido una fila. Solo cobros de clientes."""
    parties, how = engine.resolve_parties(t)
    if not parties:
        parties, how = engine.scan_names(t), "nombre_texto"
    for party in parties[:MAX_PARTIES]:
        if not party.startswith("C"):
            continue
        it = find_duplicate(_closed_invoices(engine, party, t), abs(t.amount), lambda i: engine.to_bank(i.total, i, t))
        if it is None:
            continue
        if it.method == "dd" and not DIRECT_DEBIT_TEXT.search(t.up):
            # Mismo criterio que m_refs: un recibo devuelto que el cliente paga por transferencia no es duplicado (POLICIES §5.10).
            engine.emit(t, it, it.total, "", "ur_represented", f"contraparte {party}")
        else:
            engine.emit(t, it, it.total, "DUPLICATE_PAYMENT", f"ur_duplicate_{how}", f"contraparte {party}")
        return True
    return False
