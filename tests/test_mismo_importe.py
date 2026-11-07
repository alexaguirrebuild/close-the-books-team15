"""Mismo importe: qué partida se elige cuando el cobro no cita ninguna, y cuándo no se responde."""
import collections
import datetime as dt
from types import SimpleNamespace

import pytest

from bankrec import mismo_importe
from bankrec.data import Item
from bankrec.engine import DEFAULT_PLUGINS, Engine, load_plugins
from bankrec.pdfclauses import year_after

DAY = dt.date(2025, 6, 10)
CASH = 10000


def invoice(item_id, party="C1", cents=CASH, issue=dt.date(2025, 5, 1), kind="ARI", side="AR", entity="ES01", ccy="EUR"):
    return Item(id=item_id, kind=kind, side=side, entity=entity, number=item_id, party=party, ccy=ccy, total=cents, open=cents, issue=issue)


def credit_note(item_id, party="C1", cents=CASH, issue=dt.date(2025, 1, 1)):
    return invoice(item_id, party, cents, issue, kind="ARC")


def movement(amount=CASH, up=""):
    return SimpleNamespace(id="T1", amount=amount, entity="ES01", ccy="EUR", date=DAY, up=up, orig_ccy=None, orig_amount=None, cp_iban="")


def engine(items, named=("C1",), scanned=()):
    eng = Engine.__new__(Engine)
    by_party = {}
    for item in sorted(items, key=lambda i: (i.issue, i.id)):
        by_party.setdefault(item.party, []).append(item.id)
    eng.items = {item.id: item for item in items}
    eng.ph = SimpleNamespace(children={}, by_party=by_party, fx=None, txns=[])
    eng.settled = []
    eng.settle = lambda t, ids, rule, ev="", party=None: eng.settled.append((list(ids), rule)) or True
    eng.resolve_parties = lambda t: (list(named), "nombre" if named else "")
    eng.scan_names = lambda t: list(scanned)
    mismo_importe.prepare(eng)
    return eng


class TestAbonoSuelto:
    def test_entre_factura_y_abono_mas_antiguo_se_elige_la_factura(self):
        eng = engine([credit_note("ABONO"), invoice("FACTURA")])
        assert eng.m_party_amount(movement())
        assert eng.settled == [(["FACTURA"], "party_amount_nombre")]

    def test_si_solo_coincide_un_abono_no_se_responde(self):
        eng = engine([credit_note("ABONO"), invoice("OTRA", cents=7777)])
        assert not eng.m_party_amount(movement())
        assert eng.settled == []

    def test_si_solo_coincide_un_abono_vale_la_factura_neta_de_ese_abono(self):
        eng = engine([credit_note("ABONO"), invoice("FACTURA", cents=2 * CASH)])
        assert eng.m_party_amount(movement())
        assert sorted(eng.settled[0][0]) == ["ABONO", "FACTURA"]
        assert eng.settled[0][1] == "party_subset_nombre"

    def test_el_rescate_tampoco_liquida_un_abono_suelto(self):
        eng = engine([credit_note("ABONO", party="C2"), invoice("OTRA", party="C2", cents=7777)], named=[], scanned=["C2"])
        assert not eng.m_party_amount(movement())
        assert eng.settled == []

    def test_el_rescate_elige_la_factura_y_no_el_abono(self):
        eng = engine([credit_note("ABONO", party="C2"), invoice("FACTURA", party="C2")], named=["C1"], scanned=["C2"])
        assert eng.m_party_amount(movement())
        assert eng.settled == [(["FACTURA"], "party_rescue_exact")]

    def test_el_proveedor_que_devuelve_su_abono_sigue_igual(self):
        refund = invoice("APC1", party="S1", kind="APC", side="AP")
        eng = engine([refund], named=["S1"])
        assert eng.m_party_amount(movement())
        assert eng.settled == [(["APC1"], "party_amount_nombre")]

    def test_el_abono_pagado_al_cliente_por_transferencia_sigue_igual(self):
        eng = engine([credit_note("ABONO")])
        assert eng.m_party_amount(movement(-CASH))
        assert eng.settled == [(["ABONO"], "party_amount_nombre")]

    def test_dos_clientes_con_factura_exacta_en_el_rescate_no_se_fuerza(self):
        items = [invoice("A", party="C2"), invoice("B", party="C3")]
        eng = engine(items, named=[], scanned=["C2", "C3"])
        assert eng._party_rescue(movement(), []) is False
        assert eng.settled == []


