"""Listas cortadas por el banco: el resto se busca por suma exacta, solo si la solución es única (POLICIES §5.5)."""
import datetime as dt
from collections import defaultdict
from types import SimpleNamespace

from bankrec.data import Item
from bankrec.engine import Engine
from bankrec.sumas import exact_subsets

DAY = dt.date(2025, 6, 10)
CITED = "FV24-0427095"


class TestExactSubsets:
    def test_encuentra_la_combinacion(self):
        assert [sorted(s) for s in exact_subsets([100, 250, 400], 500)] == [[0, 2]]

    def test_con_dos_soluciones_devuelve_las_dos(self):
        assert len(exact_subsets([100, 100, 300], 400)) == 2

    def test_respeta_el_limite(self):
        assert len(exact_subsets([1, 1, 1, 1], 2, tolerance=0, limit=3)) == 3

    def test_admite_la_tolerancia(self):
        assert exact_subsets([100], 102, tolerance=2) == [(0,)]
        assert exact_subsets([100], 103, tolerance=2) == []

    def test_admite_abonos_negativos(self):
        assert [sorted(s) for s in exact_subsets([500, -200, 900], 300)] == [[0, 1]]

    def test_la_combinacion_vacia_no_cuenta(self):
        assert exact_subsets([100], 0, tolerance=2) == []

    def test_sin_importes_no_hay_solucion(self):
        assert exact_subsets([], 100) == []


def item(number, cents=1000, kind="ARI", party="C1", due=None, entity="ES01"):
    return Item(id=number, kind=kind, side="AR", entity=entity, number=number, party=party, ccy="EUR", total=cents, open=cents, issue=dt.date(2025, 5, 1), due=due)


def engine(others):
    cited = item(CITED, 1000)
    items = [cited, *others]
    eng = Engine.__new__(Engine)
    eng.items = {i.id: i for i in items}
    eng.ph = SimpleNamespace(children={}, by_party={"C1": [i.id for i in items]}, by_num=defaultdict(set, {"FV240427095": {CITED}}), by_suffix=defaultdict(set), fx=None)
    eng.settled = []
    eng.settle = lambda t, ids, rule, ev="", party=None: eng.settled.append((sorted(ids), rule)) or True
    eng.resolve_parties = lambda t: (["C1"], "nombre")
    eng.scan_names = lambda t: []
    return eng


def movement(amount):
    return SimpleNamespace(id="T1", amount=amount, entity="ES01", ccy="EUR", date=DAY, up=f"FRA {CITED} 01", tokens={"FV240427095"}, orig_ccy=None, orig_amount=None)


class TestCompletar:
    def test_el_resto_con_solucion_unica_se_completa(self):
        eng = engine([item("A", 300), item("B", 700), item("C", 55)])
        assert eng.m_refs(movement(2000))
        assert eng.settled == [(sorted([CITED, "A", "B"]), "ref_completed")]

    def test_un_abono_puede_formar_parte_del_resto(self):
        eng = engine([item("A", 900), item("N", 100, kind="ARC"), item("C", 55)])
        assert eng.m_refs(movement(1800))
        assert eng.settled == [(sorted([CITED, "A", "N"]), "ref_completed")]

    def test_con_dos_soluciones_se_queda_con_lo_citado(self):
        eng = engine([item("A", 500), item("B", 500), item("C", 55)])
        assert eng.m_refs(movement(1500))
        assert eng.settled == [([CITED], "ref")]

    def test_sin_solucion_se_queda_con_lo_citado(self):
        eng = engine([item("A", 300)])
        assert eng.m_refs(movement(2000))
        assert eng.settled == [([CITED], "ref")]

    def test_con_demasiadas_candidatas_no_se_busca(self):
        others = [item(f"I{n:02d}", 300 + n) for n in range(19)]
        eng = engine(others)
        assert eng.m_refs(movement(1000 + others[0].open + others[1].open))
        assert eng.settled == [([CITED], "ref")]

    def test_con_las_candidatas_justas_si_se_busca(self):
        others = [item(f"I{n:02d}", 7000 + 100 * n) for n in range(18)]
        eng = engine(others)
        assert eng.m_refs(movement(1000 + others[0].open))
        assert eng.settled == [(sorted([CITED, "I00"]), "ref_completed")]

    def test_si_lo_citado_ya_llega_no_se_busca_nada(self):
        eng = engine([item("A", 300)])
        assert eng.m_refs(movement(1000))
        assert eng.settled[0][0] == [CITED]
        assert eng.settled[0][1] != "ref_completed"

    def test_un_pago_de_salida_no_se_completa(self):
        eng = engine([item("A", 300)])
        t = movement(-1300)
        assert eng.m_refs(t) in (True, False)
        assert all(rule != "ref_completed" for _, rule in eng.settled)


