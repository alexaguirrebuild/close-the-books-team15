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
MAY = dt.date(2025, 5, 1)


def item(number, cents=1000, kind="ARI", party="C1", entity="ES01", issue=MAY, due=dt.date(2025, 6, 1), ccy="EUR", open_cents=None):
    return Item(id=number, kind=kind, side="AR", entity=entity, number=number, party=party, ccy=ccy, total=cents,
                open=cents if open_cents is None else open_cents, issue=issue, due=due)


def movement(amount, text=f"FRA {CITED}", rmt=""):
    return SimpleNamespace(id="T1", amount=amount, entity="ES01", ccy="EUR", date=DAY, up=text.upper(), text=text, ref1="",
                           ref2="", rmt=rmt, tokens={"FV250001000"}, orig_ccy=None, orig_amount=None, cp_iban=None)


def engine(items, inner_result=None):
    """Motor mínimo como en tests/test_completar.py, con el `_complete_cited` original simulado."""
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


def run(eng, amount, **kw):
    assert eng.m_refs(movement(amount, **kw))
    return eng.settled[0]


def others(eng, *ids):
    return [eng.items[i] for i in ids]


class TestCompleta:
    def test_las_mas_antiguas_hasta_cuadrar(self):
        eng = engine([item("A", 300, issue=dt.date(2025, 3, 1)), item("B", 700, issue=dt.date(2025, 4, 1)),
                      item("C", 400, issue=dt.date(2025, 5, 2)), item("D", 50, issue=dt.date(2025, 5, 3))])
        assert [i.id for i in cp.fifo(eng, movement(2000), [eng.items[CITED]], others(eng, "A", "B", "C", "D"))] == ["A", "B"]
        ids, rule = run(eng, 2000)  # fifo ya no se aplica; con 4 candidatas lo resuelve el subconjunto pequeño
        assert (ids, rule) == (sorted([CITED, "A", "B"]), "ref_completed")
        assert eng.citas_parciales_rule == "subconjunto_pequeno"

    def test_numero_con_una_cifra_cambiada_en_el_texto(self):
        eng = engine([item("FV25-0001234", 300, issue=dt.date(2025, 5, 2)), item("FV25-0005678", 700, issue=dt.date(2025, 5, 3)),
                      item("FV25-0009999", 300, issue=dt.date(2025, 4, 1))])
        # 250001243 es el número sin prefijo con dos cifras transpuestas; 250005678 es el número sin prefijo
        ids, rule = run(eng, 2000, text=f"FRAS {CITED} 250001243 250005678")
        assert ids == sorted([CITED, "FV25-0001234", "FV25-0005678"])
        assert eng.citas_parciales_rule == "numeracion"

    def test_sufijo_de_siete_digitos_dentro_de_una_tira_larga(self):
        eng = engine([item("FV25-0001234", 1000, issue=dt.date(2025, 5, 2)), item("FV25-0005678", 1000, issue=dt.date(2025, 4, 1))])
        ids, _ = run(eng, 2000, text=f"PAGO {CITED} REF 990000001234")
        assert ids == sorted([CITED, "FV25-0001234"])
        assert eng.citas_parciales_rule == "numeracion"

    def test_un_abono_resta_en_el_subconjunto_pequeno(self):
        eng = engine([item("A", 900), item("AB", 200, kind="ARC", due=None)])
        ids, _ = run(eng, 1700)
        assert ids == sorted([CITED, "A", "AB"])
        assert eng.citas_parciales_rule == "subconjunto_pequeno"

    def test_emitidas_cerca_de_las_citadas(self):
        eng = engine([item("A", 300, issue=dt.date(2025, 5, 3)), item("B", 700, issue=dt.date(2025, 5, 20)),
                      item("Z", 400, issue=dt.date(2025, 1, 10))])
        t = movement(2000)
        assert [i.id for i in cp.emitidas_cerca(30)(eng, t, [eng.items[CITED]], others(eng, "Z", "A", "B"))] == ["A", "B"]

    def test_misma_semana_de_vencimiento(self):
        # la citada vence el domingo 1-jun (semana ISO 22): A y B vencen esa misma semana, Z no
        eng = engine([item("A", 300, due=dt.date(2025, 5, 27)), item("B", 700, due=dt.date(2025, 5, 30)),
                      item("Z", 400, due=dt.date(2025, 7, 1))])
        t = movement(2000)
        assert [i.id for i in cp.misma_semana_vencimiento(eng, t, [eng.items[CITED]], others(eng, "A", "B", "Z"))] == ["A", "B"]

    def test_vencidas_recientes_excluye_las_antiguas(self):
        eng = engine([item("A", 300, due=dt.date(2025, 6, 1)), item("V", 700, issue=dt.date(2023, 1, 1), due=dt.date(2023, 2, 1)),
                      item("N", 400, due=dt.date(2025, 9, 1))])
        t = movement(2000)
        assert [i.id for i in cp.vencidas_recientes(eng, t, [eng.items[CITED]], others(eng, "V", "A", "N"))] == ["A"]

    def test_fifo_con_salto_salta_la_que_no_cabe(self):
        eng = engine([item("A", 300, issue=dt.date(2025, 3, 1)), item("G", 5000, issue=dt.date(2025, 4, 1)),
                      item("B", 700, issue=dt.date(2025, 5, 2))])
        t = movement(2000)
        assert [i.id for i in cp.fifo_con_salto(eng, t, [eng.items[CITED]], others(eng, "A", "G", "B"))] == ["A", "B"]

    def test_lo_que_decide_el_motor_original_manda(self):
        extra = item("Z", 1000)
        eng = engine([extra, item("A", 500), item("B", 500)], inner_result=[extra])
        ids, rule = run(eng, 2000)
        assert (ids, rule) == (sorted([CITED, "Z"]), "ref_completed")

    def test_las_filas_completadas_llevan_el_predicado(self):
        eng = engine([item("A", 300, issue=dt.date(2025, 3, 1)), item("B", 700, issue=dt.date(2025, 4, 1))])
        eng.rows = []
        eng.settle = lambda t, ids, rule, ev="", party=None: [eng.rows.append(Row("T1", i, 1, "", "", rule, ev)) for i in ids] or True
        assert eng.m_refs(movement(2000))
        assert {r.rule for r in eng.rows} == {"ref_completed_cp"}
        assert all("citas_parciales:subconjunto_pequeno" in r.evidence for r in eng.rows)


