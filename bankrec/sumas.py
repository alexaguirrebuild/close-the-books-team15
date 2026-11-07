"""Qué combinaciones de importes suman exactamente un objetivo, para saber si la solución es única."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence


def _partial_sums(values: Sequence[tuple[int, int]]) -> list[tuple[int, tuple[int, ...]]]:
    sums: list[tuple[int, tuple[int, ...]]] = [(0, ())]
    for position, value in values:
        sums = [*sums, *((total + value, chosen + (position,)) for total, chosen in sums)]
    return sums


def exact_subsets(values: Sequence[int], target: int, tolerance: int = 2, limit: int = 2) -> list[tuple[int, ...]]:
    """Posiciones de las combinaciones no vacías que suman target, hasta `limit`: con 2 ya se sabe que no es única.

    Encuentro a medio camino: 2^(n/2) por lado, así que n = 18 cuesta unos 1.000 pasos, no 260.000.
    """
    positions = list(enumerate(values))
    middle = len(positions) // 2
    by_total: dict[int, list[tuple[int, ...]]] = defaultdict(list)
    for total, chosen in _partial_sums(positions[middle:]):
        by_total[total].append(chosen)
    found: list[tuple[int, ...]] = []
    for total, chosen in _partial_sums(positions[:middle]):
        for offset in range(-tolerance, tolerance + 1):
            for other in by_total.get(target - total + offset, ()):
                if chosen or other:
                    found.append(chosen + other)
                    if len(found) >= limit:
                        return found
    return found
