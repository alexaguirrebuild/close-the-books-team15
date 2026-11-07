"""Plugin: pagos a proveedores y remesas de pago (POLICIES §5.2-§5.3).

Segmentos: ap_payment_multi, ap_payment_single, ap_confirming. Envuelve métodos del motor en prepare(), sin
editar engine.py (docs/TRASPASO.md).

- run_doc_item: misma elección que el motor, con el id como último desempate. Las candidatas salen de un set, y
  sin ese criterio el ganador de un empate dependía de PYTHONHASHSEED (dos ejecuciones, dos envíos distintos).
"""
from __future__ import annotations

import datetime as dt
import types

from .data import num_variants


def run_doc_item(self, r, t):
    """Partida de un documento de remesa. Igual que Engine.run_doc_item, con desempate reproducible por id."""
    party = self.ph.party_by_iban.get((r.get("beneficiary_iban") or "").replace(" ", ""))
    if not party:
        party = self.resolve_name(r["beneficiary"])
    kinds = {"API", "APC", "ARC"} if r["document_type"] != "INVOICE" else {"API", "ARC"}
    amt = abs(int(round(float(r["document_amount"]) * 100)))
    cands = set()
    for v in num_variants(r["document_number"]):
        cands |= self.ph.by_num.get(v, set())
    cands = [self.items[c] for c in cands if self.items[c].kind in kinds]
    ent = (r.get("debtor_account_id") or t.account or "").split("-")[0]
    if party:
        cands = [c for c in cands if c.party == party] or cands
    if len(cands) > 1:
        cands = [c for c in cands if abs(c.open - amt) <= 2 or abs(c.total - amt) <= 2 or c.open > 0] or cands

    def exact(i):  # factoring: el beneficiario es la financiera, manda el importe del documento
        a = amt if i.ccy == r["currency"] else self.ph.fx.conv(amt, r["currency"], i.ccy, t.date)
        return abs(i.open - a) <= max(2, a * 0.005) or abs(i.total - a) <= max(2, a * 0.005)

    # la holding paga por las filiales (POLICIES §5.2); el id deja el empate fijo
    cands.sort(key=lambda i: (-exact(i), -(i.entity == ent), -(i.open > 0), i.issue or dt.date.min, i.id))
    return cands[0] if cands else None


def prepare(engine) -> None:
    engine.run_doc_item = types.MethodType(run_doc_item, engine)


def match(engine, t) -> bool:
    return False
