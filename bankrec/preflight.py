"""Lo que se comprueba antes de generar un fichero: en FINAL no hay segunda oportunidad."""
from __future__ import annotations

import os
import sys

REQUIRED_PACKAGES = ("pypdf", "openpyxl")
HASH_SEED_VARIABLE = "PYTHONHASHSEED"
HASH_SEED = "0"


def check_dependencies() -> None:
    """Sin estas librerías el motor no falla: pierde los avisos en PDF y XLSX y baja la nota sin decir nada."""
    missing = []
    for package in REQUIRED_PACKAGES:
        try:
            __import__(package)
        except ImportError:
            missing.append(package)
    if missing:
        raise SystemExit(
            f"Faltan dependencias obligatorias: {', '.join(missing)}. Lanza el motor con `uv run python -m bankrec.run ...`."
        )


def ensure_deterministic(argv: list[str]) -> None:
    """El orden de los sets depende de esta semilla y Python solo la lee al arrancar, así que hay que relanzar."""
    if os.environ.get(HASH_SEED_VARIABLE) == HASH_SEED:
        return
    os.environ[HASH_SEED_VARIABLE] = HASH_SEED
    os.execv(sys.executable, [sys.executable, "-m", "bankrec.run", *argv])
