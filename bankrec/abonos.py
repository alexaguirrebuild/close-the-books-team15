"""Plugin: cobros netos de tres abonos no citados. POLICIES §2 («credit note netted in a payment») y §9.

`settle()` busca abonos que cuadren la diferencia entre lo que suman las partidas y lo cobrado, pero solo prueba uno o
dos. Aquí se prueban tres, y solo se usan si hay una única terna que cuadre (sin evidencia exacta no se responde).

Segunda regla: el texto del banco se corta y solo cita parte de las facturas. Si lo citado deja dinero sin explicar, se
completa con el único subconjunto exacto de las demás partidas abiertas del tercero (y a lo sumo un abono).
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
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


MAX_OPEN = 22      # 2^11 por mitad; más partidas ya no identifican con una suma exacta


def _subset_sums(vals):
    sums = [(0, 0)]
    for k, v in enumerate(vals):
        sums += [(s + v, m | (1 << k)) for s, m in sums]
    return sorted(sums)


def _exact_subsets(vals, target, limit=2):
    """Máscaras de los subconjuntos de `vals` que suman `target` (±TOL); se corta al llegar a `limit`."""
    half = len(vals) // 2
    left, right = _subset_sums(vals[:half]), _subset_sums(vals[half:])
    keys = [s for s, _ in right]
    found = []
    for s, m in left:
        lo, hi = bisect_left(keys, target - s - TOL), bisect_right(keys, target - s + TOL)
        for _, rm in right[lo:hi]:
            found.append(m | (rm << half))
            if len(found) >= limit:
                return found
    return found


def _complete(engine, t, cands, party):
    """Partidas que faltan para explicar un cobro de más: única solución exacta entre facturas y, si hace falta, un abono."""
    exp = sum(engine.sign(it, t) * engine.to_bank(max(it.open, 0), it, t) for it in cands)
    surplus = abs(t.amount) - exp
    if surplus <= TOL:
        return None
    inv_kind, cn_kind = ({"ARI"}, {"ARC"}) if t.amount > 0 else ({"API"}, {"APC"})
    taken = {c.id for c in cands}
    invoices = sorted((c for c in engine.open_items_of(party, t, inv_kind) if c.id not in taken), key=lambda c: c.id)
    credits = sorted((c for c in engine.open_items_of(party, t, cn_kind) if c.id not in taken), key=lambda c: c.id)
    if not invoices or len(invoices) > MAX_OPEN:
        return None
    vals = [int(round(engine.to_bank(c.open, c, t))) for c in invoices]
    solutions = []
    for credit in [None] + credits[:MAX_CREDITS]:
        cv = int(round(engine.to_bank(credit.open, credit, t))) if credit else 0
        for mask in _exact_subsets(vals, surplus + cv):
            if mask:
                solutions.append(([invoices[i] for i in range(len(invoices)) if mask >> i & 1], credit))
        if len(solutions) > 1:
            return None
    if len(solutions) != 1:
        return None
    picked, credit = solutions[0]
    return picked + ([credit] if credit else [])


def prepare(engine) -> None:
    original = engine.settle

    def settle(t, cands, rule, ev="", party=None):
        if party:
            items = [engine.items[c] for c in dict.fromkeys(cands)]
            trio = _unique_triple(engine, t, items, party)
            if trio:
                ids = [it.id for it in items] + [c.id for c in trio]
                return original(t, ids, rule, f"{ev} +3 abonos".strip(), None)
            more = _complete(engine, t, items, party)
            if more:
                ids = [it.id for it in items] + [c.id for c in more]
                return original(t, ids, rule, f"{ev} +{len(more)} partidas por suma exacta".strip(), None)
        return original(t, cands, rule, ev, party)

    engine.settle = settle