class TestMesDeServicio:
    """Las cuotas se facturan a primeros del mes siguiente: el servicio de marzo se emite en abril (POLICIES §5.6)."""

    ISSUES = {"FEB": dt.date(2025, 2, 3), "MAR": dt.date(2025, 3, 3), "ABR": dt.date(2025, 4, 3)}

    def cuotas(self, party="C1"):
        return [invoice(name, party=party, issue=issue) for name, issue in self.ISSUES.items()]

    def elegida(self, up, items=None, **kwargs):
        eng = engine(items if items is not None else self.cuotas(), **kwargs)
        assert eng.m_party_amount(movement(up=up))
        return eng.settled[0][0]

    @pytest.mark.parametrize("text", ["TT IN JOHN SERVICE MARCH 2025", "SERVICIOS MARZO 2025", "MANT. MAR 25", "WARTUNG MAERZ 2025"])
    def test_el_mes_en_cualquier_idioma_elige_su_factura(self, text):
        assert self.elegida(text) == ["ABR"]

    def test_el_anio_del_texto_separa_dos_eneros(self):
        items = [invoice("ENE24", issue=dt.date(2024, 1, 3)), invoice("ENE25", issue=dt.date(2025, 1, 3))]
        assert self.elegida("/REMI/MANT. DIC 24", items) == ["ENE25"]

    def test_sin_anio_gana_la_mas_antigua_de_ese_mes(self):
        items = [invoice("JUN24", issue=dt.date(2024, 6, 3)), invoice("JUN25", issue=dt.date(2025, 6, 3)), invoice("ENE", issue=dt.date(2024, 1, 3))]
        assert self.elegida("SERVICIOS MAYO", items) == ["JUN24"]

    def test_enero_se_refiere_al_servicio_de_diciembre_del_anio_anterior(self):
        items = [invoice("DIC", issue=dt.date(2024, 12, 2)), invoice("ENE", issue=dt.date(2025, 1, 2))]
        assert self.elegida("SERVICE DECEMBER 2024", items) == ["ENE"]

    def test_el_mes_de_la_clausula_del_pdf_manda_sobre_la_fecha_de_emision(self):
        eng = engine(self.cuotas())
        eng.clauses = {"MAR": {"month": (2025, 3)}, "ABR": {"month": (2025, 4)}}
        assert eng.m_party_amount(movement(up="SERVICE MARCH 2025"))
        assert eng.settled[0][0] == ["MAR"]

    def test_tambien_elige_por_mes_en_el_rescate(self):
        assert self.elegida("SERVICE MARCH 2025", self.cuotas("C2"), named=["C1"], scanned=["C2"]) == ["ABR"]

    def test_sin_mes_gana_la_mas_antigua(self):
        assert self.elegida("TT IN JOHN ACCOUNT PAYMENT") == ["FEB"]

    def test_si_ninguna_es_de_ese_mes_gana_la_mas_antigua(self):
        assert self.elegida("SERVICE AUGUST 2025") == ["FEB"]

    def test_una_sola_factura_se_paga_aunque_el_mes_sea_otro(self):
        assert self.elegida("SERVICE AUGUST 2025", [invoice("UNICA")]) == ["UNICA"]

    @pytest.mark.parametrize("text", ["PAGO SEPA ABC", "MARTINEZ Y ASOCIADOS", "TT IN DECORA 25"])
    def test_lo_que_no_es_un_mes_no_elige(self, text):
        assert self.elegida(text) == ["FEB"]

    def test_un_nombre_que_parece_un_mes_no_tapa_el_mes_con_anio(self):
        items = [invoice("JUN", issue=dt.date(2024, 6, 3)), invoice("NOV", issue=dt.date(2024, 11, 3))]
        assert self.elegida("TT IN JOHN MAY SERVICE OCTOBER 2024", items) == ["NOV"]


