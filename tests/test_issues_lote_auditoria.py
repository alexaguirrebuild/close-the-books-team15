"""Tests unitarios para el lote de issues de auditoría (#47, #55, #62, #63, #82, #83, #85, #86, #87, #88)."""
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from bankrec.engine import Engine, classify_nonitem


def _motor_vacio(**atributos):
    motor = Engine.__new__(Engine)
    for k, v in atributos.items():
        setattr(motor, k, v)
    return motor


def test_issue_62_abono_con_signo_invertido_no_recibe_excepcion_de_factura():
    """POLICIES §3 / Issue #62: ARC/APC nunca llevan FX_DIFFERENCE, OVERPAYMENT, SHORT_* ni PARTIAL."""
    abono = SimpleNamespace(id="ARC-99", kind="ARC", ccy="USD", open=10000, total=10000, terms=None)
    filas = []

    def emit(t, it, amt, exc, rule, ev=""):
        filas.append((it.id, amt, exc))

    motor = _motor_vacio(
        items={"ARC-99": abono},
        ph=SimpleNamespace(fx=SimpleNamespace(conv=lambda c, *_: c)),
        sign=lambda it, t: 1,  # signo invertido (cobro con ARC sola)
        to_bank=lambda importe, it, t: importe,
        explain_short=lambda *_: "SHORT_BANK_CHARGES",
        emit=emit,
    )

    # Caso 1: cuadra en otra divisa -> no debe poner FX_DIFFERENCE al abono
    motor.settle(SimpleNamespace(amount=10000, ccy="EUR", date=None), ["ARC-99"], "regla")
    # Caso 2: sobra dinero -> no debe poner OVERPAYMENT al abono
    motor.settle(SimpleNamespace(amount=10500, ccy="EUR", date=None), ["ARC-99"], "regla")
    # Caso 3: falta dinero -> no debe poner SHORT_* ni PARTIAL al abono
    motor.settle(SimpleNamespace(amount=8000, ccy="EUR", date=None), ["ARC-99"], "regla")

    assert [exc for _, _, exc in filas] == ["", "", ""]


def test_issue_83_devolucion_pago_duplicado_es_supplier_refund():
    """POLICIES §4 / Issue #83: DEVOLUCION PAGO DUPLICADO en cobro clasifica como SUPPLIER_REFUND y en cargo como CUSTOMER_REFUND."""
    cat_cobro, _ = classify_nonitem(SimpleNamespace(amount=10000, up="TRANSFERENCIA DEVOLUCION PAGO DUPLICADO FACTURA"))
    cat_cargo, _ = classify_nonitem(SimpleNamespace(amount=-10000, up="TRANSFERENCIA DEVOLUCION PAGO DUPLICADO FACTURA"))
    assert cat_cobro == "SUPPLIER_REFUND"
    assert cat_cargo == "CUSTOMER_REFUND"


def test_issue_47_token_tolera_bom_export_y_comillas(tmp_path, monkeypatch):
    """Issue #47: scripts/submit.py lee HK_TOKEN con UTF-8 BOM, prefijo export y comillas."""
    import scripts.submit as sub

    env_file = tmp_path / ".env"
    env_file.write_bytes("\ufeffexport HK_TOKEN=\"tok_secreto_123\"\r\n".encode("utf-8"))
    monkeypatch.setattr(sub, "ROOT", tmp_path)
    monkeypatch.delenv("HK_TOKEN", raising=False)
    assert sub.token() == "tok_secreto_123"


def test_issue_85_plain_number_miles_con_punto_y_txt_sin_fecha():
    """Issue #85: '1.234' se interpreta como 1234 y txt_fmt acepta avisos sin fecha de pago."""
    import sys
    pipe_dir = str(Path(__file__).resolve().parents[1] / "pipeline")
    if pipe_dir not in sys.path:
        sys.path.insert(0, pipe_dir)
    from bankcheck.remittance.common import _plain_number
    from bankcheck.remittance.txt_fmt import parse_txt_advice

    assert _plain_number("1.234") == "1234"
    txt = (
        "Subject: Payment advice RA-TEST-001\n"
        "We confirm our payment of 1.234 EUR:\n"
        "ARI-2025-0001 2025-01-15 1.234\n"
    )
    doc = parse_txt_advice(txt)
    assert len(doc.lines) == 1
    assert doc.lines[0].paid == Decimal("1234")
