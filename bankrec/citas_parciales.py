"""Plugin del lote 1 (citas incompletas): completar la lista de facturas que el banco corta (POLICIES §5.5).

La regla `ref` asigna lo que cita el texto y, cuando lo citado suma menos que el cobro, `settle` deja el resto sin
asignar. `Engine._complete_cited` ya busca el resto por suma exacta única entre las abiertas del cliente, pero se
rinde con más de 18 candidatas o con varias combinaciones; y con 12-18 candidatas una «solución única» es falsa el
5-17 % de las veces. Aquí cada predicado define UN conjunto determinista de partidas no citadas del mismo cliente y
solo vale si, sumado a lo citado, da el cobro exacto (±2 céntimos, abonos restando). Solo se busca subconjunto exacto
con 8 candidatas o menos. Los predicados van en orden de más a menos preciso, medido en estático sobre el holdout
(ver la PR); en un cobro gana el primero que cuadra.

Medido: los conjuntos que incluyen todos los abonos abiertos del cliente bajan a 0,80-0,90 de precisión (arrastran
pares abono+factura antiguos que se anulan), así que casi todos los predicados trabajan solo con facturas; los abonos
entran por la numeración del texto o por el subconjunto pequeño.

Cómo encaja: `prepare` envuelve `engine._complete_cited(t, cited, party)`. Primero decide el método original (y lo que
otras ramas le añadan, como la búsqueda entre vencidas de `_complete_with_due`); solo si no devuelve nada entran estos
predicados. No toca `settle` ni `m_party_amount`, que envuelven otros lotes. `match` no emite nada. Las filas que
completa llevan `rule=ref_completed_cp` y el predicado en `evidence`.

Si algo falla dentro, se avisa una vez y se devuelve lo que habría devuelto el motor: un plugin que lanza una
excepción tumba la fase entera.
"""
from __future__ import annotations

import datetime as dt
import re
import sys

from .data import ascii_upper, text_tokens

AMOUNT_TOLERANCE_CENTS = 2
KINDS = {"ARI", "ARC"}
MAX_SMALL_SUBSET = 8  # con más candidatas, una suma exacta única no identifica (auditoría del equipo)
RECENT_DAYS = 365
DIGITS = re.compile(r"\d+")

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


def _missing(engine, t, cited) -> float:
    return abs(t.amount) - sum(_signed_bank(engine, it, t) for it in cited)


def _adds_up(engine, extra, t, missing) -> bool:
    return bool(extra) and abs(sum(_signed_bank(engine, it, t) for it in extra) - missing) <= AMOUNT_TOLERANCE_CENTS


def _norm(number) -> str:
    return re.sub(r"[^A-Z0-9]", "", ascii_upper(number or ""))


def _forms(number) -> set[str]:
    """Formas de un número de documento: alfanumérico, solo dígitos, última tira de dígitos, sin ceros a la izquierda."""
    a = _norm(number)
    d = re.sub(r"\D", "", a)
    runs = DIGITS.findall(a)
    out = {a}
    for x in (d, runs[-1] if runs else ""):
        if len(x) >= 4:
            out.add(x)
            out.add(x.lstrip("0"))
    out.discard("")
    return out


def _dist1(a: str, b: str) -> bool:
    """Una cifra cambiada o dos adyacentes transpuestas, misma longitud (POLICIES §5.5: typos y transposiciones)."""
    if len(a) != len(b):
        return False
    diff = [i for i in range(len(a)) if a[i] != b[i]]
    if len(diff) == 1:
        return True
    return len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]


def _fields(t) -> list[str]:
    return [getattr(t, k, "") or "" for k in ("text", "ref1", "ref2", "rmt")]


# ------------------------------------------------------------------ predicados: (engine, t, cited, others) -> lista o None
# `others`: partidas abiertas (ARI/ARC) del cliente no citadas, misma divisa que el cobro, ordenadas por emisión.
def _ari(others):
    return [it for it in others if it.kind == "ARI"]


def numeracion(engine, t, cited, others):
    """No citadas cuyo número aparece en el texto con una cifra cambiada o transpuesta, sin prefijo ni ceros, o como
    sufijo de 6-7 dígitos de una tira más larga (POLICIES §5.5). Es el conjunto entero de las que casan."""
    cited_forms = set().union(*(_forms(it.number) for it in cited)) if cited else set()
    fields = _fields(t)
    toks = {tk for tk in text_tokens(*fields) if len(re.sub(r"\D", "", tk)) >= 5 and tk not in cited_forms}
    runs = set()
    for f in fields:
        for d in DIGITS.findall(re.sub(r"\s*\|\s*", "", ascii_upper(f))):
            if len(d) >= 6:
                runs.add(d)
    cited_digits = {re.sub(r"\D", "", f) for f in cited_forms}
    sufs = {d[-n:] for d in runs for n in (6, 7) if len(d) >= n}
    sufs = {s for s in sufs if not any(cd.endswith(s) for cd in cited_digits if len(cd) >= 6)}
    out = []
    for it in others:
        forms = _forms(it.number)
        digits = {re.sub(r"\D", "", f) for f in forms}
        hit = any(x.endswith(s) and len(x) >= 6 for s in sufs for x in digits)
        for tk in () if hit else toks:
            td = re.sub(r"\D", "", tk)
            for f in forms:
                fd = re.sub(r"\D", "", f)
                if tk == f or td == fd or _dist1(tk, f) or (len(td) >= 5 and _dist1(td, fd)) or (
                        len(td) >= 5 and fd.endswith(td) and len(fd) - len(td) <= 2 and not fd[: len(fd) - len(td)].strip("0")):
                    hit = True
                    break
            if hit:
                break
        if hit:
            out.append(it)
    return out or None


