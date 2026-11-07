"""What every matching stage writes, so the merge can combine them, and how stages open the warehouse."""
from __future__ import annotations

import csv
import gzip
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path

import duckdb

from ..db import lit
from ..roots import Layout
from ..submission.writer import _replacing, _text_stream

MATCH_COLUMNS = ("bank_txn_id", "item_id", "amount", "category", "exception", "source", "confidence", "advice_id", "line_no")
MATCHES_DIRNAME = "matches"
THREADS = 4
MEMORY_LIMIT = "6GB"


def stage_path(layout: Layout, stage: str) -> Path:
    return layout.report_dir / MATCHES_DIRNAME / f"{stage}.csv.gz"


def write_match_rows(path: Path, rows: Iterable[Sequence[str]]) -> int:
    """Atomic and reproducible: the same rows always give the same bytes. Returns the rows written."""
    count = 0
    with _replacing(path) as raw, _text_stream(raw, compress=True) as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(MATCH_COLUMNS)
        for row in rows:
            writer.writerow(row)
            count += 1
    return count


def read_match_rows(path: Path) -> Iterator[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(f"falta el fichero de la etapa: {path}")
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != MATCH_COLUMNS:
            raise ValueError(f"{path}: columnas {reader.fieldnames}; se esperan {list(MATCH_COLUMNS)}")
        yield from reader


def open_warehouse(layout: Layout) -> duckdb.DuckDBPyConnection:
    """Read-only, so ingestion can republish at any moment. Stages only create temporary tables."""
    if not layout.warehouse_path.is_file():
        raise FileNotFoundError(f"falta el almacén {layout.warehouse_path}: ejecuta antes bankrec ingest --phase {layout.phase}")
    con = duckdb.connect()
    scratch = layout.report_dir / MATCHES_DIRNAME / ".duckdb_tmp"
    con.execute(f"SET threads={THREADS}; SET memory_limit='{MEMORY_LIMIT}'; SET temp_directory={lit(scratch)}")
    con.execute(f"ATTACH {lit(layout.warehouse_path)} AS warehouse (READ_ONLY); USE warehouse")
    return con
