"""Plugin del lote 1 (citas incompletas): completar la lista de facturas que el banco corta (POLICIES §5.5).

La regla `ref` asigna lo que cita el texto y, cuando lo citado suma menos que el cobro, `settle` deja el resto sin
asignar. `Engine._complete_cited` ya busca el resto por suma exacta única entre las abiertas del cliente, pero se
rinde con más de 18 candidatas o con varias combinaciones; y con 12-18 candidatas una «solución única» es falsa el
5-17 % de las veces. Aquí no se buscan subconjuntos: cada predicado define UN conjunto determinista de partidas no
citadas del mismo cliente (por vencimiento, por antigüedad, por abono relacionado...) y solo vale si, sumado a lo
citado, da el cobro exacto (±2 céntimos, abonos restando). Los predicados van en orden de más a menos preciso, medido
en estático sobre el holdout; en un cobro gana el primero que cuadra.

Cómo encaja: `prepare` envuelve `engine._complete_cited(t, cited, party)`. Primero decide el método original (y lo que
otras ramas le añadan, como la búsqueda entre vencidas de `_complete_with_due`); solo si no devuelve nada entran estos
predicados. No toca `settle` ni `m_party_amount`, que envuelven otros lotes. `match` no emite nada.

Si algo falla dentro, se avisa una vez y se devuelve lo que habría devuelto el motor: un plugin que lanza una
excepción tumba la fase entera.
"""
from __future__ import annotations

import datetime as dt
import re
import sys

AMOUNT_TOLERANCE_CENTS = 2
DUE_MARGIN_DAYS = 3
KINDS = {"ARI", "ARC"}
MAX_SMALL_SUBSET = 8  # con más candidatas, una suma exacta única no identifica (auditoría del equipo)

_warned: set[str] = set()


def _warn_once(where: str, error: Exception) -> None:
    if where not in _warned:
        _warned.add(where)
        print(f"  AVISO citas_parciales: {where} falló ({type(error).__name__}: {error}); se sigue sin la regla",
              file=sys.stderr, flush=True)


# ------------------------------------------------------------------ utilidades
def _signed_bank(engine, it, t) -> float:
    """Aportación de la partida al cobro, en céntimos del banco: + factura, - abono."""
    return engine.sign(it, t) * engine.to_bank(it.open, it, t)


def _adds_up(engine, extra, t, missing) -> bool:
    return bool(extra) and abs(sum(_signed_bank(engine, it, t) for it in extra) - missing) <= AMOUNT_TOLERANCE_CENTS


def _digits(number) -> str:
    return re.sub(r"\D", "", number or "")


# ------------------------------------------------------------------ predicados: (engine, t, cited, others) -> lista o None
# `others`: partidas abiertas (ARI/ARC) del cliente no citadas, misma divisa que el cobro, ordenadas por emisión.
def todas(engine, t, cited, others):
    """El cliente paga todo lo que tiene abierto."""
    return others


def _due_ok(it, limit):
    return it.kind == "ARC" or (it.due is not None and it.due <= limit)


def vencidas(margen_dias: int):
    """Todas las facturas vencidas a la fecha del cobro (con margen) y todos los abonos."""
    def pred(engine, t, cited, others):
        limit = t.date + dt.timedelta(days=margen_dias)
        return [it for it in others if _due_ok(it, limit)]
    pred.__name__ = f"vencidas_{margen_dias}d"
    return pred


def mismo_vencimiento(engine, t, cited, others):
    """Las no citadas que vencen el mismo día que alguna citada (pago por vencimiento), más los abonos."""
    dues = {it.due for it in cited if it.due is not None}
    if not dues:
        return None
    return [it for it in others if it.kind == "ARC" or it.due in dues]


def hasta_ultimo_vencimiento_citado(engine, t, cited, others):
    """Todas las no citadas que vencen hasta el último vencimiento citado, más los abonos."""
    dues = [it.due for it in cited if it.due is not None]
    if not dues:
        return None
    limit = max(dues)
    return [it for it in others if _due_ok(it, limit)]


def fifo(engine, t, cited, others):
    """Las no citadas más antiguas, una a una, hasta cuadrar el cobro (POLICIES §5.6: se paga lo más antiguo)."""
    cash_missing = _missing(engine, t, cited)
    acc, picked = 0.0, []
    for it in others:  # ya vienen por fecha de emisión
        acc += _signed_bank(engine, it, t)
        picked.append(it)
        if abs(acc - cash_missing) <= AMOUNT_TOLERANCE_CENTS:
            return picked
        if acc > cash_missing + AMOUNT_TOLERANCE_CENTS and all(engine.sign(x, t) > 0 for x in others):
            return None
    return None


def emitidas_entre_citadas(engine, t, cited, others):
    """Todas las no citadas emitidas entre la primera y la última citada: la lista se cortó por el medio."""
    issues = [it.issue for it in cited if it.issue is not None]
    if not issues:
        return None
    lo, hi = min(issues), max(issues)
    return [it for it in others if it.issue is not None and lo <= it.issue <= hi]


