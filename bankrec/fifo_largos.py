"""Plugin: cobros grandes de un cliente que liquidan sus facturas más antiguas hasta el céntimo. POLICIES §1 y §5.5.

Los clientes con muchas facturas abiertas pagan por antigüedad y el banco no cita todas. Las reglas del motor desisten por
encima de 14 partidas porque «muchas combinaciones suman lo mismo»; con un orden fijo (la más antigua primero) no hay
combinaciones: las sumas parciales siguen un único camino, y cuadrar al céntimo con 15 o más facturas no es casualidad.
Solo responde si el cliente es el único que cuadra y exactamente una suma parcial lo hace; si no, deja pasar al motor.
"""
from __future__ import annotations

MIN_ITEMS = 15      # por debajo, las reglas de subconjunto del motor ya cubren el caso
TOL = 2             # céntimos, igual que AMOUNT_TOLERANCE_CENTS del motor


def _exact_prefix(engine, t, party):
    """Partidas más antiguas cuya suma cuadra con el cobro, o None si no hay una sola forma de hacerlo."""
    items = engine.open_items_of(party, t, engine._party_kinds(party, t))
    if len(items) < MIN_ITEMS:
        return None
    cash, acc, hits = abs(t.amount), 0.0, []
    for k, it in enumerate(items, start=1):
        acc += engine.sign(it, t) * engine.to_bank(max(it.open, 0), it, t)
        if k >= MIN_ITEMS and abs(acc - cash) <= TOL:
            hits.append(k)
    return items[:hits[0]] if len(hits) == 1 else None


def match(engine, t) -> bool:
    if t.amount <= 0:
        return False
    found = {}
    for party in dict.fromkeys([*engine.resolve_parties(t)[0], *engine.scan_names(t)]):
        picks = _exact_prefix(engine, t, party)
        if picks:
            found[party] = picks
    if len(found) != 1:
        return False
    party, picks = next(iter(found.items()))
    return engine.settle(t, [i.id for i in picks], "fifo_prefix", f"contraparte {party} · {len(picks)} más antiguas por suma exacta",
                         party=party)
