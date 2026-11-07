"""Recibo devuelto que el cliente vuelve a pagar por transferencia: no es pago duplicado (POLICIES §3 y §5.10, «re-presented direct debits»).

El texto del banco dice «RECIBO DEVUELTO …» o «PAGO RECIBO …»; la guarda del motor lo confundía con un adeudo por contener RECIBO.
"""
import datetime as dt
from collections import defaultdict
from types import SimpleNamespace

from bankrec import recibos_devueltos
from bankrec.data import Item
from bankrec.engine import Engine


def invoice(method="dd"):
    return Item(id="A1", kind="ARI", side="AR", entity="ES01", number="N1", party="C1", ccy="EUR", total=10000, open=0,
                issue=dt.date(2025, 1, 1), method=method)


def movement(up):
    return SimpleNamespace(id="T1", amount=10000, entity="ES01", ccy="EUR", date=dt.date(2025, 2, 1), up=up, cp_iban="")


def engine():
    eng = Engine.__new__(Engine)
    eng.rows, eng.settled_by, eng.iban_learned = [], defaultdict(list), {}
    recibos_devueltos.prepare(eng)
    return eng


def emitted(up, method="dd", exc="DUPLICATE_PAYMENT", rule="ref_duplicate"):
    eng = engine()
    eng.emit(movement(up), invoice(method), 10000, exc, rule, "ref N1")
    return eng.rows[0]


def test_recibo_devuelto_pagado_por_transferencia_no_es_duplicado():
    row = emitted("ACH CREDIT POOL SUPPLY CCD PAYMENT ID:88 | RECIBO DEVUELTO 1062427215")
    assert row.exception == "" and row.rule == "ref_represented_texto" and row.amount == 10000


def test_pago_recibo_tambien():
    assert emitted("SEPA-UEBERWEISUNG EREF+NOTPROVIDED SVWZ+PAGO RECIBO 117085").exception == ""


def test_sin_texto_de_recibo_devuelto_sigue_siendo_duplicado():
    assert emitted("TRANSFERENCIA DE CLIENTE FRA 117085").exception == "DUPLICATE_PAYMENT"


def test_si_la_factura_no_era_de_adeudo_no_se_toca():
    assert emitted("PAGO RECIBO 117085", method="transfer").exception == "DUPLICATE_PAYMENT"


def test_no_toca_otras_excepciones():
    assert emitted("RECIBO DEVUELTO 1", exc="PARTIAL", rule="ref").exception == "PARTIAL"
    assert emitted("RECIBO DEVUELTO 1", exc="", rule="ref").exception == ""


def test_el_resto_de_la_fila_no_cambia():
    eng = engine()
    it = invoice()
    eng.emit(movement("RECIBO DEVUELTO 9"), it, 10000, "DUPLICATE_PAYMENT", "ref_duplicate", "ref N1")
    assert (eng.rows[0].txn, eng.rows[0].item, eng.rows[0].evidence.startswith("ref N1")) == ("T1", "A1", True)
    assert it.open == -10000   # igual que antes: emit resta el importe