def aviso(item_id, amount, exception=""):
    return {"bank_txn_id": "T1", "item_id": item_id, "amount": amount, "exception": exception, "advice_id": "A1"}


class TestAvisoConParcial:
    """La factura que queda abierta se paga al 40, 50 o 60 %: 56 de 56 avisos de ar_n_to_m en el holdout."""

    def preparado(self, lines, cash=7000, items=None):
        eng = Engine.__new__(Engine)
        known = items if items is not None else [invoice(line["item_id"], cents=int(round(float(line["amount"]) * 100))) for line in lines]
        eng.items = {item.id: item for item in known}
        eng.ph = SimpleNamespace(txns=[movement(cash)])
        eng.m2_rows = {"T1": lines}
        mismo_importe.prepare(eng)
        return eng

    def test_la_linea_parcial_lleva_lo_que_llego_y_su_excepcion(self):
        lines = [aviso("A", "40.00"), aviso("B", "60.00")]  # faltan 30: B al 50 %
        rows = self.preparado(lines).m2_rows["T1"]
        assert [(r["item_id"], r["amount"], r["exception"]) for r in rows] == [("A", "40.00", ""), ("B", "30.00", "PARTIAL")]

    def test_no_modifica_las_filas_originales(self):
        lines = [aviso("A", "40.00"), aviso("B", "60.00")]
        self.preparado(lines)
        assert lines == [aviso("A", "40.00"), aviso("B", "60.00")]

    def test_si_la_suma_es_el_cobro_no_cambia(self):
        lines = [aviso("A", "30.00"), aviso("B", "40.00")]
        assert self.preparado(lines).m2_rows["T1"] == lines

    def test_si_alguna_linea_trae_excepcion_no_cambia(self):
        lines = [aviso("A", "40.00", "SHORT_DISPUTE"), aviso("B", "60.00")]
        assert self.preparado(lines).m2_rows["T1"] == lines

    def test_otra_divisa_no_cambia(self):
        lines = [aviso("A", "40.00"), aviso("B", "60.00")]
        items = [invoice("A", cents=4000), invoice("B", cents=6000, ccy="USD")]
        assert self.preparado(lines, items=items).m2_rows["T1"] == lines

    def test_una_diferencia_que_no_es_esa_fraccion_no_cambia(self):
        lines = [aviso("A", "50.00"), aviso("B", "50.00")]  # falta el 2 %: es un descuento, no una parcial
        assert self.preparado(lines, cash=9800).m2_rows["T1"] == lines

    def test_dos_lineas_posibles_no_se_elige(self):
        lines = [aviso("A", "60.00"), aviso("B", "60.00")]
        assert self.preparado(lines, cash=9000).m2_rows["T1"] == lines

    def test_un_abono_del_aviso_cuenta_restando_y_nunca_es_la_parcial(self):
        lines = [aviso("A", "40.00"), aviso("B", "80.00"), aviso("ABONO", "20.00")]
        items = [invoice("A", cents=4000), invoice("B", cents=8000), credit_note("ABONO", cents=2000)]
        rows = self.preparado(lines, cash=6000, items=items).m2_rows["T1"]  # 100 menos 60 de cobro: faltan 40, B al 50 %
        assert [(r["item_id"], r["amount"], r["exception"]) for r in rows] == [("A", "40.00", ""), ("B", "40.00", "PARTIAL"), ("ABONO", "20.00", "")]

    def test_una_partida_desconocida_no_cambia(self):
        lines = [aviso("A", "40.00"), aviso("B", "60.00")]
        assert self.preparado(lines, items=[invoice("A", cents=4000)]).m2_rows["T1"] == lines

    def test_sin_avisos_cargados_no_falla(self):
        eng = Engine.__new__(Engine)
        eng.items, eng.ph = {}, SimpleNamespace(txns=[])
        mismo_importe.prepare(eng)
        assert not hasattr(eng, "m2_rows")

    def test_una_factura_de_proveedor_en_un_cobro_no_tumba_el_motor(self):
        lines = [aviso("A", "40.00"), aviso("API1", "60.00")]
        items = [invoice("A", cents=4000), invoice("API1", cents=6000, kind="API", side="AP")]
        assert self.preparado(lines, items=items).m2_rows["T1"] == lines

    def test_en_un_cargo_no_cambia(self):
        lines = [aviso("API1", "40.00"), aviso("API2", "60.00")]
        items = [invoice("API1", cents=4000, kind="API", side="AP"), invoice("API2", cents=6000, kind="API", side="AP")]
        assert self.preparado(lines, cash=-7000, items=items).m2_rows["T1"] == lines


