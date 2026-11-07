"""Citas incompletas: el resto del cobro solo con un conjunto determinista que cuadre exacto (POLICIES §5.5, §5.6, §2)."""
import datetime as dt
from collections import defaultdict
from types import SimpleNamespace

import pytest

from bankrec import citas_parciales as cp
from bankrec.data import Item
from bankrec.engine import Engine, Row

DAY = dt.date(2025, 6, 10)
CITED = "FV25-0001000"


def item(number, cents=1000, kind="ARI", party="C1", entity="ES01", issue=dt.date(2025, 5, 1), due=dt.date(2025, 6, 1),
         ccy="EUR", related=None, open_cents=None):
    return Item(id=number, kind=kind, side="AR", entity=entity, number=number, party=party, ccy=ccy, total=cents,
                open=cents if open_cents is None else open_cents, issue=issue, due=due, related=related)


def movement(amount, text=f"FRA {CITED}"):
    return SimpleNamespace(id="T1", amount=amount, entity="ES01", ccy="EUR", date=DAY, up=text, tokens={"FV250001000"},
                           orig_ccy=None, orig_amount=None, cp_iban=None)


def engine(items, inner_result=None):
    """Motor mínimo como en tests/test_completar.py, con _complete_cited original simulado."""
    cited = item(CITED, 1000)
    all_items = [cited, *items]
    eng = Engine.__new__(Engine)
    eng.items = {i.id: i for i in all_items}
    eng.ph = SimpleNamespace(children={}, by_party=defaultdict(list), by_num=defaultdict(set, {"FV250001000": {CITED}}),
                             by_suffix=defaultdict(set), fx=None)
    for i in all_items:
        eng.ph.by_party[i.party].append(i.id)
    eng.settled = []
    eng.settle = lambda t, ids, rule, ev="", party=None: eng.settled.append((sorted(ids), rule)) or True
    eng.resolve_parties = lambda t: (["C1"], "nombre")
    eng.scan_names = lambda t: []
    eng._complete_cited = lambda t, cited, party: inner_result
    cp.prepare(eng)
    return eng


def run(eng, amount):
    assert eng.m_refs(movement(amount))
    return eng.settled[0]


class TestCompleta:
    def test_todas_las_abiertas_cuadran(self):
        eng = engine([item("A", 300, due=dt.date(2025, 7, 1)), item("B", 700, due=dt.date(2025, 7, 15))])
        ids, rule = run(eng, 2000)
        assert (ids, rule) == (sorted([CITED, "A", "B"]), "ref_completed")
        assert eng.citas_parciales_rule == "todas"

    def test_todas_las_vencidas_cuadran_y_la_no_vencida_queda_fuera(self):
        eng = engine([item("A", 300, due=dt.date(2025, 6, 9)), item("B", 700, due=dt.date(2025, 6, 10)),
                      item("N", 555, due=dt.date(2025, 8, 1))])
        ids, rule = run(eng, 2000)
        assert (ids, rule) == (sorted([CITED, "A", "B"]), "ref_completed")

    def test_un_abono_resta(self):
        eng = engine([item("A", 900), item("AB", 200, kind="ARC", due=None)])
        ids, _ = run(eng, 1700)
        assert ids == sorted([CITED, "A", "AB"])

    def test_el_mismo_vencimiento_que_la_citada(self):
        eng = engine([item("A", 300, due=dt.date(2025, 6, 1)), item("B", 700, due=dt.date(2025, 6, 1)),
                      item("C", 400, due=dt.date(2025, 6, 5)), item("D", 50, due=dt.date(2025, 6, 7))])
        ids, _ = run(eng, 2000)
        assert ids == sorted([CITED, "A", "B"])
        assert eng.citas_parciales_rule == "mismo_vencimiento"

    def test_las_mas_antiguas_hasta_cuadrar(self):
        eng = engine([item("A", 300, issue=dt.date(2025, 3, 1), due=dt.date(2025, 4, 1)),
                      item("B", 700, issue=dt.date(2025, 4, 1), due=dt.date(2025, 5, 1)),
                      item("C", 400, issue=dt.date(2025, 5, 2), due=dt.date(2025, 6, 2)),
                      item("D", 50, issue=dt.date(2025, 5, 3), due=dt.date(2025, 6, 3))])
        assert cp.fifo(eng, movement(2000), [eng.items[CITED]], [eng.items[x] for x in "ABCD"]) == [eng.items["A"], eng.items["B"]]

    def test_el_abono_relacionado_con_la_citada(self):
        eng = engine([item("AB", 200, kind="ARC", due=None, related=CITED), item("AX", 200, kind="ARC", due=None, related="FV25-0009999")])
        picks = cp.abonos_relacionados(eng, movement(800), [eng.items[CITED]], [eng.items["AB"], eng.items["AX"]])
        assert [i.id for i in picks] == ["AB"]

    def test_lo_que_decide_el_motor_original_manda(self):
        extra = item("Z", 1000)
        eng = engine([extra, item("A", 500), item("B", 500)], inner_result=[extra])
        ids, rule = run(eng, 2000)
        assert (ids, rule) == (sorted([CITED, "Z"]), "ref_completed")


