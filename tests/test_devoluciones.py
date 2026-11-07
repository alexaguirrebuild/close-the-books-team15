"""POLICIES §3 RETURNED: una devolución reabre las partidas de la operación. Estos tres casos salían siempre mal."""
from types import SimpleNamespace

import pytest

from bankrec import devoluciones


def partida(item_id="API-1", moneda="EUR"):
    return SimpleNamespace(id=item_id, ccy=moneda)


def remesa(e2e="PR010121101110-0001", documento="100.00", transferencia="100.00", beneficiario="VILA SOFTWARE", moneda="EUR", fecha="2021-10-11"):
    return {
        "end_to_end_id": e2e, "document_amount": documento, "transfer_amount": transferencia, "beneficiary": beneficiario,
        "currency": moneda, "execution_date": fecha,
    }


def motor(**campos):
    engine = SimpleNamespace(
        e2e_rows={}, transfers_by_amount={}, cheque_items={}, card_items={}, emitidas=[],
        run_doc_item=lambda r, t: partida(), ph=SimpleNamespace(fx=SimpleNamespace(conv=lambda c, *_: c * 2)),
    )
    engine.emit = lambda t, it, amt, exc, rule, ev="": engine.emitidas.append((it.id, amt, exc, rule))
    for nombre, valor in campos.items():
        setattr(engine, nombre, valor)
    return engine


def movimiento(texto, importe, e2e=None, cuenta="ES01-PENA-EUR"):
    return SimpleNamespace(up=texto, amount=importe, e2e=e2e, account=cuenta, date=None)


class TestTransferenciaDevuelta:
    @pytest.mark.parametrize(
        "texto",
        [
            "DEVOLUCION TRANSFERENCIA AC01 | VILA SOFTWARE S.COOP. | PR010121101110-0001",
            "RETURN AC01 VILA SOFTWARE PR010121101110-0001",
        ],
    )
    def test_reabre_las_partidas_de_la_transferencia_citada(self, texto):
        engine = motor(e2e_rows={"PR010121101110-0001": [remesa(), remesa(documento="50.00", transferencia="100.00")]})

        assert devoluciones.match(engine, movimiento(texto, 10000)) is True
        assert engine.emitidas == [("API-1", 10000, "RETURNED", "transfer_returned"), ("API-1", 5000, "RETURNED", "transfer_returned")]

    def test_el_e2e_se_repite_entre_divisas_y_manda_el_importe(self):
        propia, otra = remesa(transferencia="100.00"), remesa(documento="7.00", transferencia="7.00")
        engine = motor(e2e_rows={"PR010121101110-0001": [propia, otra]})

        devoluciones.match(engine, movimiento("RETURN AC01 X PR010121101110-0001", 10000))

        assert [amt for _, amt, _, _ in engine.emitidas] == [10000]

    def test_sin_e2e_en_el_texto_usa_el_del_banco(self):
        engine = motor(e2e_rows={"E2E-1": [remesa(e2e="E2E-1")]})

        assert devoluciones.match(engine, movimiento("RETURN AC01 FILTERS CABALLERO GMBH", 10000, e2e="E2E-1")) is True

    def test_sin_e2e_busca_por_cuenta_e_importe(self):
        engine = motor(e2e_rows={"E2E-1": [remesa(e2e="E2E-1")]}, transfers_by_amount={("ES01-PENA-EUR", 10000): ["E2E-1"]})

        assert devoluciones.match(engine, movimiento("RETURN AC01 NORTE PAGOS CONFIRMING EFC", 10000)) is True

    def test_si_no_encuentra_la_transferencia_no_emite_nada(self):
        engine = motor()

        assert devoluciones.match(engine, movimiento("DEVOLUCION TRANSFERENCIA AC01 | X", 10000)) is False
        assert engine.emitidas == []

    def test_la_partida_en_otra_divisa_se_convierte(self):
        engine = motor(e2e_rows={"E": [remesa(e2e="E", moneda="USD")]})

        devoluciones.match(engine, movimiento("RETURN AC01 X", 10000, e2e="E"))

        assert engine.emitidas[0][1] == 20000

    def test_un_cobro_cualquiera_no_es_una_devolucion(self):
        engine = motor(e2e_rows={"PR010121101110-0001": [remesa()]})

        assert devoluciones.match(engine, movimiento("TRANSFERENCIA DE CLIENTE PR010121101110-0001", 10000)) is False


class TestChequeDevuelto:
    @pytest.mark.parametrize("texto", ["RETURNED ITEM CHK 9709507 REFER TO MAKER", "CHEQUE IMPAYE 9709507 NSF"])
    def test_reabre_la_partida_que_liquido_ese_cheque(self, texto):
        engine = motor(cheque_items={"9709507": partida("ARI-7")})

        assert devoluciones.match(engine, movimiento(texto, -12345)) is True
        assert engine.emitidas == [("ARI-7", 12345, "RETURNED", "cheque_returned")]

    def test_un_cheque_que_no_se_imputo_no_emite_nada(self):
        assert devoluciones.match(motor(), movimiento("CHEQUE IMPAYE 111 NSF", -100)) is False


class TestContracargo:
    @pytest.mark.parametrize("texto", ["CHARGEBACK ****6791 MID 888565688", "RETROCESION CONTRACARGO TARJ ****6791 | COMERC 021899465"])
    def test_reabre_el_pedido_de_esa_tarjeta_e_importe(self, texto):
        engine = motor(card_items={("****6791", 5000): partida("ARI-9")})

        assert devoluciones.match(engine, movimiento(texto, -5000)) is True
        assert engine.emitidas == [("ARI-9", 5000, "RETURNED", "card_chargeback")]

    def test_otro_importe_no_es_ese_pedido(self):
        engine = motor(card_items={("****6791", 5000): partida("ARI-9")})

        assert devoluciones.match(engine, movimiento("CHARGEBACK ****6791 MID 1", -9999)) is False


def test_un_cargo_cualquiera_sigue_su_camino():
    assert devoluciones.match(motor(), movimiento("PAGO A PROVEEDOR", -100)) is False
