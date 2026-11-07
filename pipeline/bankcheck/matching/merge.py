"""Combine stage files with the baseline into one submission: each bank line takes its answer from a single source."""
from __future__ import annotations

import csv
import gzip
import logging
from collections import Counter, defaultdict
from pathlib import Path

from ..roots import Layout
from ..submission.official import SUBMISSION_HEADER
from ..submission.writer import write_submission
from .contract import read_match_rows, stage_path

BASE_FILE = "baseline.csv.gz"
BASE_SOURCE = "baseline"
MAX_NAMED = 5
logger = logging.getLogger("bankcheck")

Row = tuple[str, ...]


def _read_base(layout: Layout) -> dict[str, list[Row]]:
    path = layout.report_dir / BASE_FILE
    if not path.is_file():
        raise FileNotFoundError(f"falta {path}: ejecuta antes bankcheck baseline --phase {layout.phase}")
    lines: dict[str, list[Row]] = defaultdict(list)
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        next(reader, None)
        for row in reader:
            lines[row[0]].append(tuple(row))
    return lines


def _read_stage(layout: Layout, stage: str) -> dict[str, list[Row]]:
    lines: dict[str, list[Row]] = defaultdict(list)
    for row in read_match_rows(stage_path(layout, stage)):
        lines[row["bank_txn_id"]].append(tuple(row[column] for column in SUBMISSION_HEADER))
    return lines


def _answers_by_priority(layout: Layout, stages: list[str]) -> tuple[dict[str, list[Row]], Counter]:
    answers: dict[str, list[Row]] = {}
    lines_per_stage: Counter = Counter()
    for stage in stages:
        for txn, rows in _read_stage(layout, stage).items():
            if txn not in answers:
                answers[txn] = rows
                lines_per_stage[stage] += 1
    return answers, lines_per_stage


def merge_stages(layout: Layout, stages: list[str], out_name: str) -> Path:
    """Earlier stages win. Lines no stage answers keep the baseline, so coverage stays complete."""
    base = _read_base(layout)
    answers, lines_per_stage = _answers_by_priority(layout, stages)
    unknown = sorted(set(answers) - set(base))
    if unknown:
        raise ValueError(f"{len(unknown)} movimientos de las etapas no están en {BASE_FILE}: {unknown[:MAX_NAMED]}")
    out = layout.report_dir / out_name
    rows = (row for txn in sorted(base) for row in answers.get(txn, base[txn]))
    written = write_submission(out, rows)
    lines_per_stage[BASE_SOURCE] = len(base) - len(answers)
    logger.info("%s filas -> %s. Movimientos por fuente: %s", f"{written:,}", out, dict(lines_per_stage))
    return out
