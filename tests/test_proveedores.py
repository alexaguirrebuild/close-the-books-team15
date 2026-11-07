"""Pagos a proveedores y remesas de pago (POLICIES §5.2-§5.3): ap_payment_multi, ap_payment_single, ap_confirming.

Reglas del plugin bankrec/proveedores.py, que envuelve métodos del motor sin editar engine.py.
"""
import datetime as dt
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

from bankrec import proveedores
from bankrec.data import Item
from bankrec.engine import Engine

ROOT = Path(__file__).resolve().parents[1]
DAY = dt.date(2025, 3, 10)


def factura(item_id, numero="INV0001234", party="S1", cents=10000, abierto=None, entity="ES01", kind="API",
            issue=dt.date(2025, 2, 1), ccy="EUR"):
    return Item(id=item_id, kind=kind, side="AP", entity=entity, number=numero, party=party, ccy=ccy, total=cents,
                open=cents if abierto is None else abierto, issue=issue)


def doc(numero="INV0001234", importe="100.00", beneficiario="Proveedor Uno SL", iban="", tipo="INVOICE",
        cuenta="ES01-BANK-EUR", moneda="EUR"):
    return {"document_number": numero, "document_amount": importe, "beneficiary": beneficiario,
            "beneficiary_iban": iban, "document_type": tipo, "debtor_account_id": cuenta, "currency": moneda}


def cargo(importe=-10000, cuenta="ES01-BANK-EUR", ccy="EUR"):
    return SimpleNamespace(id="T1", amount=importe, account=cuenta, ccy=ccy, date=DAY, up="", e2e=None,
                           orig_amount=None, orig_ccy=None)


def motor(items, by_num=None, party_by_iban=None, names=None):
    eng = Engine.__new__(Engine)
    eng.items = {i.id: i for i in items}
    eng.ph = SimpleNamespace(by_num=by_num or {}, party_by_iban=party_by_iban or {},
                             fx=SimpleNamespace(conv=lambda a, f, t, d: a))
    eng.resolve_name = lambda raw: (names or {}).get(raw)
    proveedores.prepare(eng)
    return eng


class TestDesempateReproducible:
    """run_doc_item ordena candidatas que salen de un set: sin el id como último criterio, el ganador
    de un empate dependía de PYTHONHASHSEED y dos ejecuciones podían dar envíos distintos."""

    SONDA = r"""
import datetime as dt, sys
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
from bankrec import proveedores
from bankrec.data import Item
from bankrec.engine import Engine
ids = [f"API-2025-{n:07d}" for n in range(30)]
items = {i: Item(id=i, kind="API", side="AP", entity="ES01", number="INV0001234", party="S1", ccy="EUR",
                 total=10000, open=10000, issue=dt.date(2025, 2, 1)) for i in ids}
eng = Engine.__new__(Engine)
eng.items = items
eng.ph = SimpleNamespace(by_num={"INV0001234": set(ids), "1234": set(ids)}, party_by_iban={},
                         fx=SimpleNamespace(conv=lambda a, f, t, d: a))
eng.resolve_name = lambda raw: "S1"
if sys.argv[2] == "plugin":
    proveedores.prepare(eng)
r = {"document_number": "INV0001234", "document_amount": "100.00", "beneficiary": "X", "beneficiary_iban": "",
     "document_type": "INVOICE", "debtor_account_id": "ES01-BANK-EUR", "currency": "EUR"}
t = SimpleNamespace(account="ES01-BANK-EUR", date=dt.date(2025, 3, 10))
print(eng.run_doc_item(r, t).id)
"""

    def _ganadores(self, modo):
        out = set()
        for seed in range(1, 9):
            env = dict(os.environ, PYTHONHASHSEED=str(seed))
            res = subprocess.run([sys.executable, "-c", self.SONDA, str(ROOT), modo], env=env,
                                 capture_output=True, text=True, check=True)
            out.add(res.stdout.strip())
        return out

    def test_el_empate_se_resuelve_siempre_igual_por_id(self):
        assert self._ganadores("plugin") == {"API-2025-0000000"}

    def test_sin_el_plugin_el_motor_no_es_reproducible(self):
        # documenta el defecto que corrige el plugin; si engine.py se arregla, este test avisa de quitar la envoltura
        assert len(self._ganadores("motor")) > 1


