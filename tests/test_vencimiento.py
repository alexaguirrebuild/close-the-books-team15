"""Cobros de varias facturas sin citas: casi nadie paga antes de vencer, y eso acota las candidatas hasta que la suma identifica."""
import datetime as dt
from types import SimpleNamespace

from bankrec.data import Item
from bankrec.engine import Engine

DAY = dt.date(2025, 6, 10)
OVERDUE = DAY - dt.timedelta(days=20)
FUTURE = DAY + dt.timedelta(days=30)
LATE_ISSUE = dt.date(2025, 5, 20)
EARLY_ISSUE = dt.date(2025, 1, 1)


def item(item_id, cents, due=OVERDUE, party="C1", issue=LATE_ISSUE, kind="ARI"):
    return Item(id=item_id, kind=kind, side="AR", entity="ES01", number=item_id, party=party, ccy="EUR", total=cents, open=cents, issue=issue, due=due)


def not_yet_due(count=15, party="C1"):
    """Más antiguas y aún sin vencer: llenan las 14 que mira best_subset y el filtro de vencimiento las quita."""
    return [item(f"F{party}{n:02d}", 90000 + 1000 * n, due=FUTURE, party=party, issue=EARLY_ISSUE) for n in range(count)]


def engine(items, named=("C1",), scanned=()):
    eng = Engine.__new__(Engine)
    by_party = {}
    for it in items:
        by_party.setdefault(it.party, []).append(it.id)
    eng.items = {it.id: it for it in items}
    eng.ph = SimpleNamespace(children={}, by_party=by_party, fx=None, party_country={}, entity_country={})
    eng.discount_ok = lambda *_: None
    eng.settled = []
    eng.settle = lambda t, ids, rule, ev="", party=None: eng.settled.append((sorted(ids), rule)) or True
    eng.resolve_parties = lambda t: (list(named), "nombre" if named else "")
    eng.scan_names = lambda t: list(scanned)
    return eng


def movement(amount):
    return SimpleNamespace(id="T1", amount=amount, entity="ES01", ccy="EUR", date=DAY, up="ACH CREDIT PAGADOR PAYMENT", tokens=set(), orig_ccy=None, orig_amount=None)


class TestIdentifica:
    def test_las_vencidas_que_suman_el_cobro(self):
        eng = engine([*not_yet_due(), item("A", 3000), item("B", 5000), item("C", 11000)])
        assert eng.m_party_amount(movement(8000))
        assert eng.settled == [(["A", "B"], "party_due_subset")]

    def test_un_abono_sin_vencimiento_cuenta(self):
        credit = item("N", 1000, due=None, kind="ARC")
        eng = engine([*not_yet_due(), item("A", 3000), item("B", 5000), item("C", 11000), credit])
        assert eng.m_party_amount(movement(7000))
        assert eng.settled == [(["A", "B", "N"], "party_due_subset")]

    def test_el_ultimo_dia_de_margen_cuenta(self):
        almost = DAY + dt.timedelta(days=3)
        eng = engine([*not_yet_due(), item("A", 3000, due=almost), item("B", 5000)])
        assert eng.m_party_amount(movement(8000))
        assert eng.settled == [(["A", "B"], "party_due_subset")]

    def test_tambien_si_el_nombre_solo_lo_ve_scan_names(self):
        eng = engine([*not_yet_due(), item("A", 3000), item("B", 5000)], named=(), scanned=("C1",))
        assert eng.m_party_amount(movement(8000))
        assert eng.settled == [(["A", "B"], "party_due_subset")]


class TestNoFuerza:
    def test_un_dia_despues_del_margen_no_cuenta(self):
        later = DAY + dt.timedelta(days=4)
        eng = engine([*not_yet_due(), item("A", 3000, due=later), item("B", 5000)])
        assert not eng.m_party_amount(movement(8000))
        assert eng.settled == []

    def test_con_dos_soluciones_no_se_responde(self):
        eng = engine([*not_yet_due(), item("A", 3000), item("B", 5000), item("C", 5000)])
        assert not eng.m_party_amount(movement(8000))
        assert eng.settled == []

    def test_con_demasiadas_vencidas_no_se_busca(self):
        overdue = [item(f"V{n:02d}", 30000 + 100 * n) for n in range(27)]
        eng = engine(overdue)
        target = overdue[20].open + overdue[21].open
        assert not eng.m_party_amount(movement(target))

    def test_dos_clientes_con_solucion_no_se_responde(self):
        items = [*not_yet_due(), item("A", 3000), item("B", 5000), *not_yet_due(party="C2"), item("D", 3000, party="C2"), item("E", 5000, party="C2")]
        eng = engine(items, named=("C1", "C2"))
        assert not eng.m_party_amount(movement(8000))
        assert eng.settled == []

    def test_sin_cliente_no_se_busca(self):
        eng = engine([*not_yet_due(), item("A", 3000), item("B", 5000)], named=())
        assert not eng.m_party_amount(movement(8000))

    def test_un_pago_de_salida_no_se_identifica_asi(self):
        eng = engine([*not_yet_due(), item("A", 3000), item("B", 5000)])
        assert not eng.m_party_amount(movement(-8000))
        assert eng.settled == []

    def test_una_sola_factura_no_es_esta_regla(self):
        eng = engine([*not_yet_due(), item("A", 3000)])
        assert eng.m_party_amount(movement(3000))
        assert eng.settled[0][1] != "party_due_subset"
