"""end_to_end_id no es único: la secuencia se reinicia por divisa, y un mismo id cubre a varios beneficiarios.

Antes se emitían todas las filas del id. BT-2025-0039715, pago de 33.680,54 USD a un proveedor, llevaba además 3 facturas en
BRL de otro. En el holdout eran unas 3.900 filas falsas, casi todas FX_DIFFERENCE.
"""
from collections import defaultdict
from types import SimpleNamespace

from bankrec.engine import Engine

CUENTA = "AU01-WATT-USD"


def fila(beneficiario, moneda, transferencia, documento="1.00", e2e="PR010025021344-0003", cuenta=CUENTA, tipo="transfers"):
    return {
        "run_id": e2e.split("-")[0], "run_type": tipo, "end_to_end_id": e2e, "beneficiary": beneficiario, "currency": moneda,
        "transfer_amount": transferencia, "document_number": documento, "debtor_account_id": cuenta, "execution_date": "2025-02-13",
    }


PROPIA = [fila("CP5 Plastics Ltd", "USD", "33680.54", "A1"), fila("CP5 Plastics Ltd", "USD", "33680.54", "A2")]
AJENA = [fila("Software Marin", "BRL", "31787.76", "F1"), fila("Software Marin", "BRL", "31787.76", "F2")]


def motor(*filas):
    engine = Engine.__new__(Engine)
    engine.run_rows = defaultdict(list)
    engine.e2e_rows = defaultdict(list)
    for row in filas:
        engine.run_rows[row["run_id"]].append(row)
        engine.e2e_rows[row["end_to_end_id"]].append(row)
    engine.transfers_by_amount = engine._index_transfers()
    engine.used_batches = set()
    engine.emitidas = []
    engine.emit_run_rows = lambda t, rs, rule, ev: engine.emitidas.append((list(rs), rule))
    return engine


def cargo(importe=-3368054, e2e="PR010025021344-0003", texto="SEPA-UEBERWEISUNG AN CP5 PLASTICS LTD", original=None, moneda_original=None):
    return SimpleNamespace(amount=importe, e2e=e2e, up=texto, account=CUENTA, ccy="USD", orig_amount=original, orig_ccy=moneda_original, date=__import__("datetime").date(2025, 2, 14))


def test_el_e2e_del_banco_solo_liquida_la_transferencia_de_ese_importe():
    engine = motor(*PROPIA, *AJENA)

    assert engine.m_payment_run(cargo()) is True
    assert engine.emitidas == [(PROPIA, "payment_run_e2e")]


def test_con_un_solo_beneficiario_no_cambia_nada():
    engine = motor(*PROPIA)

    engine.m_payment_run(cargo(importe=-1))

    assert engine.emitidas == [(PROPIA, "payment_run_e2e")]


def test_con_divisa_distinta_manda_el_importe_original():
    engine = motor(*PROPIA, *AJENA)

    engine.m_payment_run(cargo(importe=-999, original=3178776, moneda_original="BRL", texto="PAGO"))

    assert engine.emitidas[0][0] == AJENA


def test_sin_importe_que_cuadre_manda_el_nombre_del_texto():
    engine = motor(*PROPIA, *AJENA)

    engine.m_payment_run(cargo(importe=-999, texto="PAGO A SOFTWARE MARIN"))

    assert engine.emitidas[0][0] == AJENA


def test_por_id_de_remesa_cada_transferencia_es_la_suya_aunque_compartan_e2e():
    engine = motor(*PROPIA, *AJENA)
    texto = "TRANSFERENCIA PR010025021344"

    assert engine.m_payment_run(cargo(importe=-3178776, e2e=None, texto=texto)) is True
    assert engine.m_payment_run(cargo(importe=-3368054, e2e=None, texto=texto)) is True
    assert [rs for rs, _ in engine.emitidas] == [AJENA, PROPIA]


def test_por_importe_encuentra_tambien_la_segunda_transferencia_del_e2e():
    engine = motor(*PROPIA, *AJENA)

    assert engine.m_payment_run(cargo(importe=-3178776, e2e=None, texto="SIN REFERENCIA")) is True
    assert engine.emitidas == [(AJENA, "payment_run_amount")]


def test_una_transferencia_no_se_liquida_dos_veces():
    engine = motor(*PROPIA)
    sin_e2e = cargo(e2e=None, texto="SIN REFERENCIA")

    assert engine.m_payment_run(sin_e2e) is True
    assert engine.m_payment_run(sin_e2e) is False