class TestNoCambiaLoQueYaFunciona:
    """La envoltura no debe alterar la elección cuando no hay empate."""

    def test_gana_la_que_cuadra_con_el_importe(self):
        a = factura("API-2025-0000001", cents=10000)
        b = factura("API-2025-0000002", cents=55500)
        eng = motor([a, b], by_num={"INV0001234": {a.id, b.id}}, names={"Proveedor Uno SL": "S1"})
        assert eng.run_doc_item(doc(importe="555.00"), cargo()).id == b.id

    def test_prefiere_la_del_tercero_del_beneficiario(self):
        a = factura("API-2025-0000001", party="S1")
        b = factura("API-2025-0000002", party="S2")
        eng = motor([a, b], by_num={"INV0001234": {a.id, b.id}}, party_by_iban={"ES9900": "S2"})
        assert eng.run_doc_item(doc(iban="ES 9900"), cargo()).id == b.id

    def test_prefiere_la_sociedad_que_paga(self):
        a = factura("API-2025-0000001", entity="ES02")
        b = factura("API-2025-0000002", entity="ES01")
        eng = motor([a, b], by_num={"INV0001234": {a.id, b.id}}, names={"Proveedor Uno SL": "S1"})
        assert eng.run_doc_item(doc(), cargo()).id == b.id

    def test_prefiere_la_abierta_a_la_pagada(self):
        pagada = factura("API-2025-0000001", abierto=0, cents=20000)
        abierta = factura("API-2025-0000002", cents=20000)
        eng = motor([pagada, abierta], by_num={"INV0001234": {pagada.id, abierta.id}}, names={"Proveedor Uno SL": "S1"})
        assert eng.run_doc_item(doc(importe="300.00"), cargo()).id == abierta.id

    def test_nota_de_abono_solo_con_documento_de_abono(self):
        abono = factura("APC-2025-0000001", kind="APC")
        eng = motor([abono], by_num={"INV0001234": {abono.id}}, names={"Proveedor Uno SL": "S1"})
        assert eng.run_doc_item(doc(), cargo()) is None
        assert eng.run_doc_item(doc(tipo="CREDIT_NOTE"), cargo()).id == abono.id

    def test_sin_candidatas_devuelve_none(self):
        eng = motor([], names={"Proveedor Uno SL": "S1"})
        assert eng.run_doc_item(doc(), cargo()) is None


def motor_por_tercero(items, party_by_iban=None, names=None):
    """Motor con índice por tercero, como Phase.by_party."""
    eng = motor(items, party_by_iban=party_by_iban, names=names)
    by_party = {}
    for it in items:
        by_party.setdefault(it.party, []).append(it.id)
    eng.ph.by_party = by_party
    return eng


