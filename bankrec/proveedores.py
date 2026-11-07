"""Plugin: pagos a proveedores y remesas de pago (POLICIES §5.2-§5.3).

Segmentos: ap_payment_multi, ap_payment_single, ap_confirming. Envuelve métodos del motor en prepare(), sin
editar engine.py (docs/TRASPASO.md).

- run_doc_item: misma elección que el motor, con el id como último desempate. Las candidatas salen de un set, y
  sin ese criterio el ganador de un empate dependía de PYTHONHASHSEED (dos ejecuciones, dos envíos distintos).
- Número corto (2-3 dígitos): num_variants no lo indexa y el documento se saltaba en silencio (540 pares en el
  holdout). Se busca entre las partidas del tercero del beneficiario, con número idéntico y candidata única.
"""
from __future__ import annotations

import datetime as dt
import re
import types

from .data import ascii_upper, num_variants


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
    if not num_variants(r["document_number"]):
        return _short_number_item(self, r, t, party, kinds, amt)
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


def _norm(number) -> str:
    return re.sub(r"[^A-Z0-9]", "", ascii_upper(number or ""))


def _short_number_item(self, r, t, party, kinds, amt):
    """Número de 2-3 dígitos («556»): demasiado corto para el índice global by_num, así que se busca solo entre
    las partidas del tercero del beneficiario, con número idéntico (POLICIES §5.3). Única, o única por importe
    exacto; si no, no se responde."""
    key = _norm(r["document_number"])
    if not party or not key:
        return None
    cands = [self.items[i] for i in self.ph.by_party.get(party, ())
             if self.items[i].kind in kinds and _norm(self.items[i].number) == key]
    if len(cands) > 1:
        def exact(i):
            a = amt if i.ccy == r["currency"] else self.ph.fx.conv(amt, r["currency"], i.ccy, t.date)
            return abs(i.open - a) <= max(2, a * 0.005) or abs(i.total - a) <= max(2, a * 0.005)
        cands = [i for i in cands if exact(i)]
    return cands[0] if len(cands) == 1 else None


def _next_business_day(d):
    """Sábado y domingo pasan al lunes. Los festivos no: dependen del país y no hay calendario en los datos."""
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return d


def _wrap_confirming(engine) -> None:
    """POLICIES §5.3 confirming: el banco carga a cada proveedor en su vencimiento más tardío, pero lo que vence en
    fin de semana se paga el lunes. El motor agrupaba por la fecha exacta y un lunes con vencimientos de sábado,
    domingo y lunes no cuadraba (holdout: 3.037 de 3.200 grupos de proveedor siguen esta regla)."""
    orig = engine._confirming_due

    def confirming_due(rid, rs, t):
        return {e: (_next_business_day(v[0]), v[1], v[2]) for e, v in orig(rid, rs, t).items()}

    engine._confirming_due = confirming_due


def prepare(engine) -> None:
    engine.run_doc_item = types.MethodType(run_doc_item, engine)
    _wrap_confirming(engine)


def match(engine, t) -> bool:
    return False
