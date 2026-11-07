"""Score against the golden with the official scorer. Only phases with labels. Within submission/, only this module reads golden."""
from __future__ import annotations

import csv
import gzip
import logging
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ..roots import STATEMENTS_HOLD, Layout
from .official import EXCEPTIONS, SCORER_SHA256, SUBMISSION_HEADER, lift_limits, load_official_scorer, read_categories
from .writer import report_path, write_json, write_submission

GOLDEN_FILE = "golden_allocations.csv.gz"
SELF_CHECK_FILE = "golden_self_check.csv.gz"
PHASES_WITH_GOLDEN = ("train", "sample")
WEIGHTS = {"allocation_f1": 0.5, "txn_accuracy": 0.3, "exception_macro_f1": 0.2}
logger = logging.getLogger("bankcheck")


def _require_labels(layout: Layout) -> None:
    if layout.phase not in PHASES_WITH_GOLDEN:
        raise ValueError(f"la fase {layout.phase} no tiene golden: la puntuación oficial la da la plataforma y consume cupo")


def score_submission_file(layout: Layout, path: Path) -> dict:
    _require_labels(layout)
    target = report_path(layout, "score", path)
    target.unlink(missing_ok=True)  # Whatever fails below, an older score must not pass for this file's.
    scorer = lift_limits(load_official_scorer(layout))
    golden = scorer.load_golden(
        str(layout.phase_dir / GOLDEN_FILE),
        str(layout.phase_dir / STATEMENTS_HOLD),
        list(EXCEPTIONS),
        read_categories(layout.masters_dir),
    )
    result = {
        **scorer.score_submission(golden, path.read_bytes(), path.name),
        "phase": layout.phase,
        "file": str(path),
        "scorer_sha256": SCORER_SHA256,
        "limits_lifted": True,
    }
    write_json(target, result)
    return {**result, "report_path": str(target)}


@contextmanager
def golden_as_submission(layout: Layout) -> Iterator[Path]:
    """The labels in the submission format, to prove the gate and the scorer agree with them.
    The file lives in the phase's report folder only for the duration of the block, and is never uploaded."""
    _require_labels(layout)
    layout.report_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=layout.report_dir, prefix=".golden_self_check.") as folder:
        path = Path(folder) / SELF_CHECK_FILE
        with gzip.open(layout.phase_dir / GOLDEN_FILE, "rt", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader)
            positions = [header.index(column) for column in SUBMISSION_HEADER]
            write_submission(path, ([row[position] for position in positions] for row in reader))
        yield path


def print_score(result: dict) -> None:
    if not result["ok"]:
        logger.error("Fichero rechazado por el scorer oficial:\n%s", "\n".join(f"  fila {e['row']}: {e['message']}" for e in result["errors"][:20]))
        return
    metrics = result["metrics"]
    lines = [
        f"\nPUNTUACIÓN: {result['score']:.2f}   (scorer oficial sin modificar; solo se levantan sus 3 límites de tamaño porque TRAIN no se envía)",
        f"  Scorer SHA-256: {result['scorer_sha256']}",
        "",
        f"  {'Métrica':24s} {'Valor':>8s} {'Peso':>6s} {'Aporta':>8s}",
    ]
    for name, weight in WEIGHTS.items():
        lines.append(f"  {name:24s} {metrics[name]:8.4f} {weight:6.0%} {100 * weight * metrics[name]:8.2f}")
    lines.extend(
        [
            "",
            f"  Precisión {metrics['precision']:.4f} · Recall {metrics['recall']:.4f}",
            f"  Exactitud ponderada por importe {metrics['amount_weighted_accuracy']:.4f} (orientativa: en local no hay importe en EUR como en el servidor)",
            f"  Filas {metrics['rows']:,} · Movimientos cubiertos {metrics['txns_covered']:,} de {metrics['txns_total']:,}",
            "",
            "  Exactitud por segmento (de más a menos movimientos):",
        ]
    )
    segments = sorted(result["segments"].items(), key=lambda entry: -entry[1]["n"])
    lines.extend(f"    {name:32s} n={data['n']:>8,}  acc={data['accuracy']:.3f}" for name, data in segments)
    lines.append(f"\n  Informe: {result['report_path']}")
    logger.info("\n".join(lines))