class TestNumeroCorto:
    """Facturas de proveedor con número de 2-3 dígitos («556»): num_variants no genera ninguna variante (exige 4),
    así que el documento de la remesa se saltaba en silencio. En el holdout, 540 pares. POLICIES §5.3: el cargo
    de la remesa liquida sus documentos. Evidencia exigida: tercero del beneficiario + número idéntico + única."""

    def test_numero_corto_del_tercero_del_beneficiario(self):
        it = factura("API-2025-0000001", numero="556", party="S1", cents=12345)
        eng = motor_por_tercero([it], names={"Valves Coral Bay": "S1"})
        r = doc(numero="556", importe="123.45", beneficiario="Valves Coral Bay")
        assert eng.run_doc_item(r, cargo()).id == it.id

    def test_por_iban_del_beneficiario(self):
        it = factura("API-2025-0000001", numero="88", party="S1")
        eng = motor_por_tercero([it], party_by_iban={"AU1234": "S1"})
        assert eng.run_doc_item(doc(numero="88", iban="AU 1234"), cargo()).id == it.id

    def test_no_fuerza_sin_tercero_identificado(self):
        it = factura("API-2025-0000001", numero="556", party="S1")
        eng = motor_por_tercero([it], names={})
        assert eng.run_doc_item(doc(numero="556", beneficiario="Desconocido"), cargo()) is None

    def test_no_fuerza_la_de_otro_tercero(self):
        ajena = factura("API-2025-0000001", numero="556", party="S2")
        eng = motor_por_tercero([ajena], names={"Valves Coral Bay": "S1"})
        assert eng.run_doc_item(doc(numero="556", beneficiario="Valves Coral Bay"), cargo()) is None

    def test_no_fuerza_un_numero_distinto(self):
        it = factura("API-2025-0000001", numero="557", party="S1")
        eng = motor_por_tercero([it], names={"Valves Coral Bay": "S1"})
        assert eng.run_doc_item(doc(numero="556", beneficiario="Valves Coral Bay"), cargo()) is None

    def test_dos_del_mismo_numero_decide_el_importe_exacto(self):
        a = factura("API-2024-0000001", numero="556", party="S1", cents=50000)
        b = factura("API-2025-0000002", numero="556", party="S1", cents=12345)
        eng = motor_por_tercero([a, b], names={"Valves Coral Bay": "S1"})
        r = doc(numero="556", importe="123.45", beneficiario="Valves Coral Bay")
        assert eng.run_doc_item(r, cargo()).id == b.id

    def test_dos_del_mismo_numero_sin_importe_exacto_no_responde(self):
        a = factura("API-2024-0000001", numero="556", party="S1", cents=50000)
        b = factura("API-2025-0000002", numero="556", party="S1", cents=60000)
        eng = motor_por_tercero([a, b], names={"Valves Coral Bay": "S1"})
        r = doc(numero="556", importe="123.45", beneficiario="Valves Coral Bay")
        assert eng.run_doc_item(r, cargo()) is None

    def test_no_fuerza_una_partida_ya_pagada_sin_importe_exacto(self):
        pagada = factura("API-2023-0000001", numero="556", party="S1", cents=50000, abierto=0)
        eng = motor_por_tercero([pagada], names={"Valves Coral Bay": "S1"})
        r = doc(numero="556", importe="123.45", beneficiario="Valves Coral Bay")
        assert eng.run_doc_item(r, cargo()) is None

    def test_plazo_de_una_abierta_que_cubre_el_importe_vale(self):
        abierta = factura("API-2025-0000001", numero="556", party="S1", cents=50000)
        eng = motor_por_tercero([abierta], names={"Valves Coral Bay": "S1"})
        r = doc(numero="556", importe="250.00", beneficiario="Valves Coral Bay")
        assert eng.run_doc_item(r, cargo()).id == abierta.id

    def test_no_fuerza_un_numero_de_un_caracter(self):
        it = factura("API-2025-0000001", numero="7", party="S1")
        eng = motor_por_tercero([it], names={"Valves Coral Bay": "S1"})
        assert eng.run_doc_item(doc(numero="7", beneficiario="Valves Coral Bay"), cargo()) is None

    def test_otra_sociedad_del_grupo_vale_porque_la_holding_paga(self):
        # POLICIES §5.2: la fábrica de pagos (ES00) paga facturas de sus filiales desde sus cuentas
        filial = factura("API-2025-0000001", numero="556", party="S1", entity="PT01")
        eng = motor_por_tercero([filial], names={"Valves Coral Bay": "S1"})
        r = doc(numero="556", beneficiario="Valves Coral Bay", cuenta="ES00-BANK-EUR")
        assert eng.run_doc_item(r, cargo(cuenta="ES00-BANK-EUR")).id == filial.id

    def test_un_abono_corto_solo_con_documento_de_abono(self):
        abono = factura("APC-2025-0000001", numero="77", party="S1", kind="APC")
        eng = motor_por_tercero([abono], names={"Valves Coral Bay": "S1"})
        assert eng.run_doc_item(doc(numero="77", beneficiario="Valves Coral Bay"), cargo()) is None
        r = doc(numero="77", beneficiario="Valves Coral Bay", tipo="CREDIT_NOTE")
        assert eng.run_doc_item(r, cargo()).id == abono.id


