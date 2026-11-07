"""Números citados en cualquier forma (POLICIES §5.5): solo cuentan si las facturas citadas suman exactamente el cobro."""
import datetime as dt
from collections import defaultdict
from types import SimpleNamespace

from bankrec.data import Item
from bankrec.engine import Engine

DAY = dt.date(2025, 6, 10)


def item(number, party="C1", cents=1000, kind="ARI", entity="ES01", open_cents=None):
    return Item(id=number, kind=kind, side="AR", entity=entity, number=number, party=party, ccy="EUR", total=cents, open=cents if open_cents is None else open_cents, issue=dt.date(2025, 5, 1))


def movement(text, amount):
    return SimpleNamespace(id="T1", amount=amount, entity="ES01", ccy="EUR", date=DAY, up=text, tokens=set(), orig_ccy=None, orig_amount=None)


def engine(items, named=("C1",), scanned=(), by_num=None):
    eng = Engine.__new__(Engine)
    by_party = {}
    for it in items:
        by_party.setdefault(it.party, []).append(it.id)
    eng.items = {it.id: it for it in items}
    eng.ph = SimpleNamespace(children={}, by_party=by_party, by_num=defaultdict(set, by_num or {}), by_suffix=defaultdict(set), fx=None)
    eng.settled = []
    eng.settle = lambda t, ids, rule, ev="", party=None: eng.settled.append((sorted(ids), rule)) or True
    eng.resolve_parties = lambda t: (list(named), "nombre" if named else "")
    eng.scan_names = lambda t: list(scanned)
    return eng


class TestCitasValidas:
    def test_sufijos_de_siete_digitos(self):
        items = [item("FV24-0427095", cents=600), item("FV24-0427729", cents=400), item("FV24-0999999", cents=50)]
        eng = engine(items)
        assert eng.m_refs(movement("FRAS FV24-0427095 0427729", 1000))
        assert eng.settled == [(["FV24-0427095", "FV24-0427729"], "ref_cited")]

    def test_una_cita_de_mas_no_estropea_la_suma_exacta(self):
        items = [item("FT25-01-15350", cents=600), item("FT25-01-14211", cents=400), item("FT25-01-13720", cents=555)]
        eng = engine(items)
        assert eng.m_refs(movement("SALDO FT25-01-15350 14211 13720", 1000))
        assert eng.settled == [(["FT25-01-14211", "FT25-01-15350"], "ref_cited")]

    def test_un_abono_no_citado_que_cuadra_la_diferencia(self):
        items = [item("FV24-0118956", cents=700), item("FV24-0120216", cents=500), item("AB24-0100488", cents=200, kind="ARC")]
        eng = engine(items)
        assert eng.m_refs(movement("FRA. FV24-0120216 0118956", 1000))
        assert eng.settled == [(["FV24-0118956", "FV24-0120216"], "ref_cited")]

    def test_el_nombre_visto_por_scan_names_tambien_vale(self):
        eng = engine([item("FV24-0427095", cents=1000)], named=(), scanned=("C1",))
        assert eng.m_refs(movement("PAGO 0427095", 1000))
        assert eng.settled == [(["FV24-0427095"], "ref_cited")]


class TestNoFuerza:
    def test_si_la_suma_no_cuadra_no_se_responde(self):
        eng = engine([item("FV24-0427095", cents=600)])
        assert not eng.m_refs(movement("FRA 0427095", 1000))
        assert eng.settled == []

    def test_sin_cliente_identificado_no_se_buscan_citas(self):
        eng = engine([item("FV24-0427095", cents=1000)], named=())
        assert not eng.m_refs(movement("PAGO 0427095", 1000))

    def test_la_factura_de_otro_cliente_no_cuenta(self):
        eng = engine([item("FV24-0427095", party="C2", cents=1000)])
        assert not eng.m_refs(movement("PAGO 0427095", 1000))

    def test_un_trozo_de_menos_de_cuatro_digitos_no_cita(self):
        eng = engine([item("FV24-0000123", cents=1000)])
        assert not eng.m_refs(movement("FRA 123", 1000))

    def test_una_factura_ya_pagada_no_cuenta(self):
        eng = engine([item("FV24-0427095", cents=1000, open_cents=0)])
        assert not eng.m_refs(movement("PAGO 0427095", 1000))

    def test_dos_clientes_con_suma_exacta_no_se_responde(self):
        items = [item("FV24-0427095", cents=1000), item("RE25-0427095", party="C2", cents=1000)]
        eng = engine(items, named=("C1", "C2"))
        assert not eng.m_refs(movement("PAGO 0427095", 1000))
        assert eng.settled == []

    def test_una_factura_de_otra_sociedad_no_cuenta(self):
        eng = engine([item("FV24-0427095", cents=1000, entity="FR01")])
        assert not eng.m_refs(movement("PAGO 0427095", 1000))


class TestCaminoNormal:
    def test_el_numero_completo_que_cuadra_sigue_por_la_regla_de_siempre(self):
        eng = engine([item("FV24-0427095", cents=1000)], by_num={"FV240427095": {"FV24-0427095"}})
        t = movement("FRA FV24-0427095", 1000)
        t.tokens = {"FV240427095"}
        assert eng.m_refs(t)
        assert eng.settled == [(["FV24-0427095"], "ref")]

    def test_el_numero_completo_con_descuento_no_cambia_de_regla(self):
        eng = engine([item("FV24-0427095", cents=1000)], by_num={"FV240427095": {"FV24-0427095"}})
        t = movement("FRA FV24-0427095", 980)
        t.tokens = {"FV240427095"}
        assert eng.m_refs(t)
        assert eng.settled == [(["FV24-0427095"], "ref")]