def motor_real(items):
    """Con settle y emit de verdad: lo que se comprueba son las filas que salen."""
    eng = Engine.__new__(Engine)
    by_party = {}
    for item in sorted(items, key=lambda i: (i.issue, i.id)):
        by_party.setdefault(item.party, []).append(item.id)
    eng.items = {item.id: item for item in items}
    eng.ph = SimpleNamespace(children={}, by_party=by_party, fx=None, terms={}, txns=[])
    eng.rows, eng.iban_learned = [], {}
    eng.settled_by = collections.defaultdict(list)
    eng.resolve_parties = lambda t: (["C1"], "nombre")
    eng.scan_names = lambda t: []
    mismo_importe.prepare(eng)
    return eng


def filas(eng):
    return sorted((row.item, row.amount, row.exception) for row in eng.rows)


class TestParcialAlLiquidar:
    """Con el conjunto ya elegido: la parcial es la factura que encaja en la fracción, no la más nueva."""

    def test_la_parcial_no_tiene_por_que_ser_la_mas_nueva(self):
        items = [invoice("ANTIGUA", cents=8000, issue=dt.date(2025, 1, 1)), invoice("NUEVA", cents=3000, issue=dt.date(2025, 5, 1))]
        eng = motor_real(items)
        assert eng.settle(movement(7000), ["ANTIGUA", "NUEVA"], "ref")  # faltan 40: ANTIGUA al 50 %
        assert filas(eng) == [("ANTIGUA", 4000, "PARTIAL"), ("NUEVA", 3000, "")]
        assert items[0].open == 4000 and items[1].open == 0

    def test_un_abono_citado_se_descuenta_y_lleva_su_fila(self):
        items = [invoice("ANTIGUA", cents=8000, issue=dt.date(2025, 1, 1)), invoice("NUEVA", cents=4000, issue=dt.date(2025, 5, 1)), credit_note("ABONO", cents=2000)]
        eng = motor_real(items)
        assert eng.settle(movement(6000), ["ANTIGUA", "NUEVA", "ABONO"], "ref")  # 100 menos 60: ANTIGUA al 50 %
        assert filas(eng) == [("ABONO", 2000, ""), ("ANTIGUA", 4000, "PARTIAL"), ("NUEVA", 4000, "")]

    def test_si_dos_encajan_sigue_la_regla_de_siempre(self):
        items = [invoice("A", cents=6000, issue=dt.date(2025, 1, 1)), invoice("B", cents=6000, issue=dt.date(2025, 5, 1))]
        eng = motor_real(items)
        assert eng.settle(movement(9000), ["A", "B"], "ref")
        assert filas(eng) == [("A", 6000, ""), ("B", 3000, "PARTIAL")]

    def test_si_ninguna_encaja_sigue_la_regla_de_siempre(self):
        items = [invoice("A", cents=6000, issue=dt.date(2025, 1, 1)), invoice("B", cents=6000, issue=dt.date(2025, 5, 1))]
        eng = motor_real(items)
        assert eng.settle(movement(11000), ["A", "B"], "ref")
        assert filas(eng) == [("A", 6000, ""), ("B", 5000, "PARTIAL")]

    def test_un_abono_sin_citar_que_cubre_la_diferencia_gana_a_la_parcial(self):
        items = [invoice("A", cents=8000, issue=dt.date(2025, 1, 1)), invoice("B", cents=3000, issue=dt.date(2025, 5, 1)), credit_note("ABONO", cents=4000)]
        eng = motor_real(items)
        assert eng.settle(movement(7000), ["A", "B"], "ref", party="C1")
        assert filas(eng) == [("A", 8000, ""), ("ABONO", 4000, ""), ("B", 3000, "")]

    def test_si_el_importe_cuadra_no_hay_parcial(self):
        items = [invoice("A", cents=8000), invoice("B", cents=3000)]
        eng = motor_real(items)
        assert eng.settle(movement(11000), ["A", "B"], "ref")
        assert filas(eng) == [("A", 8000, ""), ("B", 3000, "")]

    def test_otra_divisa_sigue_la_regla_de_siempre(self):
        eng = spy_settle([invoice("A", cents=8000), invoice("B", cents=3000, ccy="USD")])
        assert eng.settle(movement(7000), ["A", "B"], "ref")
        assert eng.delegated == [["A", "B"]]

    def test_una_sola_factura_sigue_la_regla_de_siempre(self):
        eng = spy_settle([invoice("A", cents=8000)])
        assert eng.settle(movement(4000), ["A"], "ref")
        assert eng.delegated == [["A"]]

    def test_un_cargo_sigue_la_regla_de_siempre(self):
        eng = spy_settle([invoice("A", cents=8000, kind="API", side="AP"), invoice("B", cents=3000, kind="API", side="AP")])
        assert eng.settle(movement(-7000), ["A", "B"], "ref")
        assert eng.delegated == [["A", "B"]]


