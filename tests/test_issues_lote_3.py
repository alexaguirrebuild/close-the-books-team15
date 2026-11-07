"""Tests unitarios para el Lote 3 de issues de auditoría (#57, #66, #71, #84)."""
import datetime as dt
from pathlib import Path
from types import SimpleNamespace

from bankrec import devoluciones, m2_avisos, remittance
from bankrec.data import Item
from bankrec.engine import Engine


def test_issue_84_bankcheck_y_m2_aceptan_bankrec_data_y_bankrec_out(tmp_path, monkeypatch):
    """Issue #84: bankcheck.roots acepta BANKREC_DATA/BANKREC_OUT y m2_avisos usa BANKREC_OUT como respaldo."""
    import sys
    pipe_dir = str(Path(__file__).resolve().parents[1] / "pipeline")
    if pipe_dir not in sys.path:
        sys.path.insert(0, pipe_dir)
    from bankcheck.roots import resolve_layout

    data_root = tmp_path / "custom_data"
    out_root = tmp_path / "custom_out"
    (data_root / "sample").mkdir(parents=True)
    (data_root / "sample" / "bank_transactions.csv.gz").write_bytes(b"")
    (data_root / "masters").mkdir(parents=True)

    layout = resolve_layout("sample", env={"BANKREC_DATA": str(data_root), "BANKREC_OUT": str(out_root)})
    assert layout.root == data_root
    assert layout.report_dir == out_root / "sample"

    monkeypatch.delenv("BANKREC_M2_DIR", raising=False)
    monkeypatch.setenv("BANKREC_OUT", str(out_root))
    assert m2_avisos._stage_path("live") == out_root / "live" / "matches" / "m2_remittance.csv.gz"


def test_issue_57_no_aprende_iban_de_remesas_ni_machaca_party_by_iban():
    """Issue #57: emit() no envenena iban_learned desde payment_run/dd/cheque/card ni si ya está en party_by_iban."""
    eng = Engine.__new__(Engine)
    eng.rows = []
    eng.settled_by = {"API-1": [], "ARI-1": []}
    eng.iban_learned = {}
    eng.ph = SimpleNamespace(party_by_iban={"ES99": "C_REAL"})

    it_ap = Item(id="API-1", kind="API", side="AP", entity="ES01", number="1", party="S_PROV", ccy="EUR", total=1000, open=1000)
    it_ar = Item(id="ARI-1", kind="ARI", side="AR", entity="ES01", number="2", party="C_OTRO", ccy="EUR", total=1000, open=1000)

    # Remesa de pago -> no debe aprender el IBAN de confirming/intermediario
    eng.emit(SimpleNamespace(id="T1", cp_iban="ES_CONFIRMING"), it_ap, 1000, "", "payment_run_confirming")
    assert "ES_CONFIRMING" not in eng.iban_learned

    # IBAN ya presente en party_by_iban -> no debe guardarse en iban_learned con otro tercero
    eng.emit(SimpleNamespace(id="T2", cp_iban="ES99"), it_ar, 1000, "", "refs")
    assert "ES99" not in eng.iban_learned


def test_issue_71_retencion_garantia_es_partial_y_devolucion_elige_anterior_no_usada():
    """Issue #71: RETENCION GARANTIA 5% sale como PARTIAL (no SHORT_WITHHOLDING) y _returned_transfer_rows no repite e2e."""
    filas = []
    eng = Engine.__new__(Engine)
    eng.ph = SimpleNamespace(party_country={"C1": "ES"}, entity_country={"ES01": "ES"}, fx=SimpleNamespace(conv=lambda c, *_: c))
    eng.explain_short = lambda *_: None
    eng.emit = lambda t, it, amt, exc, rule, ev="": filas.append((it.id, amt, exc))

    it = Item(id="ARI-1", kind="ARI", side="AR", entity="ES01", number="1", party="C1", ccy="EUR", total=10000, open=10000)
    t = SimpleNamespace(id="T1", ccy="EUR", date=dt.date(2025, 6, 10), up="TRANSFER")
    remittance._emit_line(eng, t, {}, {"paid": 9500, "ded": -500, "reason": "RETENCION GARANTIA 5%"}, it, "aviso")
    assert filas[-1] == ("ARI-1", 9500, "PARTIAL")

    # _returned_transfer_rows elige la transferencia anterior a t.date y no la repite en una segunda devolución
    eng.e2e_rows = {
        "E2E-OLD": [{"end_to_end_id": "E2E-OLD", "transfer_amount": "100.00", "execution_date": "2025-05-01"}],
        "E2E-NEW": [{"end_to_end_id": "E2E-NEW", "transfer_amount": "100.00", "execution_date": "2025-05-20"}],
        "E2E-FUT": [{"end_to_end_id": "E2E-FUT", "transfer_amount": "100.00", "execution_date": "2025-07-01"}],
    }
    eng.transfers_by_amount = {("ES01-1", 10000): ["E2E-OLD", "E2E-NEW", "E2E-FUT"]}
    t_ret = SimpleNamespace(up="DEVOLUCION TRANSFERENCIA ES01", e2e="", account="ES01-1", amount=10000, date=dt.date(2025, 6, 1))
    r1 = devoluciones._returned_transfer_rows(eng, t_ret)
    r2 = devoluciones._returned_transfer_rows(eng, t_ret)
    assert r1[0]["end_to_end_id"] == "E2E-NEW"
    assert r2[0]["end_to_end_id"] == "E2E-OLD"


def test_issue_66_cheque_devuelto_busca_por_librador_si_no_estaba_en_cheque_items():
    """Issue #66: si el cheque se depositó antes y no está en cheque_items, busca por drawer + importe cerrado."""
    it = Item(id="ARI-9", kind="ARI", side="AR", entity="FR01", number="9", party="C_FR", ccy="EUR", total=25000, open=0)
    filas = []
    eng = Engine.__new__(Engine)
    eng.cheque_items = {}
    eng.items = {"ARI-9": it}
    eng.ph = SimpleNamespace(
        cheques=[{"cheque_number": "4271407", "drawer": "CLIENT FR SAS"}],
        by_party={"C_FR": ["ARI-9"]},
    )
    eng.resolve_name = lambda name: "C_FR" if "CLIENT FR" in name else None
    eng.emit = lambda t, item, amt, exc, rule, ev="": filas.append((item.id, amt, exc, rule))

    t = SimpleNamespace(up="CHEQUE IMPAYE 4271407", amount=-25000)
    assert devoluciones.match(eng, t) is True
    assert filas == [("ARI-9", 25000, "RETURNED", "cheque_returned")]
