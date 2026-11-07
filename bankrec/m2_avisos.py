"""Plugin: filas de los avisos de remesa enlazados con su cobro.

El enlace aviso-movimiento lo calcula `bankcheck match`, sobre el almacén de la fase, antes de lanzar el motor. Sus filas
siguen POLICIES §5.4 y entran aquí como cualquier otra regla, gastando saldo, para que otro movimiento no use esas partidas.
En el holdout suben la nota de 82,33 a 83,95 sobre el motor sin él.
"""
from __future__ import annotations

import os
from collections import defaultdict
from pathlib import Path

from .data import cents, rows
from .engine import Row

STAGE_FILE = Path("matches") / "m2_remittance.csv.gz"
REQUIRED_IN = ("live", "final")
DEFAULT_ROOT = Path(__file__).resolve().parents[1] / "out"


def _stage_path(phase: str) -> Path:
    base = os.environ.get("BANKREC_M2_DIR") or os.environ.get("BANKREC_OUT") or DEFAULT_ROOT
    return Path(base) / phase / STAGE_FILE


def prepare(engine) -> None:
    phase = engine.ph.name
    path = _stage_path(phase)
    engine.m2_rows = {}
    if not path.is_file():
        if phase in REQUIRED_IN:
            raise SystemExit(f"Falta {path}. Sin él se pierden unos 1,6 puntos. Genéralo con `bankcheck ingest` y `bankcheck match --phase {phase}`.")
        print(f"  AVISO: sin filas de avisos enlazados en {path}", flush=True)
        return
    grouped = defaultdict(list)
    for row in rows(path):
        if row["item_id"]:
            grouped[row["bank_txn_id"]].append(row)
    engine.m2_rows = dict(grouped)
    print(f"  avisos enlazados: {len(grouped)} movimientos · {path}", flush=True)


def match(engine, t) -> bool:
    lines = engine.m2_rows.get(t.id)
    if not lines:
        return False
    for line in lines:
        amount = cents(line["amount"])
        evidence = f"aviso {line['advice_id']}"
        item = engine.items.get(line["item_id"])
        if item is None:
            engine.rows.append(Row(t.id, line["item_id"], amount, "", line["exception"], "m2_avisos", evidence))
        else:
            engine.emit(t, item, amount, line["exception"], "m2_avisos", evidence)
    return True
