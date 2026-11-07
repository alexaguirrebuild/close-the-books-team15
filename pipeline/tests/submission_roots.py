"""A small data root with several phases: real scorer, real CSV headers and a warehouse per phase."""
from __future__ import annotations

import csv
import gzip
import shutil
from collections.abc import Sequence
from pathlib import Path

import duckdb

from bankrec.db import MACROS
from bankrec.roots import Layout, resolve_layout
from bankrec.tables import AP_INVOICES, AR_INVOICES, BANK_TRANSACTIONS, CREDIT_NOTES, OPEN_ITEMS

PHASES_WITH_OPENING = ("live", "final")
REAL_SCORER = Path(__file__).resolve().parents[2] / "bankrec-docs-and-masters" / "starter" / "bankrec_score.py"
HEADER = "bank_txn_id,item_id,amount,category,exception"
GOLDEN_HEADER = f"{HEADER},segment,difficulty"
ITEM_FILES = {"ARI": ("ar_invoices.csv.gz", AR_INVOICES), "API": ("ap_invoices.csv.gz", AP_INVOICES)}
CREDIT_FILE = ("credit_notes.csv.gz", CREDIT_NOTES)

Txn = tuple[str, int, str]  # id, cents (signed), bank text
Item = tuple[str, int, str]  # id, open cents, printed number


def _write_gz_csv(path: Path, columns: Sequence[str], ids: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(columns)
        for identifier in ids:
            writer.writerow([identifier, *[""] * (len(columns) - 1)])


def _write_raw_files(phase_dir: Path, txns: Sequence[Txn], items: Sequence[Item], opening: Sequence[str]) -> None:
    _write_gz_csv(phase_dir / "bank_transactions.csv.gz", BANK_TRANSACTIONS.columns, [txn[0] for txn in txns])
    for prefix, (name, spec) in ITEM_FILES.items():
        _write_gz_csv(phase_dir / name, spec.columns, [item[0] for item in items if item[0].startswith(prefix)])
    credit_ids = [item[0] for item in items if item[0][:3] in ("ARC", "APC")]
    _write_gz_csv(phase_dir / CREDIT_FILE[0], CREDIT_FILE[1].columns, credit_ids)
    if phase_dir.name in PHASES_WITH_OPENING:
        _write_gz_csv(phase_dir / "open_items_at_start.csv.gz", OPEN_ITEMS.columns, opening)


def _write_warehouse(path: Path, txns: Sequence[Txn], items: Sequence[Item], ingest_errors: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute(MACROS)
    con.execute(
        """CREATE TABLE bank_txn (bank_txn_id VARCHAR, amount_cents BIGINT, currency VARCHAR, booking_date DATE, text VARCHAR,
           ref1 VARCHAR, ref2 VARCHAR, remittance_info VARCHAR, entity VARCHAR);
           CREATE TABLE item (item_id VARCHAR, side VARCHAR, printed_norm VARCHAR, open_cents BIGINT, currency VARCHAR,
           entity VARCHAR, issue_date DATE);
           CREATE TABLE ingest_run (phase VARCHAR, errors BIGINT, warnings BIGINT);"""
    )
    con.execute("INSERT INTO ingest_run VALUES ('fixture', ?, 0)", [ingest_errors])
    for txn, cents, text in txns:
        con.execute("INSERT INTO bank_txn VALUES (?, ?, 'EUR', DATE '2025-07-01', ?, NULL, NULL, NULL, 'ES01')", [txn, cents, text])
    for item, cents, printed in items:
        side = "AR" if item.startswith("AR") else "AP"
        con.execute("INSERT INTO item VALUES (?, ?, pnorm(?), ?, 'EUR', 'ES01', DATE '2025-06-01')", [item, side, printed, cents])
    con.close()


def build_phase(
    root: Path,
    phase: str,
    txns: Sequence[Txn],
    items: Sequence[Item],
    opening: Sequence[str] = (),
    ingest_errors: int = 0,
) -> Layout:
    """Adds one phase to the root and returns its layout, resolved exactly as the command line does."""
    masters = root / "masters"
    masters.mkdir(parents=True, exist_ok=True)
    (masters / "categories.csv").write_text("category,description,gl_account\nBANK_FEES,,\nUNIDENTIFIED,,\nPAYROLL,,\n", encoding="utf-8")
    (root / "starter").mkdir(exist_ok=True)
    shutil.copyfile(REAL_SCORER, root / "starter" / REAL_SCORER.name)
    _write_raw_files(root / phase, txns, items, opening)
    _write_warehouse(root / "cache" / "warehouse" / f"{phase}.duckdb", txns, items, ingest_errors)
    return resolve_layout(phase, data_root=root)


def run_on_warehouse(layout: Layout, statement: str) -> None:
    """Makes the warehouse disagree with the raw files, as a rejected row or a stale build would."""
    con = duckdb.connect(str(layout.warehouse_path))
    con.execute(statement)
    con.close()


def write_golden(layout: Layout, rows: Sequence[str]) -> None:
    with gzip.open(layout.phase_dir / "golden_allocations.csv.gz", "wt", encoding="utf-8", newline="") as handle:
        handle.write("\n".join([GOLDEN_HEADER, *rows]) + "\n")


def write_submission_text(folder: Path, rows: Sequence[str], name: str = "sub.csv") -> Path:
    path = folder / name
    path.write_text("\n".join([HEADER, *rows]) + "\n", encoding="utf-8")
    return path


def over_60_mb(rows: Sequence[str]) -> str:
    """Valid rows plus blank-looking lines the scorer skips: 70 MB, above its limit of 60 * 1024 * 1024 bytes."""
    blank_line = " " * 100_000
    return "\n".join([HEADER, *rows, *[blank_line] * 700]) + "\n"