VENCIDA = dt.date(2025, 5, 30)


def vencida(item_id, cents, party="C1", due=VENCIDA, **kwargs):
    item = invoice(item_id, party=party, cents=cents, **kwargs)
    item.due = due
    return item


class TestSumaConParcial:
    """Cobro sin citas que no cuadra con facturas enteras: suma exacta con una de ellas al 40, 50 o 60 %."""

    def test_identifica_las_enteras_y_la_parcial(self):
        eng = motor_real([vencida("A", 10000), vencida("B", 7000), vencida("C", 3333)])
        assert eng.m_party_amount(movement(13500))  # A entera y B al 50 %
        assert filas(eng) == [("A", 10000, ""), ("B", 3500, "PARTIAL")]
        assert eng.rows[0].rule == "party_due_partial"

    def test_el_cobro_siguiente_cuadra_con_lo_que_quedo_abierto(self):
        eng = motor_real([vencida("A", 10000), vencida("B", 7000), vencida("C", 3333)])
        assert eng.m_party_amount(movement(13500))
        assert eng.m_party_amount(movement(3500))
        assert eng.items["B"].open == 0

    def test_dos_soluciones_no_se_responde(self):
        eng = motor_real([vencida("A", 10000), vencida("OTRA", 10000), vencida("B", 7000)])
        assert not eng.m_party_amount(movement(13500))
        assert eng.rows == []

    def test_si_nada_encaja_no_se_responde(self):
        eng = motor_real([vencida("A", 10000), vencida("B", 7000), vencida("C", 3333)])
        assert not eng.m_party_amount(movement(13777))
        assert eng.rows == []

    def test_una_factura_aun_no_vencida_no_cuenta(self):
        eng = motor_real([vencida("A", 10000), vencida("B", 7000, due=DAY + dt.timedelta(days=30)), vencida("C", 3333)])
        assert not eng.m_party_amount(movement(13500))

    def test_vence_dentro_del_margen_si_cuenta(self):
        eng = motor_real([vencida("A", 10000), vencida("B", 7000, due=DAY + dt.timedelta(days=3)), vencida("C", 3333)])
        assert eng.m_party_amount(movement(13500))

    def test_una_sola_factura_al_50_no_basta(self):
        eng = motor_real([vencida("A", 10000), vencida("C", 3333)])
        assert not eng.m_party_amount(movement(5000))

    @pytest.mark.parametrize("candidates, answered", [(mismo_importe.MAX_PARTIAL_CANDIDATES, True), (mismo_importe.MAX_PARTIAL_CANDIDATES + 1, False)])
    def test_con_demasiadas_candidatas_no_se_responde(self, candidates, answered):
        eng = motor_real([vencida(f"F{n:02d}", 100000 + 1009 * n) for n in range(candidates)])
        assert bool(eng.m_party_amount(movement(100000 + 50504))) is answered  # F00 entera y F01 al 50 %
        assert bool(eng.rows) is answered

    def test_dos_clientes_con_solucion_no_se_responde(self):
        items = [vencida(n, v, party="C1") for n, v in (("A", 10000), ("B", 7000))] + [vencida(n, v, party="C2") for n, v in (("D", 10000), ("E", 7000))]
        eng = motor_real(items)
        eng.resolve_parties = lambda t: (["C1", "C2"], "nombre")
        assert not eng.m_party_amount(movement(13500))
        assert eng.rows == []

    def test_otra_sociedad_no_cuenta(self):
        eng = motor_real([vencida("A", 10000), vencida("B", 7000, entity="FR01"), vencida("C", 3333)])
        assert not eng.m_party_amount(movement(13500))

    def test_otra_divisa_no_cuenta(self):
        eng = motor_real([vencida("A", 10000), vencida("B", 7000, ccy="USD"), vencida("C", 3333)])
        eng.ph.fx = SimpleNamespace(conv=lambda cents, frm, to, day: cents * 3)
        assert not eng.m_party_amount(movement(13500))

    def test_un_abono_no_es_candidata(self):
        eng = motor_real([vencida("A", 10000), credit_note("ABONO", cents=7000)])
        assert not eng.m_party_amount(movement(13500))

    def test_una_factura_ya_pagada_no_es_candidata_ni_parcial(self):
        pagada = vencida("B", 7000)
        pagada.open = 0
        eng = motor_real([vencida("A", 10000), pagada, vencida("C", 3333)])
        assert not eng.m_party_amount(movement(13500))  # A entera y B al 50 %, si B siguiera abierta
        assert eng.rows == []

    def test_un_proveedor_no_entra(self):
        eng = motor_real([vencida("A", 10000, party="S1", kind="API", side="AP"), vencida("B", 7000, party="S1", kind="API", side="AP")])
        eng.resolve_parties = lambda t: (["S1"], "nombre")
        assert not eng.m_party_amount(movement(13500))

    def test_un_cargo_no_entra(self):
        eng = motor_real([vencida("A", 10000), vencida("B", 7000)])
        assert not eng.m_party_amount(movement(-13500))
        assert eng.rows == []

    def test_lo_exacto_gana_a_la_parcial(self):
        eng = motor_real([vencida("A", 10000), vencida("B", 3500), vencida("C", 7000)])
        assert eng.m_party_amount(movement(13500))  # A y B enteras, no A y C al 50 %
        assert filas(eng) == [("A", 10000, ""), ("B", 3500, "")]


