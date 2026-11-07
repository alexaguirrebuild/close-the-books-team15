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


def prepare(engine) -> None:
    return None


def _match(engine, t) -> bool:
    return False


def match(engine, t) -> bool:
    return guarded(engine, "match", lambda: _match(engine, t), lambda: False)
