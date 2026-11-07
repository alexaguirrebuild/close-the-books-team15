"""Plugin cobros_cortos (lote 4): sin reglas activas no cambia nada, y un fallo de una regla no tumba la fase."""
import datetime as dt
from collections import defaultdict
from types import SimpleNamespace

from bankrec import cobros_cortos
from bankrec.data import Item
from bankrec.engine import Engine

DAY = dt.date(2025, 6, 10)


def invoice(item_id="A", cents=10000):
    return Item(id=item_id, kind="ARI", side="AR", entity="ES01", number=item_id, party="C1", ccy="EUR", total=cents, open=cents,
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
    return eng


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
