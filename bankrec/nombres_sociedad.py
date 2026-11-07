"""Plugin: un nombre en el texto del banco se busca primero entre los terceros de la sociedad (POLICIES §5.5).

`scan_names` se queda con el nombre más largo de cualquier sociedad: en un cobro de CN01, un homónimo de EE. UU. con una
palabra más le gana al cliente de verdad. Si en la sociedad no aparece nadie, el resultado es el de siempre.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping

from .engine import PAYMENT_FACTORY


def parties_by_entity(engine) -> dict[str, set[str]]:
    """Terceros con partidas en cada sociedad. La fábrica de pagos también responde por las partidas de sus filiales."""
    found: dict[str, set[str]] = defaultdict(set)
    for item in engine.ph.items.values():
        found[item.entity].add(item.party)
    for payer, subsidiaries in PAYMENT_FACTORY.items():
        for subsidiary in subsidiaries:
            found[payer] = found[payer] | found[subsidiary]
    return dict(found)


def scoped_index(name_index: Mapping[str, set[str]], parties: set[str]) -> dict[str, set[str]]:
    """El índice de nombres reducido a `parties`, sin las palabras que se quedan sin ningún tercero."""
    scoped = {token: set(ids) & parties for token, ids in name_index.items()}
    return {token: ids for token, ids in scoped.items() if ids}


def prepare(engine) -> None:
    """Envuelve `scan_names` para buscar primero en la sociedad del movimiento. Los índices se crean la primera vez que se usan."""
    original = engine.scan_names
    parties = parties_by_entity(engine)
    indexes: dict[str, dict[str, set[str]]] = {}

    def scan_names(t):
        """Los nombres encontrados entre los terceros de la sociedad; si no hay ninguno, la búsqueda de siempre."""
        whole = engine.ph.name_index
        if t.entity not in indexes:
            indexes[t.entity] = scoped_index(whole, parties.get(t.entity, set()))
        engine.ph.name_index = indexes[t.entity]
        try:
            hits = original(t)
        finally:
            engine.ph.name_index = whole
        return hits or original(t)

    engine.scan_names = scan_names
