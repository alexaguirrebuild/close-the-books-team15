"""Plugin: un recibo devuelto que el cliente vuelve a pagar por transferencia no es DUPLICATE_PAYMENT. POLICIES §3 y §5.10.

POLICIES §3 deja vacía la excepción de los «re-presented direct debits». El motor ya tiene esa guarda (`ref_represented`),
pero la activa solo si el texto del banco no contiene RECIBO, y estos cobros lo dicen justo así: «RECIBO DEVUELTO 106…» o
«PAGO RECIBO 117085». Resultado: la factura, cerrada por el adeudo que luego se devolvió, salía como pago duplicado.
Medido en holdout: 53 filas DUPLICATE_PAYMENT sobre facturas de adeudo con ese texto, y las 53 tienen la excepción vacía en
el golden; ninguna es un duplicado real. Se envuelve `engine.emit` para no tocar `engine.py`.
"""
from __future__ import annotations

import re

REPRESENTED = re.compile(r"RECIBO\s+DEVUELTO|PAGO\s+RECIBO")


def prepare(engine) -> None:
    original = engine.emit

    def emit(t, it, amount, exc, rule, ev=""):
        if exc == "DUPLICATE_PAYMENT" and it.method == "dd" and REPRESENTED.search(t.up or ""):
            exc, rule, ev = "", "ref_represented_texto", f"{ev} · recibo devuelto, pagado por transferencia".strip(" ·")
        return original(t, it, amount, exc, rule, ev)

    engine.emit = emit