def remesa_confirming(vencimientos, rid="CF010025011055"):
    """Un proveedor (e2e) por vencimiento; importe de cada transferencia = 100,00 × (k + 1)."""
    rs = []
    for k, _ in enumerate(vencimientos):
        rs.append({"run_id": rid, "run_type": "confirming", "end_to_end_id": f"{rid}-000{k}", "execution_date": "2025-01-02",
                   "debtor_account_id": "ES01-BANK-EUR", "beneficiary": f"Prov {k}", "currency": "EUR",
                   "transfer_amount": f"{(k + 1) * 100}.00", "document_number": f"INV{k}", "document_amount": f"{(k + 1) * 100}.00"})
    return rs


def motor_confirming(vencimientos, plugin=True):
    # dos proveedores más que vencen días después: como en una remesa real, el total del fichero no cuadra con el cargo
    relleno = [max(vencimientos) + dt.timedelta(days=10)] * 2
    rs = remesa_confirming(list(vencimientos) + relleno)
    vencimientos = list(vencimientos) + relleno
    eng = Engine.__new__(Engine)
    eng.run_rows, eng.e2e_rows, eng.transfers_by_amount, eng.used_batches = {rs[0]["run_id"]: rs}, {}, {}, set()
    due = {r["document_number"]: d for r, d in zip(rs, vencimientos)}
    eng.run_doc_item = lambda r, t: SimpleNamespace(due=due[r["document_number"]])
    eng.emitidas = []
    eng.emit_run_rows = lambda t, filas, rule, ev: eng.emitidas.append((sorted(f["beneficiary"] for f in filas), rule))
    if plugin:
        proveedores._wrap_confirming(eng)
    return eng


def cargo_confirming(dia, centimos, n):
    return SimpleNamespace(amount=-centimos, account="ES01-BANK-EUR", e2e="", date=dia, ccy="EUR", orig_amount=None,
                           orig_ccy=None, up=f"PAGO CONFIRMING | REMESA CF010025011055 {n} PROVEEDORES")


