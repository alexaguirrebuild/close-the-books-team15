"""Plugin del lote 4 (etiquetas y soluciones seguras): excepciones de cobros con partidas ya bien elegidas.

Por ahora no cambia ninguna fila: `prepare` no envuelve nada y `match` devuelve False. Las reglas entran de una en una,
cada una con su holdout (ver la PR a Dev).

Un plugin que lanza una excepción tumba toda la fase, porque `Engine.run` no tiene guardia. Por eso `match` corre
dentro de `guarded`: si algo falla, deshace lo que haya emitido y devuelve False, y el movimiento sigue su camino normal.
"""
from __future__ import annotations

import sys

_warned: set[str] = set()


def _warn_once(where: str, error: Exception) -> None:
    if where not in _warned:
        _warned.add(where)
        print(f"  AVISO cobros_cortos: {where} falló ({type(error).__name__}: {error}); se deshace y sigue sin la regla",
              file=sys.stderr, flush=True)


def guarded(engine, where: str, fn, fallback):
    """Ejecuta fn(). Si lanza, deshace las filas y saldos que haya emitido y devuelve fallback()."""
    n_rows = len(engine.rows)
    log = []
    had_own_emit = "emit" in engine.__dict__
    inner_emit = engine.emit

    def emit(t, it, amount, exc, rule, ev=""):
        iban = getattr(t, "cp_iban", None)
        log.append((it, it.open, len(engine.settled_by[it.id]), iban, engine.iban_learned.get(iban)))
        return inner_emit(t, it, amount, exc, rule, ev)

    engine.emit = emit
    try:
        return fn()
    except Exception as error:  # noqa: BLE001 - cualquier fallo de la regla deja el movimiento como estaba
        for it, open_before, n_settled, iban, learned in reversed(log):
            it.open = open_before
            del engine.settled_by[it.id][n_settled:]
            if iban:
                if learned is None:
                    engine.iban_learned.pop(iban, None)
                else:
                    engine.iban_learned[iban] = learned
        del engine.rows[n_rows:]
        _warn_once(where, error)
        return fallback()
    finally:
        if had_own_emit:
            engine.emit = inner_emit
        else:
            del engine.emit


# --------------------------------------------------------------- regla 1: sobrepago según la forma del exceso
# POLICIES §3 OVERPAYMENT: el cliente paga más que el saldo de UNA factura; la factura queda cerrada por su saldo.
# En los 2.838 sobrepagos de TRAIN el exceso es siempre +1, 2, 5, 10, 50 o 100 unidades, o +1 % o +10 % (al céntimo).
# Con esa forma es sobrepago aunque pase del 10 % que usa settle; sin ella, la diferencia es otra cosa (otra factura que
# no vemos, un saldo mal llevado) y no se marca OVERPAYMENT.
OVERPAY_STEPS_CENTS = (100, 200, 500, 1000, 5000, 10000)
OVERPAY_RATES = (1.01, 1.10)
ROUNDING_CENTS = 1


def overpaid(cash: int, open_cents: int) -> bool:
    """El cobro supera el saldo con la forma de un sobrepago."""
    excess = cash - open_cents
    if excess <= 2 * ROUNDING_CENTS:
        return False
    if any(abs(excess - step) <= ROUNDING_CENTS for step in OVERPAY_STEPS_CENTS):
        return True
    return any(abs(cash - round(open_cents * rate)) <= ROUNDING_CENTS for rate in OVERPAY_RATES)


def _single_invoice(engine, t, cands):
    """La única factura del cobro, en la divisa del banco y sin abonos; si no, None."""
    if t.amount <= 0:
        return None
    items = [engine.items[c] for c in dict.fromkeys(cands)]
    if len(items) != 1:
        return None
    it = items[0]
    if engine.sign(it, t) <= 0 or it.ccy != t.ccy or it.open <= 0:
        return None
    return it


def _wrap_settle(engine) -> None:
    inner = engine.settle

    def settle(t, cands, rule, ev="", party=None):
        def rule_1():
            it = _single_invoice(engine, t, cands)
            if it is not None and overpaid(abs(t.amount), it.open):
                engine.emit(t, it, it.open, "OVERPAYMENT", rule, ev)
                return True
            n_rows = len(engine.rows)
            done = inner(t, cands, rule, ev, party)
            if it is not None:
                for row in engine.rows[n_rows:]:
                    if row.exception == "OVERPAYMENT" and row.item == it.id:
                        row.exception = ""  # sobra dinero sin forma de sobrepago: no se afirma la excepción
            return done

        return guarded(engine, "settle", rule_1, lambda: inner(t, cands, rule, ev, party))

    engine.settle = settle


def prepare(engine) -> None:
    _wrap_settle(engine)


def _match(engine, t) -> bool:
    return False


def match(engine, t) -> bool:
    return guarded(engine, "match", lambda: _match(engine, t), lambda: False)
