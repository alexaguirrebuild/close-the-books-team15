"""Una remesa agregada es un solo cargo que liquida todos sus documentos (POLICIES §5.3).

Antes se exigía que la suma cuadrase, y estas remesas mezclan divisas: el cargo salía como BANK_FEES y sus partidas se
perdían. En el holdout eran 42 cargos y 43.151 filas.
"""
from collections import defaultdict
from types import SimpleNamespace

from bankrec.engine import Engine

CUENTA = "US01-HPNB-USD-2"


def fila(run_id="PR010125081490", cuenta=CUENTA, importe="10.00"):
    return {
        "run_id": run_id, "run_type": "aggregated", "debtor_account_id": cuenta, "execution_date": "2025-08-14",
        "end_to_end_id": f"{run_id}-1", "transfer_amount": importe,
    }


def motor(*filas):
    engine = Engine.__new__(Engine)
    engine.e2e_rows = {}
    engine.run_rows = defaultdict(list)
    for row in filas:
        engine.run_rows[row["run_id"]].append(row)
    engine.used_batches = set()
    engine.transfers_by_amount = {}
    engine.emitidas = []
    engine.emit_run_rows = lambda t, rs, rule, ev: engine.emitidas.append((rs, rule))
    return engine


def cargo(texto, cuenta=CUENTA, importe=-729577169):
    return SimpleNamespace(amount=importe, e2e=None, up=texto, account=cuenta)


def test_un_cargo_liquida_toda_la_remesa_aunque_la_suma_no_cuadre():
    filas = [fila(), fila()]
    engine = motor(*filas)

    assert engine.m_payment_run(cargo("BULK PAYMENT PR010125081490 458 TRANSFERS")) is True
    assert engine.emitidas == [(filas, "payment_run_aggregated")]


def test_encuentra_el_id_partido_por_el_banco():
    engine = motor(fila("PR010125071007"))

    assert engine.m_payment_run(cargo("TRANSF. EMITIDAS FICHERO C34 PR0101250 | 71007 | (272 ORD.)")) is True
    assert len(engine.emitidas) == 1


def test_solo_toma_los_documentos_de_la_cuenta_del_cargo():
    """El run_id se repite entre sociedades."""
    propia, ajena = fila(), fila(cuenta="ES01-RIBA-EUR")
    engine = motor(propia, ajena)

    engine.m_payment_run(cargo("BULK PAYMENT PR010125081490 458 TRANSFERS"))

    assert engine.emitidas[0][0] == [propia]


def test_una_remesa_de_otra_cuenta_no_se_toma_por_la_de_este_cargo():
    engine = motor(fila(cuenta="ES01-RIBA-EUR"))

    assert engine.m_payment_run(cargo("BULK PAYMENT PR010125081490 458 TRANSFERS")) is False


def test_no_se_liquida_dos_veces():
    engine = motor(fila())
    texto = "BULK PAYMENT PR010125081490 458 TRANSFERS"

    assert engine.m_payment_run(cargo(texto)) is True
    assert engine.m_payment_run(cargo(texto)) is False
    assert len(engine.emitidas) == 1


def test_una_referencia_del_banco_con_la_misma_forma_no_es_una_remesa():
    engine = motor(fila())

    assert engine.m_payment_run(cargo("CARGO LO250107123456")) is False
    assert engine.emitidas == []


def test_un_cobro_no_es_una_remesa_de_pago():
    engine = motor(fila())

    assert engine.m_payment_run(cargo("BULK PAYMENT PR010125081490", importe=100)) is False