class TestConfirmingFinDeSemana:
    """POLICIES §5.3: el confirming carga a cada proveedor en el vencimiento más tardío de sus facturas, y lo que
    vence en sábado o domingo se paga el lunes. En el holdout, 3.037 de 3.200 grupos de proveedor cuadran así
    (el resto, festivos). El motor agrupaba por la fecha exacta: un lunes con vencimientos de sábado, domingo y
    lunes no cuadraba nunca, y con más de 4 proveedores tampoco lo rescataba la combinatoria."""

    LUNES = dt.date(2025, 3, 10)
    SAB, DOM, VIE = dt.date(2025, 3, 8), dt.date(2025, 3, 9), dt.date(2025, 3, 7)

    def test_lunes_junta_sabado_domingo_y_lunes(self):
        venc = [self.SAB, self.SAB, self.DOM, self.LUNES, self.LUNES]
        eng = motor_confirming(venc)
        assert eng.m_payment_run(cargo_confirming(self.LUNES, 150000, 5)) is True
        assert eng.emitidas == [([f"Prov {k}" for k in range(5)], "payment_run_confirming")]

    def test_sin_el_plugin_no_cuadraba(self):
        venc = [self.SAB, self.SAB, self.DOM, self.LUNES, self.LUNES]
        eng = motor_confirming(venc, plugin=False)
        eng.m_payment_run(cargo_confirming(self.LUNES, 150000, 5))
        assert eng.emitidas == []

    def test_no_mete_el_viernes_en_el_lunes(self):
        venc = [self.VIE, self.SAB, self.LUNES]  # importes 100, 200, 300
        eng = motor_confirming(venc)
        assert eng.m_payment_run(cargo_confirming(self.LUNES, 50000, 2)) is True
        assert eng.emitidas == [(["Prov 1", "Prov 2"], "payment_run_confirming")]

    def test_no_fuerza_si_el_importe_no_cuadra(self):
        venc = [self.SAB, self.DOM, self.LUNES, self.LUNES, self.LUNES]
        eng = motor_confirming(venc)
        assert eng.m_payment_run(cargo_confirming(self.LUNES, 150001 + 500, 5)) is False
        assert eng.emitidas == []

    def test_el_grupo_del_sabado_cargado_solo_sigue_cuadrando(self):
        # revisión: con el día de pago, sábado y lunes se funden; si el banco carga solo el sábado (y son más de 4
        # proveedores, sin combinatoria), hay que seguir probando la fecha exacta como antes
        venc = [self.SAB] * 5 + [self.LUNES] * 2
        eng = motor_confirming(venc)
        assert eng.m_payment_run(cargo_confirming(self.LUNES, 150000, 5)) is True
        assert eng.emitidas == [([f"Prov {k}" for k in range(5)], "payment_run_confirming")]

    def test_vence_en_sabado_y_se_carga_el_viernes(self):
        # revisión: al pasar el vencimiento al lunes, un cargo del viernes quedaba fuera de la ventana (-1..+5)
        eng = motor_confirming([self.SAB, self.SAB])
        assert eng.m_payment_run(cargo_confirming(self.VIE, 30000, 2)) is True
        assert eng.emitidas == [(["Prov 0", "Prov 1"], "payment_run_confirming")]

    def test_entre_semana_no_cambia(self):
        martes = dt.date(2025, 3, 11)
        eng = motor_confirming([martes, martes])
        assert eng.m_payment_run(cargo_confirming(martes, 30000, 2)) is True
        assert eng.emitidas == [(["Prov 0", "Prov 1"], "payment_run_confirming")]


class TestDerivaDeFinal:
    """POLICIES §6: Moncayo pasa de Norma 43 a camt.053 (2025-10-01) y en 2026 llega ZA01 con numeración «KWI…».
    Textos reales de LIVE: el cargo de confirming debe seguir encontrando su remesa en los dos formatos."""

    def test_la_remesa_citada_se_lee_en_n43_y_en_camt053(self):
        n43 = "PAGO CONFIRMING | REMESA CF000025062552 3 PROVEEDORES"
        camt = "PAGO CONFIRMING REMESA CF000025092566 3 PROVEEDORES"
        assert Engine._cited_runs(SimpleNamespace(up=n43)) == ["CF000025062552"]
        assert Engine._cited_runs(SimpleNamespace(up=camt)) == ["CF000025092566"]

    def test_el_id_partido_por_el_banco_se_recompone(self):
        assert "PR010125071007" in Engine._cited_runs(SimpleNamespace(up="PAGO REMESA PR0101250 | 71007"))

    def test_confirming_en_camt053_paga_los_tres_proveedores_del_dia(self):
        due = dt.date(2025, 10, 1)
        rs = [{"run_id": "CF000025092566", "run_type": "confirming", "end_to_end_id": f"CF000025092566-000{k}",
               "execution_date": "2025-09-25", "debtor_account_id": "ES01-BANK-EUR", "beneficiary": f"Prov {k}",
               "currency": "EUR", "transfer_amount": f"{k}00.00", "document_number": f"INV{k}", "document_amount": f"{k}00.00"}
              for k in (1, 2, 3)]
        eng = Engine.__new__(Engine)
        eng.run_rows, eng.e2e_rows, eng.transfers_by_amount, eng.used_batches = {"CF000025092566": rs}, {}, {}, set()
        eng.run_doc_item = lambda r, t: SimpleNamespace(due=due)
        eng.emitidas = []
        eng.emit_run_rows = lambda t, filas, rule, ev: eng.emitidas.append((len(filas), rule))
        t = SimpleNamespace(amount=-60000, account="ES01-BANK-EUR", up="PAGO CONFIRMING REMESA CF000025092566 3 PROVEEDORES",
                            e2e="", date=due, ccy="EUR", orig_amount=None, orig_ccy=None)
        assert eng.m_payment_run(t) is True
        assert eng.emitidas == [(3, "payment_run_confirming")]

    def test_numeracion_kwi_de_la_sociedad_nueva(self):
        it = factura("API-2026-0000001", numero="KWI26-000417", entity="ZA01", ccy="ZAR")
        from bankrec.data import num_variants
        by_num = {v: {it.id} for v in num_variants(it.number)}
        eng = motor([it], by_num=by_num, names={"Proveedor ZA": "S1"})
        r = doc(numero="KWI26-000417", beneficiario="Proveedor ZA", cuenta="ZA01-BANK-ZAR", moneda="ZAR")
        assert eng.run_doc_item(r, cargo(cuenta="ZA01-BANK-ZAR", ccy="ZAR")).id == it.id


