"""Tests unitarios para el Lote 2 de issues de auditoría (#50, #54, #58, #59, #61, #64, #65, #69, #81)."""
import datetime as dt
from types import SimpleNamespace

from bankrec import duplicados, nombres_cortados
from bankrec.data import Item, name_key
from bankrec.engine import Engine


def _motor(**attrs):
    eng = Engine.__new__(Engine)
    for k, v in attrs.items():
        setattr(eng, k, v)
    return eng


def test_issue_59_y_81_za01_nacional_y_gb_ch_pl_cross_border():
    """Issue #59: ZA01 sin entidad en entities.csv no trata ZA->ZA01 como extranjero.
    Issue #81: GB, CH y PL sin /CHGS/ sí cuentan como cross-border para SHORT_BANK_CHARGES."""
    eng = _motor(
        ph=SimpleNamespace(
            party_country={"C_ZA": "ZA", "C_GB": "GB"},
            entity_country={"ES01": "ES"},
            fx=SimpleNamespace(conv=lambda cents, *_: cents),
        ),
        discount_ok=lambda *_: None,
    )
    t = SimpleNamespace(up="TRANSFERENCIA", orig_ccy=None, date=dt.date(2025, 6, 10))
    inv_za = Item(id="A1", kind="ARI", side="AR", entity="ZA01", number="A1", party="C_ZA", ccy="ZAR", total=100000, open=100000)
    inv_gb = Item(id="A2", kind="ARI", side="AR", entity="ES01", number="A2", party="C_GB", ccy="EUR", total=100000, open=100000)

    assert eng.explain_short(inv_za, t, 1500, 98500) is None
    assert eng.explain_short(inv_gb, t, 1500, 98500) == "SHORT_BANK_CHARGES"


def test_issue_64_retencion_sobre_varias_facturas():
    """Issue #64: SHORT_WITHHOLDING sobre varias facturas del mismo cliente MX/BR/MA con tasa única."""
    f1 = Item(id="ARI-1", kind="ARI", side="AR", entity="MX01", number="1", party="C_MX", ccy="MXN", total=10000, open=10000)
    f2 = Item(id="ARI-2", kind="ARI", side="AR", entity="MX01", number="2", party="C_MX", ccy="MXN", total=20000, open=20000)
    filas = []
    eng = _motor(
        items={"ARI-1": f1, "ARI-2": f2},
        ph=SimpleNamespace(
            party_country={"C_MX": "MX"},
            terms={},
            fx=SimpleNamespace(conv=lambda c, *_: c),
        ),
        sign=lambda it, t: 1,
        to_bank=lambda c, *_: c,
        emit=lambda t, it, amt, exc, rule, ev="": filas.append((it.id, amt, exc)),
    )
    # 10% de retención sobre 10.000 + 20.000 = cobro de 27.000
    eng.settle(SimpleNamespace(amount=27000, ccy="MXN", date=dt.date(2025, 6, 10)), ["ARI-1", "ARI-2"], "refs")
    assert filas == [("ARI-1", 10000, "SHORT_WITHHOLDING"), ("ARI-2", 20000, "SHORT_WITHHOLDING")]


def test_issue_61_y_65_duplicado_ap_en_remesa_y_sin_referencia():
    """Issue #61: emit_run_rows marca DUPLICATE_PAYMENT si la API ya fue liquidada con ese importe.
    Issue #65: settle_duplicate detecta pagos duplicados a proveedores (API)."""
    from bankrec.data import num_variants

    api = Item(id="API-1", kind="API", side="AP", entity="DE01", number="FV-2025-0001", party="S1", ccy="EUR", total=50000, open=0, issue=dt.date(2025, 1, 10))
    filas = []
    eng = _motor(
        items={"API-1": api},
        settled_by={"API-1": [("BT-0", 50000, "payment_run_transfer")]},
        ph=SimpleNamespace(
            by_num={v: {"API-1"} for v in num_variants("FV-2025-0001")},
            party_by_iban={},
            by_party={"S1": ["API-1"]},
            children={},
            fx=SimpleNamespace(conv=lambda c, *_: c),
        ),
        resolve_name=lambda *_: "S1",
        resolve_parties=lambda *_: (["S1"], "iban"),
        scan_names=lambda *_: [],
        entity_ok=lambda *_: True,
        to_bank=lambda c, *_: c,
        emit=lambda t, it, amt, exc, rule, ev="": filas.append((it.id, amt, exc, rule)),
    )
    t = SimpleNamespace(id="BT-2", amount=-50000, ccy="EUR", account="DE01-1", entity="DE01", date=dt.date(2025, 2, 1), up="SEPA S1")
    eng.emit_run_rows(t, [{"document_type": "INVOICE", "document_number": "FV-2025-0001", "document_amount": "500.00", "currency": "EUR", "beneficiary": "S1"}], "payment_run_transfer", "remesa")
    assert filas[-1] == ("API-1", 50000, "DUPLICATE_PAYMENT", "payment_run_transfer")

    assert duplicados.settle_duplicate(eng, t) is True
    assert filas[-1] == ("API-1", 50000, "DUPLICATE_PAYMENT", "ap_duplicate_iban")


def test_issue_58_nombres_cortados_no_rompe_unicidad_si_ya_hay_acierto_exacto():
    """Issue #58: si scan_names ya encontró un cliente con factura exacta, nombres_cortados no añade un segundo."""
    i0 = Item(id="A0", kind="ARI", side="AR", entity="CN01", number="A0", party="C0", ccy="EUR", total=10000, open=10000)
    i1 = Item(id="A1", kind="ARI", side="AR", entity="CN01", number="A1", party="C1", ccy="EUR", total=10000, open=10000)
    party_names = {"C0": {name_key("Dragon Pools")}, "C1": {name_key("Aquatics Harris 2000")}}
    name_index = {"DRAGON": {"C0"}, "AQUATICS": {"C1"}}
    eng = _motor(
        items={"A0": i0, "A1": i1},
        ph=SimpleNamespace(name_index=name_index, party_names=party_names, items={"A0": i0, "A1": i1}, by_party={"C0": ["A0"], "C1": ["A1"]}, children={}),
    )
    nombres_cortados.prepare(eng)
    t = SimpleNamespace(id="T1", text="FP DRAGON POOLS AQUATICS HARRIS 20 SERVICE", rmt="", ref1="", ref2="", entity="CN01", amount=10000, ccy="EUR", date=dt.date(2025, 6, 10))
    assert eng.scan_names(t) == ["C0"]