def spy_settle(items):
    """Solo para ver si settle pasa el caso a la regla original."""
    eng = Engine.__new__(Engine)
    eng.items = {item.id: item for item in items}
    eng.ph = SimpleNamespace(children={}, by_party={}, fx=None, terms={}, txns=[])
    eng.rows = []
    eng.delegated = []
    eng.settle = lambda t, ids, rule, ev="", party=None: eng.delegated.append(list(ids)) or True
    mismo_importe.prepare(eng)
    return eng


class TestAnoTrasElMes:
    @pytest.mark.parametrize("words, year", [
        (["MAYO", "2025"], 2025),
        (["MAYO", "25"], 2025),
        (["MAYO", "DE", "2025"], 2025),
        (["MAYO", "123"], None),
        (["MAYO", "DE"], None),
        (["MAYO"], None),
    ])
    def test_lee_el_ano_que_sigue_al_mes(self, words, year):
        assert year_after(words, 0) == year


class TestOrdenDePlugins:
    """`mismo_importe` transforma en `prepare` lo que deja `m2_avisos`: cargado antes no vería nada, sin ningún aviso."""

    def test_detras_de_m2_avisos_carga(self):
        names = [module.__name__ for module in load_plugins("m2_avisos,mismo_importe")]
        assert names == ["bankrec.m2_avisos", "bankrec.mismo_importe"]

    def test_delante_de_m2_avisos_se_rechaza(self):
        with pytest.raises(SystemExit, match="mismo_importe"):
            load_plugins("mismo_importe,m2_avisos")

    def test_sin_m2_avisos_carga(self):
        assert [module.__name__ for module in load_plugins("mismo_importe")] == ["bankrec.mismo_importe"]

    def test_el_orden_por_defecto_es_valido(self):
        assert load_plugins(DEFAULT_PLUGINS)


if __name__ == "__main__":
    pytest.main([__file__])
