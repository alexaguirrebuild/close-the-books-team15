"""Plugin: cobros netos de tres abonos no citados. POLICIES §2 («credit note netted in a payment») y §9.

`settle()` busca abonos que cuadren la diferencia entre lo que suman las partidas y lo cobrado, pero solo prueba uno o
dos. Aquí se prueban tres, y solo se usan si hay una única terna que cuadre (sin evidencia exacta no se responde).
"""
from __future__ import annotations

from itertools import combinations

TOL = 2
MAX_CREDITS = 30   # C(30,3) = 4.060 ternas como mucho por movimiento


def _unique_triple(engine, t, cands, party):
    """Los tres abonos abiertos del tercero que cuadran el hueco, o None si no hay uno solo o ya vale uno o dos."""
    gap = sum(engine.sign(it, t) * engine.to_bank(max(it.open, 0), it, t) for it in cands) - abs(t.amount)
    if gap <= TOL:
        return None
    kinds = {"ARC"} if t.amount > 0 else {"APC"}
    taken = {c.id for c in cands}
    credits = sorted((c for c in engine.open_items_of(party, t, kinds) if c.id not in taken), key=lambda c: c.id)
    if len(credits) < 3 or len(credits) > MAX_CREDITS:
        return None
    vals = [int(round(engine.to_bank(c.open, c, t))) for c in credits]
    if any(abs(v - gap) <= TOL for v in vals):
        return None
    if any(abs(vals[a] + vals[b] - gap) <= TOL for a, b in combinations(range(len(vals)), 2)):
        return None
    found = [trio for trio in combinations(range(len(vals)), 3) if abs(sum(vals[i] for i in trio) - gap) <= TOL]
    if len(found) != 1:
        return None
    return [credits[i] for i in found[0]]


def prepare(engine) -> None:
    original = engine.settle

    def settle(t, cands, rule, ev="", party=None):
        if party:
            items = [engine.items[c] for c in dict.fromkeys(cands)]
            trio = _unique_triple(engine, t, items, party)
            if trio:
                ids = [it.id for it in items] + [c.id for c in trio]
                return original(t, ids, rule, f"{ev} +3 abonos".strip(), None)
        return original(t, cands, rule, ev, party)

    engine.settle = settle
