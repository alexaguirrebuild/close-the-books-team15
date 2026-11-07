"""Rescate de terceros: quien el camino normal no llegó a probar, solo con evidencia exacta y un único candidato."""
import datetime as dt
from types import SimpleNamespace

import pytest

from bankrec.data import Item
from bankrec.engine import Engine

DAY = dt.date(2025, 6, 10)
CASH = 10000


def invoice(item_id, party, cents=CASH, entity="ES01"):
    return Item(id=item_id, kind="ARI", side="AR", entity=entity, number=item_id, party=party, ccy="EUR", total=cents, open=cents, issue=dt.date(2025, 5, 1))


def movement(amount=CASH):
    return SimpleNamespace(id="T1", amount=amount, entity="ES01", ccy="EUR", date=DAY, up="", orig_ccy=None, orig_amount=None)


def engine(items, named=(), scanned=()):
    eng = Engine.__new__(Engine)
    by_party = {}
    for item in items:
        by_party.setdefault(item.party, []).append(item.id)
    eng.items = {item.id: item for item in items}
    eng.ph = SimpleNamespace(children={}, by_party=by_party, fx=None)
    eng.settled = []
    eng.settle = lambda t, ids, rule, ev="", party=None: eng.settled.append((list(ids), rule)) or True
    eng.resolve_parties = lambda t: (list(named), "nombre" if named else "")
    eng.scan_names = lambda t: list(scanned)
    return eng


class TestCaminoNormal:
    def test_el_primer_tercero_con_importe_exacto_sigue_igual(self):
        eng = engine([invoice("A", "C1")], named=["C1"])
        assert eng.m_party_amount(movement())
        assert eng.settled == [(["A"], "party_amount_nombre")]


class TestRescate:
    def test_un_homonimo_mas_alla_de_los_cuatro_primeros(self):
        eng = engine([invoice("A", "C5")], named=["C1", "C2", "C3", "C4", "C5"])
        assert eng.m_party_amount(movement())
        assert eng.settled == [(["A"], "party_rescue_exact")]

    def test_scan_names_se_prueba_aunque_el_nombre_diera_otro_tercero(self):
        eng = engine([invoice("A", "C2")], named=["C1"], scanned=["C2"])
        assert eng.m_party_amount(movement())
        assert eng.settled == [(["A"], "party_rescue_exact")]

    def test_una_suma_exacta_de_pocas_partidas_vale(self):
        items = [invoice("A", "C2", 6000), invoice("B", "C2", 4000), invoice("C", "C2", 777)]
        eng = engine(items, named=["C1"], scanned=["C2"])
        assert eng.m_party_amount(movement())
        assert sorted(eng.settled[0][0]) == ["A", "B"]
        assert eng.settled[0][1] == "party_rescue_subset"


class TestNoFuerza:
    def test_dos_candidatos_exactos_no_se_responde(self):
        eng = engine([invoice("A", "C2"), invoice("B", "C3")], named=["C1"], scanned=["C2", "C3"])
        assert not eng.m_party_amount(movement())
        assert eng.settled == []

    def test_el_importe_exacto_gana_a_la_suma_de_otro_tercero(self):
        items = [invoice("A", "C2"), invoice("B", "C3", 6000), invoice("D", "C3", 4000)]
        eng = engine(items, named=["C1"], scanned=["C2", "C3"])
        assert eng.m_party_amount(movement())
        assert eng.settled == [(["A"], "party_rescue_exact")]

    def test_sin_importe_exacto_no_hay_parcial_adivinado(self):
        eng = engine([invoice("A", "C2", CASH + 500)], named=["C1"], scanned=["C2"])
        assert not eng.m_party_amount(movement())
        assert eng.settled == []

    def test_una_partida_de_otra_sociedad_no_cuenta(self):
        eng = engine([invoice("A", "C2", entity="FR01")], named=["C1"], scanned=["C2"])
        assert not eng.m_party_amount(movement())

    def test_con_demasiadas_partidas_abiertas_la_suma_no_identifica(self):
        items = [invoice(f"I{n:02d}", "C2", 300 + n) for n in range(15)]
        cash = items[0].open + items[1].open
        eng = engine(items, named=["C1"], scanned=["C2"])
        assert not eng.m_party_amount(movement(cash))
        assert eng.settled == []

    def test_una_factura_ya_pagada_no_cuenta(self):
        paid = invoice("A", "C2")
        paid.open = 0
        eng = engine([paid], named=["C1"], scanned=["C2"])
        assert not eng.m_party_amount(movement())

    def test_un_pago_de_salida_no_rescata_facturas_de_cliente(self):
        eng = engine([invoice("A", "C2")], named=["S1"], scanned=["C2"])
        assert not eng.m_party_amount(movement(-CASH))
        assert eng.settled == []


def test_sin_ningun_tercero_no_hace_nada():
    eng = engine([invoice("A", "C2")])
    assert not eng.m_party_amount(movement())


@pytest.mark.parametrize("party,amount,kinds", [("C1", CASH, {"ARI", "ARC"}), ("S1", CASH, {"APC"}), ("S1", -CASH, {"API", "APC"}), ("C1", -CASH, {"ARC"})])
def test_clases_de_partida_por_tercero_y_signo(party, amount, kinds):
    assert Engine._party_kinds(party, movement(amount)) == kinds