class TestNoFuerza:
    def test_si_ningun_conjunto_cuadra_se_queda_con_lo_citado(self):
        eng = engine([item("A", 400), item("B", 600), item("C", 55)])
        ids, rule = run(eng, 1500)
        assert (ids, rule) == ([CITED], "ref")

    def test_una_partida_suelta_que_cuadra_no_se_adivina(self):
        # B sola cuadra el resto, pero no es la más antigua ni un conjunto determinista: sería un subconjunto adivinado
        eng = engine([item("A", 300, issue=dt.date(2025, 5, 1)), item("B", 1000, issue=dt.date(2025, 5, 2)),
                      item("C", 700, issue=dt.date(2025, 5, 3))])
        ids, rule = run(eng, 2000)
        assert (ids, rule) == ([CITED], "ref")

    def test_la_partida_de_otra_sociedad_no_cuenta(self):
        eng = engine([item("A", 1000, entity="FR01")])
        ids, rule = run(eng, 2000)
        assert (ids, rule) == ([CITED], "ref")

    def test_la_partida_de_otro_cliente_no_cuenta(self):
        eng = engine([item("A", 1000, party="C2")])
        ids, rule = run(eng, 2000)
        assert (ids, rule) == ([CITED], "ref")

    def test_la_partida_ya_pagada_no_cuenta(self):
        eng = engine([item("A", 1000, open_cents=0), item("B", 1000)])
        ids, _ = run(eng, 2000)
        assert ids == sorted([CITED, "B"])

    def test_otra_divisa_no_se_completa(self):
        eng = engine([item("A", 1000, ccy="USD")])
        ids, rule = run(eng, 2000)
        assert (ids, rule) == ([CITED], "ref")

    def test_si_lo_citado_ya_cuadra_no_se_anade_nada(self):
        eng = engine([item("A", 1000)])
        ids, rule = run(eng, 1000)
        assert (ids, rule) == ([CITED], "ref")

    def test_un_pago_de_salida_no_se_completa(self):
        eng = engine([item("A", 300)])
        assert cp.completar(eng, movement(-1300), [eng.items[CITED]], "C1") is None

    def test_sin_contraparte_no_se_completa(self):
        eng = engine([item("A", 1000)])
        assert cp.completar(eng, movement(2000), [eng.items[CITED]], None) is None


class TestRobustez:
    def test_un_predicado_que_lanza_no_tumba_el_movimiento(self, monkeypatch):
        def roto(engine, t, cited, others):
            raise ValueError("boom")
        monkeypatch.setattr(cp, "PREDICADOS", [roto])
        eng = engine([item("A", 1000)])
        ids, rule = run(eng, 2000)
        assert (ids, rule) == ([CITED], "ref")

    def test_vencimiento_y_emision_vacios_no_rompen(self):
        eng = engine([item("A", 300, issue=None, due=None), item("B", 700, issue=None, due=None)])
        ids, _ = run(eng, 2000)
        assert ids == sorted([CITED, "A", "B"])

    def test_las_filas_completadas_llevan_el_predicado(self):
        eng = engine([item("A", 300, due=dt.date(2025, 7, 1)), item("B", 700, due=dt.date(2025, 7, 15))])
        eng.rows = []
        eng.settle = lambda t, ids, rule, ev="", party=None: [eng.rows.append(Row("T1", i, 1, "", "", rule, ev)) for i in ids] or True
        assert eng.m_refs(movement(2000))
        assert {r.rule for r in eng.rows} == {"ref_completed_cp"}
        assert all("citas_parciales:todas" in r.evidence for r in eng.rows)

    def test_match_no_emite_nada(self):
        eng = engine([item("A", 1000)])
        assert cp.match(eng, movement(2000)) is False
        assert eng.settled == []

    def test_prepare_sobre_un_motor_sin_complete_cited_avisa_y_sigue(self):
        eng = SimpleNamespace()
        cp.prepare(eng)  # no lanza


@pytest.mark.parametrize("pred", cp.PREDICADOS)
def test_cada_predicado_devuelve_lista_o_none(pred):
    eng = engine([item("A", 300), item("B", 700)])
    out = pred(eng, movement(2000), [eng.items[CITED]], [eng.items["A"], eng.items["B"]])
    assert out is None or isinstance(out, list)
