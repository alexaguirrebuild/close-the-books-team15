"""Abonos no citados en un cobro neto (POLICIES §2 «credit note netted» y §9): tres abonos, solo con solución única."""
import datetime as dt
from collections import defaultdict
from types import SimpleNamespace

from bankrec import abonos
from bankrec.data import Item
from bankrec.engine import Engine

DAY = dt.date(2025, 6, 10)


def item(item_id, kind, cents, party="C1"):
    return Item(id=item_id, kind=kind, side="AR", entity="ES01", number=item_id, party=party, ccy="EUR", total=cents,
                open=cents, issue=dt.date(2025, 5, 1))


def movement(amount):
    return SimpleNamespace(id="T1", amount=amount, entity="ES01", ccy="EUR", date=DAY, up="", cp_iban="", orig_ccy=None, orig_amount=None)


def engine(items):
    eng = Engine.__new__(Engine)
    by_party = {}
    for it in items:
        by_party.setdefault(it.party, []).append(it.id)
    eng.items = {it.id: it for it in items}
    eng.ph = SimpleNamespace(children={}, by_party=by_party, fx=None, terms={})
    eng.rows = []
    eng.settled_by = defaultdict(list)
    eng.iban_learned = {}
    eng.explain_short = lambda *a, **k: None
    abonos.prepare(eng)
    return eng


def paid(eng):
    return {(r.item, r.exception) for r in eng.rows}


def test_tres_abonos_que_cuadran_la_diferencia_se_compensan():
    eng = engine([item("INV", "ARI", 10000), item("N1", "ARC", 1000), item("N2", "ARC", 1500), item("N3", "ARC", 2000)])
    assert eng.settle(movement(5500), ["INV"], "refs", party="C1")
    assert paid(eng) == {("INV", ""), ("N1", ""), ("N2", ""), ("N3", "")}


def test_un_abono_o_dos_siguen_por_el_camino_original():
    eng = engine([item("INV", "ARI", 10000), item("N1", "ARC", 1000), item("N2", "ARC", 1500)])
    assert eng.settle(movement(7500), ["INV"], "refs", party="C1")
    assert paid(eng) == {("INV", ""), ("N1", ""), ("N2", "")}


def test_no_fuerza_si_hay_dos_ternas_posibles():
    # 1000+1500+2000 y 600+1000+2900 suman lo mismo (y ningún par suma 4500): sin solución única no se responde con abonos
    items = [item("INV", "ARI", 10000), item("N1", "ARC", 1000), item("N2", "ARC", 1500), item("N3", "ARC", 2000),
             item("N4", "ARC", 600), item("N5", "ARC", 2900)]
    eng = engine(items)
    eng.settle(movement(5500), ["INV"], "refs", party="C1")
    assert not {"N1", "N2", "N3", "N4", "N5"} & {r.item for r in eng.rows}


def test_no_fuerza_si_la_suma_no_cuadra():
    eng = engine([item("INV", "ARI", 10000), item("N1", "ARC", 1000), item("N2", "ARC", 1500), item("N3", "ARC", 2100)])
    eng.settle(movement(5500), ["INV"], "refs", party="C1")
    assert not {"N1", "N2", "N3"} & {r.item for r in eng.rows}


def test_abono_de_otra_sociedad_no_se_usa():
    other = item("N3", "ARC", 2000)
    other.entity = "DE01"
    eng = engine([item("INV", "ARI", 10000), item("N1", "ARC", 1000), item("N2", "ARC", 1500), other])
    eng.settle(movement(5500), ["INV"], "refs", party="C1")
    assert "N3" not in {r.item for r in eng.rows}