class TestNoFuerza:
    def test_si_ningun_conjunto_cuadra_se_queda_con_lo_citado(self):
        eng = engine([item("A", 400, issue=dt.date(2025, 5, 2)), item("B", 600, issue=dt.date(2025, 5, 3)),
                      item("C", 55, issue=dt.date(2025, 5, 4))] + [item(f"R{n}", 1000 + n, issue=dt.date(2025, 1, 1)) for n in range(9)])
        ids, rule = run(eng, 1500)
        assert (ids, rule) == ([CITED], "ref")

    def test_el_subconjunto_unico_no_vale_con_mas_de_ocho_candidatas(self):
        # B sola cuadra, pero hay más de 8 candidatas: la unicidad no identifica
        eng = engine([item("A", 300, issue=dt.date(2025, 5, 2)), item("B", 1000, issue=dt.date(2025, 5, 3)),
                      item("C", 700, issue=dt.date(2025, 5, 4))] + [item(f"R{n}", 2000 + n, issue=dt.date(2025, 1, 1)) for n in range(7)])
        t = movement(2000)
        cands = others(eng, *[f"R{n}" for n in range(7)], "A", "B", "C")
        assert cp.subconjunto_pequeno(eng, t, [eng.items[CITED]], cands) is None
        assert cp.subconjunto_pequeno(eng, t, [eng.items[CITED]], cands[7:]) is None  # 3 candidatas y dos soluciones (B; A+C)

    def test_fifo_con_salto_no_dispara_si_no_cierra(self):
        eng = engine([item("A", 300, issue=dt.date(2025, 5, 2)), item("B", 600, issue=dt.date(2025, 5, 3))])
        assert cp.fifo_con_salto(eng, movement(2000), [eng.items[CITED]], others(eng, "A", "B")) is None

    def test_todos_los_abonos_abiertos_no_se_arrastran(self):
        # un par abono+factura antiguos que se anulan: con FIFO solo de facturas no entran
        eng = engine([item("V", 500, issue=dt.date(2021, 1, 1)), item("VC", 500, kind="ARC", issue=dt.date(2021, 2, 1), due=None),
                      item("A", 1000, issue=dt.date(2025, 5, 2))] + [item(f"R{n}", 3000 + n, issue=dt.date(2025, 5, 9)) for n in range(8)])
        ids, _ = run(eng, 2000)
        assert "VC" not in ids and "V" not in ids

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

    def test_el_numero_de_una_citada_no_vuelve_a_casar(self):
        # el texto solo trae la citada: la numeración no debe adjudicar otra factura parecida
        eng = engine([item("FV25-0001001", 1000, issue=dt.date(2025, 5, 2))])
        assert cp.numeracion(eng, movement(2000, text=f"FRA {CITED}"), [eng.items[CITED]], others(eng, "FV25-0001001")) is None


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

    def test_sin_texto_ni_referencias_no_rompe(self):
        eng = engine([item("A", 1000, issue=dt.date(2025, 5, 2))])
        t = movement(2000, text="")
        t.rmt = None
        assert cp.completar(eng, t, [eng.items[CITED]], "C1") is not None

    def test_match_no_emite_nada(self):
        eng = engine([item("A", 1000)])
        assert cp.match(eng, movement(2000)) is False
        assert eng.settled == []

    def test_prepare_sobre_un_motor_sin_complete_cited_avisa_y_sigue(self):
        cp.prepare(SimpleNamespace())  # no lanza


@pytest.mark.parametrize("pred", cp.PREDICADOS)
def test_cada_predicado_devuelve_lista_o_none(pred):
    eng = engine([item("A", 300), item("B", 700)])
    out = pred(eng, movement(2000), [eng.items[CITED]], others(eng, "A", "B"))
    assert out is None or isinstance(out, list)