def test_el_cargo_del_viernes_no_se_lleva_otra_remesa():
    """Issue #21: la fecha del confirming se prueba antes que las búsquedas por importe, que miran cualquier remesa."""
    run = "CF010025011055"
    vencimientos = {"INV0": dt.date(2025, 3, 8), "INV1": dt.date(2025, 3, 18), "INV2": dt.date(2025, 3, 18), "OTRA": dt.date(2025, 3, 6)}

    def fila(remesa, tipo, e2e, proveedor, importe, documento, ejecucion="2025-03-05"):
        return {
            "run_id": remesa, "run_type": tipo, "end_to_end_id": e2e, "execution_date": ejecucion, "debtor_account_id": "ES01-BANK-EUR",
            "beneficiary": proveedor, "currency": "EUR", "transfer_amount": importe, "document_number": documento, "document_amount": importe,
        }

    filas = [
        fila(run, "confirming", f"{run}-0000", "Prov Cero", "100.00", "INV0"),
        fila(run, "confirming", f"{run}-0001", "Prov Uno", "200.00", "INV1"),
        fila(run, "confirming", f"{run}-0002", "Prov Dos", "300.00", "INV2"),
        fila("PR010125030601", "transfers", "PR010125030601-0007", "Otro Proveedor", "100.00", "OTRA", ejecucion="2025-03-06"),
    ]
    motor = Engine.__new__(Engine)
    motor.run_rows, motor.e2e_rows = defaultdict(list), defaultdict(list)
    for r in filas:
        motor.run_rows[r["run_id"]].append(r)
        motor.e2e_rows[r["end_to_end_id"]].append(r)
    motor.transfers_by_amount = motor._index_transfers()
    motor.used_batches = set()
    motor.run_doc_item = lambda r, t: SimpleNamespace(due=vencimientos[r["document_number"]])
    salida = []
    motor.emit_run_rows = lambda t, rs, rule, ev: salida.append((rule, sorted(r["document_number"] for r in rs)))
    proveedores._wrap_confirming(motor)
    viernes = SimpleNamespace(
        amount=-10000, account="ES01-BANK-EUR", e2e="", date=dt.date(2025, 3, 7), ccy="EUR", orig_amount=None, orig_ccy=None,
        up=f"PAGO CONFIRMING | REMESA {run} 1 PROVEEDORES",
    )

    assert motor.m_payment_run(viernes) is True
    assert salida == [("payment_run_confirming", ["INV0"])]
