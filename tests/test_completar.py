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


def item(number, cents=1000, kind="ARI", party="C1"):
    return Item(id=number, kind=kind, side="AR", entity="ES01", number=number, party=party, ccy="EUR", total=cents, open=cents, issue=dt.date(2025, 5, 1))


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
