"""Lo que se comprueba antes de generar un fichero: en FINAL no hay segunda oportunidad."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REQUIRED_PACKAGES = ("pypdf", "openpyxl")
HASH_SEED_VARIABLE = "PYTHONHASHSEED"
HASH_SEED = "0"
PRIOR_PHASES = {"live": ("train",), "final": ("train", "live")}
MASTER_FILES = ("entities.csv", "bank_accounts.csv", "customers.csv", "suppliers.csv", "categories.csv", "payment_terms.csv", "fx_rates.csv")


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
    """El orden de los sets depende de esta semilla y Python solo la lee al arrancar, así que hay que relanzar.

    Con subprocess y no con os.execv: en Windows os.execv no espera al hijo (el proceso sale con 0 al instante) y no
    pone comillas a los argumentos, así que con un espacio en la ruta del intérprete el motor no llegaba a ejecutarse."""
    if os.environ.get(HASH_SEED_VARIABLE) == HASH_SEED:
        return
    env = {**os.environ, HASH_SEED_VARIABLE: HASH_SEED}
    raise SystemExit(subprocess.call([sys.executable, "-m", "bankrec.run", *argv], env=env))


def check_phase_inputs(phase: str, data: Path) -> None:
    """Sin partidas de apertura o sin fases anteriores el motor no falla: los cobros de partidas antiguas salen UNIDENTIFIED."""
    if phase not in PRIOR_PHASES:
        return
    problems = []
    opening = data / phase / "open_items_at_start.csv.gz"
    if not opening.is_file():
        problems.append(f"falta {opening}")
    problems.extend(f"falta la fase anterior {prior}: {data / prior}" for prior in PRIOR_PHASES[phase] if not (data / prior / "ar_invoices.csv.gz").is_file())
    if phase == "final" and not (data / "final" / "masters_final").is_dir():
        problems.append("falta final/masters_final: sin él no están la filial nueva ni los cambios del semestre")
    if problems:
        raise SystemExit(f"La fase {phase} no está completa:\n  " + "\n  ".join(problems))
    if phase == "final":
        warn_incomplete_final_masters(data)


def warn_incomplete_final_masters(data: Path) -> None:
    """No para: se usa el maestro anterior y el fichero sigue siendo válido. Pero lo nuevo del semestre no estará, y hay que saberlo."""
    folder = data / "final" / "masters_final"
    missing = [name for name in MASTER_FILES if not (folder / name).is_file()]
    if missing:
        print(f"  AVISO: masters_final no trae {', '.join(missing)}. Se usa masters/, sin lo nuevo del semestre: ver docs/RUNBOOK_FINAL.md.", flush=True)
