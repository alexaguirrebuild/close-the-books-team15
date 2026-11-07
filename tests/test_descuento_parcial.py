"""POLICIES §3 SHORT_DISCOUNT: en un cobro de varias facturas el descuento puede aplicarse solo a las que llegan a tiempo."""
import datetime as dt
from collections import defaultdict
from types import SimpleNamespace

from bankrec.engine import Engine

TERMS = {"T10": {"early_payment_discount_pct": "2", "discount_days": "10"}, "T30": {}}


def motor(*items) -> Engine:
    engine = Engine.__new__(Engine)
    engine.ph = SimpleNamespace(terms=TERMS, party_country={}, fx=None)
    engine.items = {i.id: i for i in items}
    engine.rows = []
    engine.settled_by = defaultdict(list)
    engine.iban_learned = {}
    return engine


def factura(item_id, total, terms, issue):
    return SimpleNamespace(id=item_id, kind="ARI", side="AR", ccy="EUR", total=total, open=total, terms=terms,
                           issue=issue, party="C1", entity="E1")


def cobro(importe, fecha):
    return SimpleNamespace(id="T1", amount=importe, ccy="EUR", date=fecha, orig_ccy=None, orig_amount=None, cp_iban=None)


def test_descuento_solo_en_la_factura_a_tiempo():
    a = factura("A", 100000, "T10", dt.date(2025, 3, 1))   # a tiempo: 98.000
    b = factura("B", 50000, "T10", dt.date(2025, 1, 1))    # fuera de plazo: 50.000 completo
    motor_ = motor(a, b)
    assert motor_.settle(cobro(98000 + 50000, dt.date(2025, 3, 5)), ["A", "B"], "ref")
    filas = {r.item: (r.amount, r.exception) for r in motor_.rows}
    assert filas == {"A": (100000, "SHORT_DISCOUNT"), "B": (50000, "")}


def test_sin_solucion_unica_no_inventa_descuento():
    a = factura("A", 100000, "T10", dt.date(2025, 3, 1))
    b = factura("B", 100000, "T10", dt.date(2025, 3, 1))
    motor_ = motor(a, b)
    motor_.settle(cobro(98000 + 100000, dt.date(2025, 3, 5)), ["A", "B"], "ref")
    assert "SHORT_DISCOUNT" not in {r.exception for r in motor_.rows}
