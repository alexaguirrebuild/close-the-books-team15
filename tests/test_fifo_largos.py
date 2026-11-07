"""Cobros grandes de un solo cliente que liquidan sus facturas más antiguas, de una en una, hasta el céntimo (POLICIES §1, §5.5).

Las sumas parciales de un orden fijo solo crecen, así que solo una puede cuadrar: con 15 o más facturas no hay ambigüedad
de subconjuntos. Las listas cortas ya las resuelven las reglas del motor.
"""
import datetime as dt
from types import SimpleNamespace

from bankrec import fifo_largos
from bankrec.data import Item
from bankrec.engine import Engine

DAY = dt.date(2025, 6, 10)


def invoice(n, party="C1", cents=10000, entity="ES01", kind="ARI"):
    return Item(id=f"{kind}{n:03d}", kind=kind, side="AR", entity=entity, number=f"N{n}", party=party, ccy="EUR", total=cents,
                open=cents, issue=dt.date(2025, 1, 1) + dt.timedelta(days=n))


def movement(amount):
    return SimpleNamespace(id="T1", amount=amount, entity="ES01", ccy="EUR", date=DAY, up="", orig_ccy=None, orig_amount=None)


def engine(items, named=("C1",), scanned=()):
    eng = Engine.__new__(Engine)
    by_party = {}
    for it in items:
        by_party.setdefault(it.party, []).append(it.id)
    eng.items = {it.id: it for it in items}
    eng.ph = SimpleNamespace(children={}, by_party=by_party, fx=None)
    eng.settled = []
    eng.settle = lambda t, ids, rule, ev="", party=None: eng.settled.append((list(ids), rule, party)) or True
    eng.resolve_parties = lambda t: (list(named), "nombre")
    eng.scan_names = lambda t: list(scanned)
    return eng


def many(n=30, party="C1", base=10000):
    return [invoice(i, party, base + 37 * i) for i in range(n)]


def prefix(items, k):
    return sum(i.open for i in sorted(items, key=lambda i: (i.issue, i.id))[:k])


def test_liquida_las_mas_antiguas_si_la_suma_cuadra_al_centimo():
    items = many(30)
    eng = engine(items)
    assert fifo_largos.match(eng, movement(prefix(items, 20)))
    ids, rule, party = eng.settled[0]
    assert len(ids) == 20 and rule == "fifo_prefix" and party == "C1"
    assert ids == sorted(ids)[:20]


def test_no_responde_si_ninguna_suma_parcial_cuadra():
    items = many(30)
    eng = engine(items)
    assert not fifo_largos.match(eng, movement(prefix(items, 20) + 500))
    assert eng.settled == []


def test_listas_cortas_se_dejan_a_las_reglas_del_motor():
    items = many(30)
    eng = engine(items)
    assert not fifo_largos.match(eng, movement(prefix(items, 8)))
    assert eng.settled == []


def test_si_dos_clientes_cuadran_no_responde():
    a, b = many(30, "C1"), many(30, "C2")
    eng = engine(a + b, named=["C1", "C2"])
    assert not fifo_largos.match(eng, movement(prefix(a, 20)))


def test_no_cuenta_facturas_de_otra_sociedad_ni_ya_pagadas():
    items = many(30)
    items[0].open = 0                       # ya pagada: no entra en la suma
    items[1].entity = "DE01"                # otra sociedad: tampoco
    eng = engine(items)
    live = [i for i in items if i.open > 0 and i.entity == "ES01"]
    assert fifo_largos.match(eng, movement(prefix(live, 20)))
    assert items[0].id not in eng.settled[0][0] and items[1].id not in eng.settled[0][0]


def test_un_cliente_con_pocas_facturas_abiertas_no_se_prueba():
    items = many(10)
    eng = engine(items)
    assert not fifo_largos.match(eng, movement(prefix(items, 10)))


def test_ingreso_negativo_o_cero_no_se_trata():
    eng = engine(many(30))
    assert not fifo_largos.match(eng, movement(0))
