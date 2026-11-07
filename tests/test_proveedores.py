"""Pagos a proveedores y remesas de pago (POLICIES §5.2-§5.3): ap_payment_multi, ap_payment_single, ap_confirming.

Reglas del plugin bankrec/proveedores.py, que envuelve métodos del motor sin editar engine.py.
"""
import datetime as dt
import os
import subprocess
import sys
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