PAST_DUE = DAY - dt.timedelta(days=5)
NOT_DUE = DAY + dt.timedelta(days=30)


def fillers(count, due=PAST_DUE, start=0):
    """Partidas con importes grandes y separados: ninguna suma casual llega al resto que se busca."""
    return [item(f"R{n + start:02d}", 900000 + 7001 * n, due=due) for n in range(count)]


class TestCompletarConVencidas:
    """Si la búsqueda general no da respuesta, se prueba solo con facturas vencidas, que son las que un cliente paga."""

    def test_un_abono_que_da_otra_solucion_no_impide_completar(self):
        others = [item("A", 300, due=PAST_DUE), item("B", 500, due=PAST_DUE), item("N", 200, kind="ARC"), item("Z", 1000, due=NOT_DUE)]
        eng = engine(others)  # A+B y Z-N suman lo mismo: la búsqueda general no decide
        assert eng.m_refs(movement(1800))
        assert eng.settled == [(sorted([CITED, "A", "B"]), "ref_completed")]

    def test_mas_de_18_partidas_pero_pocas_vencidas(self):
        eng = engine([item("A", 300, due=PAST_DUE), *fillers(25, due=NOT_DUE)])
        assert eng.m_refs(movement(1300))
        assert eng.settled == [(sorted([CITED, "A"]), "ref_completed")]

    def test_hasta_26_vencidas_si_se_busca(self):
        eng = engine([item("A", 300, due=PAST_DUE), *fillers(25)])
        assert eng.m_refs(movement(1300))
        assert eng.settled == [(sorted([CITED, "A"]), "ref_completed")]

    def test_mas_de_26_vencidas_no_se_busca(self):
        eng = engine([item("A", 300, due=PAST_DUE), *fillers(26)])
        assert eng.m_refs(movement(1300))
        assert eng.settled == [([CITED], "ref")]

    def test_dos_soluciones_entre_las_vencidas_se_queda_con_lo_citado(self):
        eng = engine([item("A", 300, due=PAST_DUE), item("B", 300, due=PAST_DUE), *fillers(20)])
        assert eng.m_refs(movement(1300))
        assert eng.settled == [([CITED], "ref")]

    def test_la_que_completa_aun_no_ha_vencido(self):
        eng = engine([item("A", 300, due=NOT_DUE), *fillers(20)])
        assert eng.m_refs(movement(1300))
        assert eng.settled == [([CITED], "ref")]

    def test_vence_dentro_del_margen(self):
        eng = engine([item("A", 300, due=DAY + dt.timedelta(days=3)), *fillers(20)])
        assert eng.m_refs(movement(1300))
        assert eng.settled == [(sorted([CITED, "A"]), "ref_completed")]

    def test_solo_cuadra_con_un_abono(self):
        eng = engine([item("N", 300, kind="ARC", due=PAST_DUE), item("A", 600, due=PAST_DUE), *fillers(20)])
        assert eng.m_refs(movement(1300))
        assert eng.settled == [([CITED], "ref")]

    def test_otra_sociedad_no_cuenta(self):
        eng = engine([item("A", 300, due=PAST_DUE, entity="FR01"), *fillers(20)])
        assert eng.m_refs(movement(1300))
        assert eng.settled == [([CITED], "ref")]

    def test_un_exceso_pequeno_sobre_una_factura_citada_es_sobrepago(self):
        eng = engine([item("A", 50, due=PAST_DUE), *fillers(20)])
        assert eng.m_refs(movement(1050))
        assert eng.settled == [([CITED], "ref")]

    def test_con_dos_citadas_un_exceso_pequeno_si_se_completa(self):
        eng = engine([item("OTRA", 1000), item("A", 100, due=PAST_DUE), *fillers(20)])
        eng.ph.by_num["FV240427096"] = {"OTRA"}
        t = movement(2100)
        t.tokens = {"FV240427095", "FV240427096"}
        assert eng.m_refs(t)
        assert eng.settled == [(sorted([CITED, "OTRA", "A"]), "ref_completed")]

    def test_una_vencida_ya_pagada_no_completa(self):
        pagada = item("A", 300, due=PAST_DUE)
        pagada.open = 0
        eng = engine([pagada, *fillers(20)])
        assert eng.m_refs(movement(1300))
        assert eng.settled == [([CITED], "ref")]

    def test_ninguna_combinacion_de_vencidas_suma_lo_que_falta(self):
        eng = engine([item("A", 300, due=PAST_DUE), item("B", 450, due=PAST_DUE), *fillers(20)])
        assert eng.m_refs(movement(1400))
        assert eng.settled == [([CITED], "ref")]
