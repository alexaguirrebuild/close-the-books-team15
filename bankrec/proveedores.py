"""Plugin: pagos a proveedores y remesas de pago (POLICIES §5.2-§5.3).

Segmentos: ap_payment_multi, ap_payment_single, ap_confirming. Envuelve métodos del motor en prepare(), sin
editar engine.py (docs/TRASPASO.md).

- run_doc_item: misma elección que el motor, con el id como último desempate. Las candidatas salen de un set, y
  sin ese criterio el ganador de un empate dependía de PYTHONHASHSEED (dos ejecuciones, dos envíos distintos).
- Número corto (2-3 caracteres): num_variants no lo indexa y el documento se saltaba en silencio (540 pares en el
  holdout). Se busca entre las partidas del tercero del beneficiario, con número idéntico, candidata única y
  evidencia de importe: exacto, o partida abierta que cubre el pago (plazo).
- Confirming: lo que vence en fin de semana se paga el lunes. Se prueba primero por día de pago y, si el cargo no
  cuadra, por la fecha exacta como hacía el motor (un grupo de sábado cargado solo, o un cargo del viernes).
"""
from __future__ import annotations

import datetime as dt
import re
import types
from collections import defaultdict
from itertools import combinations

from .data import ascii_upper, num_variants

MIN_SHORT_NUMBER = 2  # «556» sí; «7» o «A» no son evidencia


def _exact(self, i, amt, ccy, d) -> bool:
    """El importe del documento coincide con el saldo abierto o el total de la partida (en su divisa)."""
    a = amt if i.ccy == ccy else self.ph.fx.conv(amt, ccy, i.ccy, d)
    return abs(i.open - a) <= max(2, a * 0.005) or abs(i.total - a) <= max(2, a * 0.005)


def run_doc_item(self, r, t):
    """Partida de un documento de remesa. Igual que Engine.run_doc_item, con desempate reproducible por id."""
    party = self.ph.party_by_iban.get((r.get("beneficiary_iban") or "").replace(" ", ""))
    if not party:
        party = self.resolve_name(r["beneficiary"])
    kinds = {"API", "APC", "ARC"} if r["document_type"] != "INVOICE" else {"API", "ARC"}
    amt = abs(int(round(float(r["document_amount"]) * 100)))
    variants = num_variants(r["document_number"])
    if not variants:
        return _short_number_item(self, r, t, party, kinds, amt)
    cands = set()
    for v in variants:
        cands |= self.ph.by_num.get(v, set())
    cands = [self.items[c] for c in cands if self.items[c].kind in kinds]
    ent = (r.get("debtor_account_id") or t.account or "").split("-")[0]
    if party:
        cands = [c for c in cands if c.party == party] or cands
    if len(cands) > 1:
        cands = [c for c in cands if abs(c.open - amt) <= 2 or abs(c.total - amt) <= 2 or c.open > 0] or cands
    # factoring: el beneficiario es la financiera y manda el importe del documento; la holding paga por las
    # filiales (POLICIES §5.2); el id deja el empate fijo
    cands.sort(key=lambda i: (-_exact(self, i, amt, r["currency"], t.date), -(i.entity == ent), -(i.open > 0),
                              i.issue or dt.date.min, i.id))
    return cands[0] if cands else None


def _norm(number) -> str:
    """La misma forma alfanumérica que usa num_variants (data.py) para el índice by_num."""
    return re.sub(r"[^A-Z0-9]", "", ascii_upper(number or ""))


