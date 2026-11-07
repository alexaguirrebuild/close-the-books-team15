"""Genera el fichero de una fase de principio a fin y para en el primer fallo. Nunca sube nada.

    uv run python scripts/final_run.py                 # FINAL
    uv run python scripts/final_run.py --phase live    # ensayo cronometrado

Encadena ingesta, enlace de avisos, motor y validación estricta. El envío lo lanza una persona con scripts/submit.py.
El procedimiento completo está en docs/RUNBOOK_FINAL.md.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BANKCHECK = ("uv", "run", "--project", "pipeline", "bankcheck")
ENGINE = ("uv", "run", "python", "-m", "bankrec.run")
INGEST, LINK, RUN, VALIDATE = "Ingesta", "Enlace de avisos", "Motor", "Validación estricta"
PHASES = ("final", "live", "sample")
MAX_SAMPLES = 5
MIN_SHARE_WITH_ITEM = 0.5
FAILURE_HINTS = {
    INGEST: "Mira out/{phase}/ingest_report.json y cache/warehouse/{phase}.failed.duckdb. Qué hacer con cada código: docs/RUNBOOK_FINAL.md.",
    LINK: "Sin este paso el motor no arranca en LIVE ni en FINAL. Mira out/{phase}/match.log.",
    RUN: "El motor se ha parado. El mensaje de arriba dice por qué; preflight avisa de lo que falte en data/.",
    VALIDATE: "Mira out/{phase}/validate__sub_{phase}.json. Si el único error es INGEST_HAD_ERRORS y el formato oficial está OK, decidís vosotros.",
}


@dataclass(frozen=True)
class Step:
    name: str
    command: tuple[str, ...]


def out_dir() -> Path:
    return Path(os.environ.get("BANKREC_OUT", ROOT / "out"))


def data_dir() -> Path:
    return Path(os.environ.get("BANKREC_DATA", ROOT / "data"))


def plan(phase: str, allow_ingest_errors: bool = False) -> list[Step]:
    ingest_flags = ("--allow-errors",) if allow_ingest_errors else ()
    return [
        Step(INGEST, (*BANKCHECK, "ingest", "--phase", phase, *ingest_flags)),
        Step(LINK, (*BANKCHECK, "match", "--phase", phase)),
        Step(RUN, (*ENGINE, "--phase", phase, "--no-score")),
        Step(VALIDATE, (*BANKCHECK, "validate", "--phase", phase, str(out_dir() / f"sub_{phase}.csv.gz"))),
    ]


def clear_stale(phase: str) -> None:
    """Un fichero antiguo también valida: si algo falla a medias, no puede quedar con el nombre del que se envía."""
    out = out_dir()
    (out / f"pdf_text_{phase}.json").unlink(missing_ok=True)
    for name in (f"sub_{phase}", f"explain_{phase}"):
        previous = out / f"{name}.csv.gz"
        if previous.is_file():
            previous.replace(out / f"{name}.anterior.csv.gz")


def tolerated(phase: str, step: Step, allow_ingest_errors: bool) -> bool:
    """Con --allow-errors la ingesta sale con error aunque publique: solo se sigue si de verdad publicó el almacén."""
    if not allow_ingest_errors or step.name != INGEST:
        return False
    report = ROOT / "out" / phase / "ingest_report.json"
    if not report.is_file():
        return False
    return json.loads(report.read_text(encoding="utf-8")).get("published") is True


def run_steps(
    steps: Sequence[Step],
    execute: Callable[[tuple[str, ...]], int],
    may_continue: Callable[[Step], bool],
) -> tuple[int, list[tuple[str, float, int]]]:
    """Devuelve el código de salida y lo que tardó cada paso."""
    times: list[tuple[str, float, int]] = []
    for step in steps:
        print(f"\n=== {step.name}: {' '.join(step.command)}", flush=True)
        started = time.monotonic()
        code = execute(step.command)
        times.append((step.name, time.monotonic() - started, code))
        if code != 0 and not may_continue(step):
            return code, times
    return 0, times


def _read_rows(path: Path) -> Iterator[dict[str, str]]:
    handle = gzip.open(path, "rt", encoding="utf-8", newline="") if path.suffix == ".gz" else path.open(encoding="utf-8-sig", newline="")
    with handle:
        yield from csv.DictReader(handle)


def _outcome(rows: Sequence[dict[str, str]]) -> str:
    if any(row["item_id"] for row in rows):
        return "con partida"
    category = rows[0]["category"] if rows else ""
    return category if category in ("UNIDENTIFIED", "BANK_FEES") else "otra categoría"


def new_entities_report(phase: str) -> list[str]:
    """Las sociedades que los maestros anteriores no conocían son lo único que el motor no ha visto nunca: se revisan a mano."""
    known = {row["entity"] for row in _read_rows(data_dir() / "masters" / "entities.csv")}
    lines = {row["bank_txn_id"]: row for row in _read_rows(data_dir() / phase / "bank_transactions.csv.gz") if row["entity"] not in known}
    if not lines:
        return ["Sociedades nuevas respecto a masters/: Ninguna."]
    answers: dict[str, list[dict[str, str]]] = {}
    for row in _read_rows(out_dir() / f"explain_{phase}.csv.gz"):
        if row["bank_txn_id"] in lines:
            answers.setdefault(row["bank_txn_id"], []).append(row)
    report = ["Sociedades nuevas respecto a masters/. Revísalas a mano:"]
    for entity in sorted({line["entity"] for line in lines.values()}):
        report.extend(_entity_lines(entity, lines, answers))
    return report


def _entity_lines(entity: str, lines: dict[str, dict[str, str]], answers: dict[str, list[dict[str, str]]]) -> list[str]:
    ids = sorted(txn for txn, line in lines.items() if line["entity"] == entity)
    outcomes = Counter(_outcome(answers.get(txn, [])) for txn in ids)
    alert = "  REVISAR: menos de la mitad tiene partida" if outcomes["con partida"] < MIN_SHARE_WITH_ITEM * len(ids) else ""
    summary = " · ".join(f"{name} {outcomes[name]}" for name in ("con partida", "UNIDENTIFIED", "BANK_FEES", "otra categoría"))
    result = [f"  {entity}: {len(ids)} movimientos · {summary}{alert}"]
    without_item = [txn for txn in ids if _outcome(answers.get(txn, [])) != "con partida"]
    for txn in without_item[:MAX_SAMPLES]:
        line = lines[txn]
        result.append(f"    {txn} {line['amount']} {line['currency']} · {_outcome(answers.get(txn, []))} · {line['text']}")
    return result


def next_step(phase: str) -> str:
    if phase != "final":
        return f"Terminado. Este comando no ha subido nada. Fichero: out/sub_{phase}.csv.gz"
    return (
        "Fichero listo y validado: out/sub_final.csv.gz. Este comando no ha subido nada.\n"
        "Antes de enviar, sigue las comprobaciones de docs/RUNBOOK_FINAL.md.\n"
        "El envío se hace una sola vez, con el OK expreso del usuario, y lo lanza él:\n"
        "  ! python scripts/submit.py FINAL out/sub_final.csv.gz"
    )


def print_times(times: Sequence[tuple[str, float, int]]) -> None:
    print("\nTiempos:")
    for name, seconds, code in times:
        print(f"  {name:22s} {seconds:7.1f} s  salida {code}")
    print(f"  {'Total':22s} {sum(seconds for _, seconds, _ in times):7.1f} s", flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--phase", default="final", choices=PHASES)
    parser.add_argument("--allow-ingest-errors", action="store_true", help="sigue si la ingesta publica con errores; ver docs/RUNBOOK_FINAL.md antes de usarlo")
    args = parser.parse_args(argv)
    clear_stale(args.phase)
    steps = plan(args.phase, args.allow_ingest_errors)
    code, times = run_steps(
        steps,
        lambda command: subprocess.run(list(command), cwd=ROOT, check=False).returncode,
        lambda step: tolerated(args.phase, step, args.allow_ingest_errors),
    )
    print_times(times)
    if code != 0:
        failed = times[-1][0]
        print(f"\nPARADO en «{failed}». No hay fichero listo para enviar.\n{FAILURE_HINTS[failed].format(phase=args.phase)}", flush=True)
        return code
    print()
    print("\n".join(new_entities_report(args.phase)))
    print(f"\n{next_step(args.phase)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
