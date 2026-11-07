"""Official score of one stage, on the part of TRAIN it can answer.

TRAIN only keeps advices on disk from 2024-07, so a whole-TRAIN score would mostly measure the missing files.
Two views, both from the unmodified scorer: the window from a date, as LIVE would see it if the stage were alone,
and only the bank lines the stage answers, which shows how right its answers are. Labels only pass through a
temporary folder; what stays behind are the scores.
"""
from __future__ import annotations

import csv
import io
import logging
import tempfile
from pathlib import Path

import duckdb

from ..db import lit
from ..roots import STATEMENTS_HOLD, Layout
from ..submission.official import EXCEPTIONS, SCORER_SHA256, SUBMISSION_HEADER, lift_limits, load_official_scorer, read_categories
from ..submission.score import GOLDEN_FILE, PHASES_WITH_GOLDEN, print_score
from ..submission.writer import write_json
from .contract import read_match_rows, stage_path

logger = logging.getLogger("bankrec")

Row = tuple[str, ...]


def _write_filtered(con: duckdb.DuckDBPyConnection, source: Path, target: Path, condition: str) -> None:
    con.execute(
        f"COPY (SELECT * FROM read_csv({lit(source)}, all_varchar=true, header=true) WHERE {condition}) "
        f"TO {lit(target)} (HEADER, COMPRESSION GZIP)"
    )


def _submission_bytes(rows: list[Row]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(SUBMISSION_HEADER)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _score_lines(layout: Layout, folder: Path, name: str, txns: list[str], rows: list[Row]) -> dict:
    """Score only `txns`: the scorer takes the phase from the files it is given, so they are filtered first."""
    con = duckdb.connect()
    try:
        con.execute("CREATE TEMP TABLE scored (bank_txn_id VARCHAR)")
        con.executemany("INSERT INTO scored VALUES (?)", [[txn] for txn in txns])
        condition = "bank_txn_id IN (SELECT bank_txn_id FROM scored)"
        bank, golden = folder / f"{name}_bank.csv.gz", folder / f"{name}_golden.csv.gz"
        _write_filtered(con, layout.phase_dir / STATEMENTS_HOLD, bank, condition)
        _write_filtered(con, layout.phase_dir / GOLDEN_FILE, golden, condition)
    finally:
        con.close()
    scorer = lift_limits(load_official_scorer(layout))
    labels = scorer.load_golden(str(golden), str(bank), list(EXCEPTIONS), read_categories(layout.masters_dir))
    wanted = set(txns)
    result = scorer.score_submission(labels, _submission_bytes([row for row in rows if row[0] in wanted]), f"{name}.csv")
    return {**result, "scorer_sha256": SCORER_SHA256}


def _window_lines(layout: Layout, since: str) -> list[str]:
    con = duckdb.connect()
    try:
        return [
            row[0]
            for row in con.execute(
                f"SELECT bank_txn_id FROM read_csv({lit(layout.phase_dir / STATEMENTS_HOLD)}, all_varchar=true, header=true) "
                f"WHERE booking_date >= {lit(since)} ORDER BY 1"
            ).fetchall()
        ]
    finally:
        con.close()


def evaluate_stage(layout: Layout, stage: str, since: str) -> dict:
    if layout.phase not in PHASES_WITH_GOLDEN:
        raise ValueError(f"la fase {layout.phase} no tiene golden: solo se evalúa en {', '.join(PHASES_WITH_GOLDEN)}")
    rows = [tuple(row[column] for column in SUBMISSION_HEADER) for row in read_match_rows(stage_path(layout, stage))]
    window = _window_lines(layout, since)
    inside = set(window)
    answered = sorted({row[0] for row in rows} & inside)
    layout.report_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=layout.report_dir, prefix=".evaluate_") as folder:
        result = {
            "stage": stage,
            "since": since,
            "window": _score_lines(layout, Path(folder), "window", window, rows),
            "linked": _score_lines(layout, Path(folder), "linked", answered, rows),
        }
    target = layout.report_dir / f"{stage}_evaluate.json"
    write_json(target, result)
    return {**result, "report_path": str(target)}


def print_evaluation(result: dict) -> None:
    logger.info("\n=== %s: ventana desde %s, como si la etapa estuviera sola ===", result["stage"], result["since"])
    print_score({**result["window"], "report_path": result["report_path"]})
    logger.info("\n=== %s: solo los movimientos que la etapa responde ===", result["stage"])
    print_score({**result["linked"], "report_path": result["report_path"]})