def abonos_relacionados(engine, t, cited, others):
    """Los abonos cuyo documento relacionado es una factura del cobro (POLICIES §2: abono compensado)."""
    nums = {_digits(it.number) for it in cited if it.number}
    nums |= {re.sub(r"[^A-Z0-9]", "", (it.number or "").upper()) for it in cited}
    out = []
    for it in others:
        if it.kind != "ARC" or not getattr(it, "related", None):
            continue
        rel = it.related
        if _digits(rel) in nums or re.sub(r"[^A-Z0-9]", "", rel.upper()) in nums:
            out.append(it)
    return out or None


def consecutivas(engine, t, cited, others):
    """Las no citadas cuyo número sigue en cadena (±1 en la parte numérica) a alguna del cobro: lista cortada."""
    seen = {_digits(it.number) for it in cited if _digits(it.number)}
    if not seen:
        return None
    by_num = {}
    for it in others:
        d = _digits(it.number)
        if d:
            by_num[d] = it
    picked, frontier = [], list(seen)
    while frontier:
        d = frontier.pop()
        for delta in (-1, 1):
            n = str(int(d) + delta).zfill(len(d))
            it = by_num.pop(n, None)
            if it is not None:
                picked.append(it)
                frontier.append(n)
    return picked or None


def prefijo_cortado(engine, t, cited, others):
    """El último token del texto es el principio del número de una no citada: el banco cortó la lista ahí."""
    toks = re.findall(r"[A-Z0-9][A-Z0-9\-/\.]{3,}", t.up or "")
    if not toks:
        return None
    last = re.sub(r"[^A-Z0-9]", "", toks[-1])
    if len(last) < 4:
        return None
    hits = [it for it in others if re.sub(r"[^A-Z0-9]", "", (it.number or "").upper()).startswith(last)
            and len(re.sub(r"[^A-Z0-9]", "", (it.number or "").upper())) > len(last)]
    return hits if len(hits) == 1 else None


def subconjunto_pequeno(engine, t, cited, others):
    """Suma exacta única solo con pocas candidatas: con más, la unicidad es falsa demasiadas veces."""
    if not 1 <= len(others) <= MAX_SMALL_SUBSET:
        return None
    from .sumas import exact_subsets
    values = [int(round(_signed_bank(engine, it, t))) for it in others]
    sols = exact_subsets(values, int(round(_missing(engine, t, cited))), AMOUNT_TOLERANCE_CENTS)
    return [others[i] for i in sols[0]] if len(sols) == 1 else None


# Orden de aplicación: de más a menos preciso según la medición estática (ver la PR). Se fija al cerrar el panel.
PREDICADOS = [
    todas,
    abonos_relacionados,
    mismo_vencimiento,
    vencidas(0),
    vencidas(DUE_MARGIN_DAYS),
    hasta_ultimo_vencimiento_citado,
    fifo,
]


# ------------------------------------------------------------------ envoltura
def _missing(engine, t, cited) -> float:
    return abs(t.amount) - sum(_signed_bank(engine, it, t) for it in cited)


def completar(engine, t, cited, party):
    """El resto del cobro según el primer predicado que cuadra; None si ninguno."""
    if t.amount <= 0 or not cited or not party:
        return None
    missing = _missing(engine, t, cited)
    if missing <= AMOUNT_TOLERANCE_CENTS:
        return None
    known = {it.id for it in cited}
    others = [it for it in engine.open_items_of(party, t, KINDS) if it.id not in known and it.ccy == t.ccy]
    if not others or any(it.ccy != t.ccy for it in cited):
        return None
    for pred in PREDICADOS:
        extra = pred(engine, t, cited, others)
        if extra and _adds_up(engine, extra, t, missing):
            engine.citas_parciales_rule = pred.__name__
            return list(extra)
    return None


def _wrap_complete_cited(engine) -> None:
    inner = engine._complete_cited

    def _complete_cited(t, cited, party):
        found = inner(t, cited, party)
        if found:
            return found
        try:
            return completar(engine, t, cited, party)
        except Exception as error:  # noqa: BLE001 - la regla nunca tumba la fase
            _warn_once("completar", error)
            return None

    engine._complete_cited = _complete_cited


def _wrap_m_refs(engine) -> None:
    """Solo trazabilidad: las filas que completa este plugin llevan el predicado en `rule` y `evidence`."""
    inner = engine.m_refs

    def m_refs(t):
        engine.citas_parciales_rule = None
        n0 = len(getattr(engine, "rows", ()))
        done = inner(t)
        try:
            pred = engine.citas_parciales_rule
            if done and pred:
                for row in getattr(engine, "rows", ())[n0:]:
                    if row.rule == "ref_completed":
                        row.rule = "ref_completed_cp"
                        row.evidence = f"{row.evidence} · citas_parciales:{pred}"
        except Exception as error:  # noqa: BLE001
            _warn_once("m_refs", error)
        return done

    engine.m_refs = m_refs


def prepare(engine) -> None:
    try:
        _wrap_complete_cited(engine)
        _wrap_m_refs(engine)
    except Exception as error:  # noqa: BLE001
        _warn_once("prepare", error)


def match(engine, t) -> bool:
    return False
