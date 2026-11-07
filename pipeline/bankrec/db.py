"""Warehouse plumbing: atomic build, shared macros, bulk insert and the ingestion context."""
from __future__ import annotations

import csv
import os
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import duckdb

from .issues import IssueLog, Manifest
from .roots import Layout

MACROS = r"""
CREATE OR REPLACE MACRO nz(x) AS NULLIF(trim(x), '');
CREATE OR REPLACE MACRO pnorm(x) AS regexp_replace(upper(x), '[^A-Z0-9]', '', 'g');
CREATE OR REPLACE MACRO bank_of(account) AS NULLIF(split_part(account, '-', 2), '');
CREATE OR REPLACE MACRO month_of(d) AS strftime(d, '%Y-%m');
CREATE OR REPLACE MACRO cents_of(x) AS CAST(x * 100 AS BIGINT);
"""


@dataclass
class IngestContext:
    layout: Layout
    con: duckdb.DuckDBPyConnection
    issues: IssueLog
    manifest: Manifest


def lit(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def building_path(warehouse: Path) -> Path:
    return warehouse.with_name(f"{warehouse.stem}.building{warehouse.suffix}")


def open_build(warehouse: Path) -> duckdb.DuckDBPyConnection:
    """Start from an empty file next to the final one, so a failed run never leaves a half-built warehouse."""
    warehouse.parent.mkdir(parents=True, exist_ok=True)
    target = building_path(warehouse)
    target.unlink(missing_ok=True)
    Path(f"{target}.wal").unlink(missing_ok=True)
    con = duckdb.connect(str(target))
    con.execute(MACROS)
    return con


def failed_path(warehouse: Path) -> Path:
    return warehouse.with_name(f"{warehouse.stem}.failed{warehouse.suffix}")


def publish_build(warehouse: Path) -> Path:
    os.replace(building_path(warehouse), warehouse)
    failed_path(warehouse).unlink(missing_ok=True)
    return warehouse


def keep_failed_build(warehouse: Path) -> Path:
    """A run with errors must not replace a good warehouse, but its result is still needed to diagnose them."""
    target = failed_path(warehouse)
    os.replace(building_path(warehouse), target)
    return target


def table_columns(con: duckdb.DuckDBPyConnection, table: str) -> list[tuple[str, str]]:
    return [(row[0], row[1]) for row in con.execute(f"DESCRIBE {table}").fetchall()]


def bulk_insert(con: duckdb.DuckDBPyConnection, table: str, rows: Iterable[Sequence[object]]) -> int:
    """Load Python rows through a temporary CSV: much faster than row-by-row inserts."""
    columns = table_columns(con, table)
    written = 0
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        for row in rows:
            writer.writerow(["" if value is None else value for value in row])
            written += 1
    try:
        if written:
            spec = ", ".join(f"{lit(name)}: {lit(kind)}" for name, kind in columns)
            con.execute(
                f"INSERT INTO {table} SELECT * FROM read_csv({lit(handle.name)}, header=false, auto_detect=false, "
                f"delim=',', quote='\"', escape='\"', columns={{{spec}}})"
            )
    finally:
        os.unlink(handle.name)
    return written
