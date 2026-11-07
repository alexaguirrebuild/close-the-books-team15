"""Plugin: un nombre cortado por el banco, solo si el importe exacto señala a un único cliente (POLICIES §5.5).

`scan_names` solo admite el corte al final del texto, y no cuando la palabra cortada es justo la última:
`NOVA ESPERANCA DO SUL JAR`. Tampoco lo ve en medio: `FP AQUATICS HARRIS 20 SERVICE`. Aquí se exige más evidencia que
allí: dos palabras completas, importe exacto y un solo candidato. Si no, sigue sin haber respuesta.
"""
from __future__ import annotations

import re

from .data import name_key
from .nombres_sociedad import parties_by_entity

MIN_FULL_WORDS = 2
EXACT_CENTS = 2
INVOICE_KINDS = {"ARI"}


def is_cut_name(tokens: list[str], start: int, name_tokens: list[str]) -> bool:
    """Empieza en `start` con al menos dos palabras completas, y la siguiente del texto es el principio de la del nombre."""
    full = 0
    while full < len(name_tokens) and tokens[start + full:start + full + 1] == [name_tokens[full]]:
        full += 1
    if full < MIN_FULL_WORDS or full == len(name_tokens) or start + full >= len(tokens):
        return False
    word, expected = tokens[start + full], name_tokens[full]
    return word != expected and expected.startswith(word)


def cut_names(engine, t, parties: set[str]) -> set[str]:
    """Los terceros de `parties` cuyo nombre aparece cortado en alguna parte del texto del movimiento."""
    segments = [name_key(part) for part in re.split(r"\|", " | ".join(filter(None, (t.text, t.rmt, t.ref1, t.ref2))))]
    found: set[str] = set()
    for segment in segments:
        tokens = segment.split()
        for start, token in enumerate(tokens):
            for party in engine.ph.name_index.get(token, ()):
                if party in parties and any(is_cut_name(tokens, start, name.split()) for name in engine.ph.party_names.get(party, ())):
                    found.add(party)
    return found


def has_exact_invoice(engine, party: str, t) -> bool:
    """Si alguna factura abierta del tercero vale exactamente lo cobrado. Es la evidencia que exige el nombre cortado."""
    return any(abs(engine.to_bank(item.open, item, t) - t.amount) <= EXACT_CENTS for item in engine.open_items_of(party, t, INVOICE_KINDS))


def prepare(engine) -> None:
    """Envuelve `scan_names`: añade el nombre cortado solo si el importe exacto señala a un único cliente."""
    original = engine.scan_names
    parties = parties_by_entity(engine)
    cut_cache: dict[tuple[str, str], set[str]] = {}

    def scan_names(t):
        """Lo que ya encontraba el motor y, solo en cobros, el único cliente con nombre cortado e importe exacto."""
        hits = original(t)
        if t.amount <= 0 or any(has_exact_invoice(engine, p, t) for p in hits):
            return hits
        tid = getattr(t, "id", None)
        key = (tid, t.entity) if tid is not None else None
        if key is not None and key in cut_cache:
            cands = cut_cache[key]
        else:
            cands = cut_names(engine, t, parties.get(t.entity, set()))
            if key is not None:
                cut_cache[key] = cands
        exact = [party for party in cands if has_exact_invoice(engine, party, t)]
        return [*hits, exact[0]] if len(exact) == 1 and exact[0] not in hits else hits

    engine.scan_names = scan_names