def fifo(engine, t, cited, others):
    """Las facturas no citadas más antiguas, una a una, hasta cuadrar (POLICIES §5.6: se paga lo más antiguo)."""
    missing = _missing(engine, t, cited)
    acc, picked = 0.0, []
    for it in _ari(others):
        acc += _signed_bank(engine, it, t)
        picked.append(it)
        if abs(acc - missing) <= AMOUNT_TOLERANCE_CENTS:
            return picked
        if acc > missing + AMOUNT_TOLERANCE_CENTS:
            return None
    return None


def emitidas_cerca(dias: int):
    """Todas las facturas no citadas emitidas en el rango de emisión de las citadas, ampliado `dias` por cada lado."""
    def pred(engine, t, cited, others):
        issues = [it.issue for it in cited if it.issue is not None]
        if not issues:
            return None
        lo, hi = min(issues) - dt.timedelta(days=dias), max(issues) + dt.timedelta(days=dias)
        return [it for it in _ari(others) if it.issue is not None and lo <= it.issue <= hi]
    pred.__name__ = f"emitidas_cerca_{dias}d"
    return pred


def subconjunto_pequeno(engine, t, cited, others):
    """Suma exacta única solo con pocas candidatas: con más, la unicidad es falsa demasiadas veces."""
    if not 1 <= len(others) <= MAX_SMALL_SUBSET:
        return None
    from .sumas import exact_subsets
    values = [int(round(_signed_bank(engine, it, t))) for it in others]
    sols = exact_subsets(values, int(round(_missing(engine, t, cited))), AMOUNT_TOLERANCE_CENTS)
    return [others[i] for i in sols[0]] if len(sols) == 1 else None


def mismo_mes_emision(engine, t, cited, others):
    """Todas las facturas no citadas emitidas en los mismos meses que las citadas."""
    months = {(it.issue.year, it.issue.month) for it in cited if it.issue is not None}
    if not months:
        return None
    return [it for it in _ari(others) if it.issue is not None and (it.issue.year, it.issue.month) in months]


def misma_semana_vencimiento(engine, t, cited, others):
    """Todas las facturas no citadas que vencen en la misma semana ISO que alguna citada (pago por vencimiento)."""
    weeks = {it.due.isocalendar()[:2] for it in cited if it.due is not None}
    if not weeks:
        return None
    return [it for it in _ari(others) if it.due is not None and it.due.isocalendar()[:2] in weeks]


def vencimiento_entre_citadas(dias: int):
    """Todas las facturas no citadas con vencimiento entre el primero y el último citados, con margen."""
    def pred(engine, t, cited, others):
        dues = [it.due for it in cited if it.due is not None]
        if not dues:
            return None
        lo, hi = min(dues) - dt.timedelta(days=dias), max(dues) + dt.timedelta(days=dias)
        return [it for it in _ari(others) if it.due is not None and lo <= it.due <= hi]
    pred.__name__ = f"vencimiento_entre_citadas_{dias}d"
    return pred


def vencidas_recientes(engine, t, cited, others):
    """Todas las facturas no citadas vencidas a la fecha del cobro y emitidas en el último año."""
    since = t.date - dt.timedelta(days=RECENT_DAYS)
    return [it for it in _ari(others) if it.due is not None and it.due <= t.date and (it.issue is None or it.issue >= since)]


def numeracion_pequeno(engine, t, cited, others):
    """Si las que casan por numeración son 8 o menos y no cuadran enteras: el único subconjunto de ellas que cuadra."""
    pool = numeracion(engine, t, cited, others)
    if not pool or len(pool) > MAX_SMALL_SUBSET:
        return None
    return subconjunto_pequeno(engine, t, cited, pool)


def fifo_con_salto(engine, t, cited, others):
    """FIFO que salta las facturas que no caben en lo que falta; vale si al final cuadra (precisión medida 0,98)."""
    remaining = _missing(engine, t, cited)
    picked = []
    for it in _ari(others):
        v = _signed_bank(engine, it, t)
        if v <= remaining + AMOUNT_TOLERANCE_CENTS:
            picked.append(it)
            remaining -= v
    return picked if picked and abs(remaining) <= AMOUNT_TOLERANCE_CENTS else None


# Orden de aplicación: precisión estática 1,000 primero (por filas netas), luego 0,996 y 0,984. Ver la tabla de la PR.
PREDICADOS = [
    numeracion,
    fifo,
    emitidas_cerca(30),
    subconjunto_pequeno,
    emitidas_cerca(7),
    mismo_mes_emision,
    misma_semana_vencimiento,
    vencimiento_entre_citadas(3),
    vencidas_recientes,
    numeracion_pequeno,
    fifo_con_salto,
]


# ------------------------------------------------------------------ envoltura
def completar(engine, t, cited, party):
    """El resto del cobro según el primer predicado que cuadra; None si ninguno."""
    if t.amount <= 0 or not cited or not party:
        return None
    if any(it.ccy != t.ccy for it in cited):
        return None
    missing = _missing(engine, t, cited)
    if missing <= AMOUNT_TOLERANCE_CENTS:
        return None
    known = {it.id for it in cited}
    others = [it for it in engine.open_items_of(party, t, KINDS) if it.id not in known and it.ccy == t.ccy]
    if not others:
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