def _short_number_item(self, r, t, party, kinds, amt):
    """Número de 2-3 caracteres («556»): demasiado corto para el índice global by_num, así que se busca solo entre
    las partidas del tercero del beneficiario, con número idéntico (POLICIES §5.3). La sociedad puede ser otra
    del grupo (§5.2). Evidencia de importe: exacto, o partida abierta que cubre el pago (un plazo). Única; si no,
    no se responde."""
    key = _norm(r["document_number"])
    if not party or len(key) < MIN_SHORT_NUMBER:
        return None
    cands = [self.items[i] for i in self.ph.by_party.get(party, ())
             if self.items[i].kind in kinds and _norm(self.items[i].number) == key]

    def covers(i):
        a = amt if i.ccy == r["currency"] else self.ph.fx.conv(amt, r["currency"], i.ccy, t.date)
        return i.open > 0 and a <= i.open + 2

    exact = [i for i in cands if _exact(self, i, amt, r["currency"], t.date)]
    cands = exact if exact else [i for i in cands if covers(i)]
    return cands[0] if len(cands) == 1 else None


def _next_business_day(d):
    """Sábado y domingo pasan al lunes. Los festivos no: dependen del país y no hay calendario en los datos."""
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return d


def _cites_confirming(engine, t) -> bool:
    for rid in engine._cited_runs(t):
        rs = engine.run_rows.get(rid)
        if rs and rs[0].get("run_type") == "confirming":
            return True
    return False


def _confirming_pick(engine, t, due_fn) -> bool:
    """La rama de confirming de Engine.m_payment_run (engine.py:571-596), con el vencimiento que da due_fn y nada más:
    sin los grupos por fecha de ejecución ni la búsqueda por importe, que pueden coger otra remesa."""
    amt = -t.amount
    m = re.search(r"\b(\d+) PROVEEDOR", t.up)
    n = int(m.group(1)) if m else None
    for rid in engine._cited_runs(t):
        rs = engine.run_rows.get(rid)
        if not rs or rs[0].get("run_type") != "confirming":
            continue
        pool = [(e, v) for e, v in due_fn(rid, rs, t).items()
                if (rid, e) not in engine.used_batches and -1 <= (t.date - v[0]).days <= 5]
        by_day = defaultdict(list)
        for e, v in pool:
            by_day[v[0]].append((e, v))
        pick = next((by_day[d] for d in sorted(by_day, key=lambda d: abs((t.date - d).days))
                     if abs(sum(v[2] for _, v in by_day[d]) - amt) <= 2 and n in (None, len(by_day[d]))), None)
        if pick is None and n and n <= 4 and len(pool) <= 40:
            pick = next((list(c) for c in combinations(pool, n) if abs(sum(v[2] for _, v in c) - amt) <= 2), None)
        if pick:
            for e, _ in pick:
                engine.used_batches.add((rid, e))
            engine.emit_run_rows(t, [r for _, v in pick for r in v[1]], "payment_run_confirming",
                                 f"remesa {rid} · {len(pick)} proveedores al vencimiento")
            return True
    return False


def _wrap_confirming(engine) -> None:
    """POLICIES §5.3 confirming: el banco carga a cada proveedor en su vencimiento más tardío, pero lo que vence en
    fin de semana se paga el lunes (holdout: 3.037 de 3.200 grupos de proveedor; cargos agrupables 480 -> 636 de
    788). Primero se prueba por día de pago; si el cargo no cuadra, por la fecha exacta, como antes, para no perder
    un grupo de sábado cargado solo ni un cargo del viernes."""
    exact_due = engine._confirming_due
    orig_run = engine.m_payment_run
    rolled_cache: dict = {}

    def rolled_due(rid, rs, t):
        if rid not in rolled_cache:
            rolled_cache[rid] = {e: (_next_business_day(v[0]), v[1], v[2]) for e, v in exact_due(rid, rs, t).items()}
        return rolled_cache[rid]

    def m_payment_run(t):
        # solo la rama de confirming con cada vencimiento; si ninguno cuadra, el motor entero como antes
        if t.amount < 0 and not (t.e2e and t.e2e in engine.e2e_rows) and _cites_confirming(engine, t):
            if _confirming_pick(engine, t, rolled_due) or _confirming_pick(engine, t, exact_due):
                return True
        return orig_run(t)

    engine.m_payment_run = m_payment_run


def prepare(engine) -> None:
    engine.run_doc_item = types.MethodType(run_doc_item, engine)
    _wrap_confirming(engine)


def match(engine, t) -> bool:
    return False
