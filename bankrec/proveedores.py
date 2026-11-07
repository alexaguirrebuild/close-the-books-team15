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

from .data import ascii_upper, num_variants

MIN_SHORT_NUMBER = 2  # «556» sí; «7» o «A» no son evidencia
GATES = ("m_dd_batch", "m_dd_return", "m_card", "m_cheque", "m_payment_run")


def _cents_or_none(value):
    """Céntimos de un importe de fichero, o None si viene roto (vacío, «N/A», «1.234,56», «nan»…)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or abs(f) == float("inf"):
        return None
    return abs(int(round(f * 100)))


def _exact(self, i, amt, ccy, d) -> bool:
    """El importe del documento coincide con el saldo abierto o el total de la partida (en su divisa)."""
    a = amt if i.ccy == ccy else self.ph.fx.conv(amt, ccy, i.ccy, d)
    return abs(i.open - a) <= max(2, a * 0.005) or abs(i.total - a) <= max(2, a * 0.005)


def run_doc_item(self, r, t):
    """Partida de un documento de remesa. Igual que Engine.run_doc_item, con desempate reproducible por id."""
    amt = _cents_or_none(r.get("document_amount"))
    if amt is None:  # importe roto: sin él no hay evidencia; se salta este documento, no la remesa
        return None
    party = self.ph.party_by_iban.get((r.get("beneficiary_iban") or "").replace(" ", ""))
    if not party:
        party = self.resolve_name(r.get("beneficiary") or "")
    kinds = {"API", "APC", "ARC"} if r.get("document_type") != "INVOICE" else {"API", "ARC"}
    variants = num_variants(r.get("document_number") or "")
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


ORIG_AMOUNT_WINDOW_DAYS = 5  # la misma ventana que usa el motor en su búsqueda por importe


def _cents(value) -> int:
    return int(round(float(value) * 100))


def _index_by_original(engine) -> dict:
    """(cuenta, importe, divisa) de cada transferencia -> [(e2e, filas)]. Una entrada por transferencia, como
    Engine._index_transfers, pero con la divisa: el importe original del cargo viene en la divisa del proveedor."""
    index: dict = {}
    for e2e in sorted(engine.e2e_rows):
        for group in engine._transfers_of(engine.e2e_rows[e2e]):
            r0 = group[0]
            index.setdefault((r0["debtor_account_id"], _cents(r0["transfer_amount"]), r0["currency"]), []).append((e2e, group))
    return index


def _by_original_amount(engine, index, t) -> bool:
    """ap_fx: cargo sin e2e ni remesa citada cuya transferencia solo cuadra por el importe original (POLICIES §5.3).
    Evidencia: misma cuenta, importe y divisa originales exactos, ejecución en ±5 días y candidata única."""
    if t.amount >= 0 or not t.orig_amount or not t.orig_ccy:
        return False
    hits = []
    for e2e, group in index.get((t.account, t.orig_amount, t.orig_ccy), ()):
        key = ("orig", e2e, group[0]["beneficiary"], group[0]["currency"])
        days = abs((dt.date.fromisoformat(group[0]["execution_date"]) - t.date).days)
        if key not in engine.used_batches and days <= ORIG_AMOUNT_WINDOW_DAYS:
            hits.append((key, e2e, group))
    if len(hits) != 1:
        return False
    key, e2e, group = hits[0]
    engine.used_batches.add(key)
    engine.emit_run_rows(t, group, "payment_run_amount_orig", f"transfer {e2e} · importe original {t.orig_ccy}")
    return True


def _wrap_payment_run(engine) -> None:
    """Envuelve m_payment_run (POLICIES §5.3):
    - confirming: el banco carga a cada proveedor en su vencimiento más tardío, pero lo que vence en fin de semana se
      paga el lunes (holdout: 3.037 de 3.200 grupos de proveedor; cargos agrupables 480 -> 636 de 788). Primero por
      día de pago; si el cargo no cuadra, por la fecha exacta, como antes (grupo de sábado cargado solo, viernes).
    - importe original: si ninguna ruta del motor casa el cargo, la transferencia por (cuenta, importe, divisa)
      originales (ap_fx: 617 cargos del holdout, todos con candidata única)."""
    exact_due = engine._confirming_due
    orig_run = engine.m_payment_run
    rolled_cache: dict = {}
    original_index = None

    def rolled_due(rid, rs, t):
        if rid not in rolled_cache:
            rolled_cache[rid] = {e: (_next_business_day(v[0]), v[1], v[2]) for e, v in exact_due(rid, rs, t).items()}
        return rolled_cache[rid]

    def engine_routes(t):
        if t.amount >= 0 or not _cites_confirming(engine, t):
            return orig_run(t)
        engine._confirming_due = rolled_due
        try:
            if orig_run(t):
                return True
        finally:
            engine._confirming_due = exact_due
        return orig_run(t)

    def m_payment_run(t):
        nonlocal original_index
        if engine_routes(t):
            return True
        if t.amount >= 0 or not t.orig_amount:
            return False
        if original_index is None:  # se construye al primer cargo en divisa, una sola vez
            original_index = _index_by_original(engine)
        return _by_original_amount(engine, original_index, t)

    engine.m_payment_run = m_payment_run


def _rollback(engine, n0) -> None:
    """Deshace las filas emitidas desde n0: saldo de la partida, settled_by y la lista de filas."""
    for row in reversed(engine.rows[n0:]):
        it = engine.items.get(row.item) if row.item else None
        if it is not None and row.amount is not None:
            it.open += -row.amount if row.exception == "RETURNED" else row.amount
            done = engine.settled_by.get(row.item)
            if done and done[-1] == (row.txn, row.amount):
                done.pop()
    del engine.rows[n0:]


def _safety_net(engine) -> None:
    """Una fila rota en un fichero de lotes no puede tumbar FINAL (un único envío). Si una puerta de lotes lanza
    una excepción con un movimiento, se deshace lo que hubiera emitido, se apunta en engine.lote2_errors y devuelve
    False: el movimiento sigue por las demás puertas y la fase continúa."""
    engine.lote2_errors = []

    def guard(name, gate):
        def guarded(t):
            n0 = len(engine.rows)
            try:
                return gate(t)
            except Exception as exc:  # noqa: BLE001 - cualquier dato roto, nunca caer
                _rollback(engine, n0)
                engine.lote2_errors.append((getattr(t, "id", None), name, f"{type(exc).__name__}: {exc}"[:200]))
                if len(engine.lote2_errors) <= 5:
                    print(f"  AVISO lotes: {name} en {getattr(t, 'id', '?')}: {type(exc).__name__}: {exc}"[:200])
                return False
        return guarded

    for name in GATES:
        if hasattr(engine, name):
            setattr(engine, name, guard(name, getattr(engine, name)))


def prepare(engine) -> None:
    engine.run_doc_item = types.MethodType(run_doc_item, engine)
    _wrap_payment_run(engine)
    _safety_net(engine)


def match(engine, t) -> bool:
    return False
