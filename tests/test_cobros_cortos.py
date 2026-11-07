"""Plugin cobros_cortos (lote 4): sin reglas activas no cambia nada, y un fallo de una regla no tumba la fase."""
import datetime as dt
from collections import defaultdict
from types import SimpleNamespace

from bankrec import cobros_cortos
from bankrec.data import Item
from bankrec.engine import Engine

DAY = dt.date(2025, 6, 10)


def invoice(item_id="A", cents=10000, kind="ARI", ccy="EUR"):
    return Item(id=item_id, kind=kind, side="AR", entity="ES01", number=item_id, party="C1", ccy=ccy, total=cents, open=cents,
                issue=dt.date(2025, 5, 1))


def movement(amount=10000):
    return SimpleNamespace(id="T1", amount=amount, entity="ES01", ccy="EUR", date=DAY, up="", orig_ccy=None, orig_amount=None,
                           cp_iban="ES0000000000000000000001")


def engine(items):
    eng = Engine.__new__(Engine)
    eng.items = {item.id: item for item in items}
    eng.rows = []
    eng.settled_by = defaultdict(list)
    eng.iban_learned = {}
    eng.ph = SimpleNamespace(fx=SimpleNamespace(conv=lambda cents, a, b, d: cents), terms={}, party_country={}, entity_country={})
    return eng


def settled(eng, cash, ids):
    cobros_cortos.prepare(eng)
    assert eng.settle(movement(cash), ids, "ref")
    return [(r.item, r.amount, r.exception) for r in eng.rows]


def test_sin_reglas_match_no_emite_nada():
    eng = engine([invoice()])
    cobros_cortos.prepare(eng)
    assert cobros_cortos.match(eng, movement()) is False
    assert eng.rows == []
    assert eng.items["A"].open == 10000


def test_un_fallo_deshace_lo_emitido_y_sigue():
    eng = engine([invoice()])
    t = movement()

    def regla_rota():
        eng.emit(t, eng.items["A"], 4000, "PARTIAL", "prueba")
        raise KeyError("columna que falta en FINAL")

    assert cobros_cortos.guarded(eng, "prueba", regla_rota, lambda: False) is False
    assert eng.rows == []
    assert eng.items["A"].open == 10000
    assert eng.settled_by["A"] == []
    assert eng.iban_learned == {}
    assert "emit" not in eng.__dict__  # el método original vuelve a su sitio


class TestSobrepagoConPatron:
    """POLICIES §3 OVERPAYMENT: una sola factura, cobro mayor que su saldo. En TRAIN el exceso es siempre +1, 2, 5, 10,
    50 o 100 unidades, o +1 % o +10 %: con esa forma se marca aunque supere el 10 % que usa settle."""

    def test_cien_unidades_de_mas_en_una_factura_pequena(self):
        assert settled(engine([invoice(cents=15549)]), 25549, ["A"]) == [("A", 15549, "OVERPAYMENT")]

    def test_un_uno_por_ciento_de_mas(self):
        assert settled(engine([invoice(cents=98163)]), 99145, ["A"]) == [("A", 98163, "OVERPAYMENT")]

    def test_un_diez_por_ciento_de_mas(self):
        assert settled(engine([invoice(cents=5450812)]), 5995893, ["A"]) == [("A", 5450812, "OVERPAYMENT")]


class TestSobrepagoNoFuerza:
    def test_un_exceso_sin_esa_forma_no_es_sobrepago(self):
        # 10308,65 cobrado 11186,85: dentro del 10 % de settle, pero el exceso no tiene la forma: falta otra factura
        assert settled(engine([invoice(cents=1030865)]), 1118685, ["A"]) == [("A", 1030865, "")]

    def test_un_centimo_de_mas_es_redondeo_no_sobrepago(self):
        assert settled(engine([invoice(cents=10000)]), 10001, ["A"]) == [("A", 10000, "")]

    def test_dos_facturas_no_son_un_sobrepago(self):
        rows = settled(engine([invoice("A", 6000), invoice("B", 3000)]), 10000, ["A", "B"])
        assert all(exc != "OVERPAYMENT" for _, _, exc in rows)

    def test_un_abono_en_el_cobro_sigue_como_hasta_ahora(self):
        rows = settled(engine([invoice("A", 6000), invoice("N", 1000, kind="ARC")]), 15000, ["A", "N"])
        assert ("A", 6000, "") in rows and ("N", 1000, "") in rows

    def test_otra_divisa_sigue_como_hasta_ahora(self):
        plain = engine([invoice(cents=15549, ccy="USD")])
        assert plain.settle(movement(25549), ["A"], "ref")
        rows = settled(engine([invoice(cents=15549, ccy="USD")]), 25549, ["A"])
        assert rows == [(r.item, r.amount, r.exception) for r in plain.rows]

    def test_un_pago_no_se_toca_en_settle(self):
        eng = engine([Item(id="P", kind="API", side="AP", entity="ES01", number="P", party="S1", ccy="EUR", total=15549, open=15549,
                           issue=dt.date(2025, 5, 1))])
        cobros_cortos.prepare(eng)
        assert eng.settle(movement(-25549), ["P"], "ref")
        assert [r.exception for r in eng.rows] == [""]
