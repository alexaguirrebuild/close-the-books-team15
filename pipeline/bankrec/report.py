"""Control tables, content fingerprints and the human and JSON reports of an ingestion."""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .db import IngestContext, bulk_insert
from .issues import IssueLog

logger = logging.getLogger("bankrec")

REPORT_NAME = "ingest_report.json"

# Not fingerprinted: run times, and a cache that grows with use.
UNSTABLE_TABLES = {"ingest_run", "table_fingerprint", "pdf_text_cache"}

CREATE_CONTROL_TABLES = """
CREATE OR REPLACE TABLE file_manifest (path VARCHAR, source_type VARCHAR, segment VARCHAR, size_bytes BIGINT, rows BIGINT, status VARCHAR, error VARCHAR);
CREATE OR REPLACE TABLE ingest_issue (severity VARCHAR, code VARCHAR, source_type VARCHAR, segment VARCHAR, file VARCHAR, line BIGINT, count BIGINT, detail VARCHAR);
CREATE OR REPLACE TABLE source_summary (source_type VARCHAR, segment VARCHAR, files BIGINT, rows BIGINT);
CREATE OR REPLACE TABLE table_fingerprint (table_name VARCHAR, row_count BIGINT, content_hash VARCHAR);
CREATE OR REPLACE TABLE ingest_run (phase VARCHAR, data_root VARCHAR, version VARCHAR, started_at TIMESTAMP, finished_at TIMESTAMP, errors BIGINT, warnings BIGINT);
"""

# (source_type, table, segment expression): the row counts behind the summary.
SUMMARY_QUERIES = (
    ("bank_transactions", "bank_txn", "statement_format || '/' || bank"),
    ("bank_statements", "bank_statement_line", "format || '/' || bank"),
    ("items", "item", "item_type"),
    ("payment_runs", "payment_run_line", "run_type"),
    ("direct_debit_batches", "dd_batch_line", "scheme"),
    ("card_settlements", "card_settlement_line", "acquirer"),
    ("cheque_deposits", "cheque_deposit_line", "entity"),
    ("remittance_lines", "remittance_line", "split_part(src_file, '.', -1)"),
    ("pdf_index", "pdf_index", "coalesce(document_type, 'sin_indice')"),
)


def write_control_tables(ctx: IngestContext) -> None:
    con = ctx.con
    con.execute(CREATE_CONTROL_TABLES)
    bulk_insert(
        con,
        "file_manifest",
        ([record.path, record.source_type, record.segment, record.size_bytes, record.rows, record.status, record.error] for record in ctx.manifest.files),
    )
    bulk_insert(
        con,
        "ingest_issue",
        ([issue.severity, issue.code, issue.source_type, issue.segment, issue.file, issue.line, issue.count, issue.detail] for issue in ctx.issues.items),
    )
    files = {row[0]: row[1] for row in con.execute("SELECT source_type, count(*) FROM file_manifest GROUP BY 1").fetchall()}
    for source, table, segment in SUMMARY_QUERIES:
        con.execute(
            f"INSERT INTO source_summary SELECT ?, coalesce({segment}, '-'), ?, count(*) FROM {table} GROUP BY 2",
            [source, files.get(source, 0)],
        )


def write_fingerprints(ctx: IngestContext) -> dict[str, tuple[int, str]]:
    """Order-independent hash per table: two ingestions of the same files must give the same values."""
    tables = [row[0] for row in ctx.con.execute("SELECT table_name FROM duckdb_tables() WHERE NOT temporary ORDER BY 1").fetchall()]
    found: dict[str, tuple[int, str]] = {}
    for table in tables:
        if table in UNSTABLE_TABLES:
            continue
        count, value = ctx.con.execute(f"SELECT count(*), coalesce(sum(hash(t))::VARCHAR, '0') FROM {table} t").fetchone()
        found[table] = (count, value)
        ctx.con.execute("INSERT INTO table_fingerprint VALUES (?, ?, ?)", [table, count, value])
    return found


def write_run(ctx: IngestContext, started_at: datetime) -> None:
    totals = ctx.issues.totals()
    ctx.con.execute(
        "INSERT INTO ingest_run VALUES (?, ?, ?, ?, ?, ?, ?)",
        [ctx.layout.phase, str(ctx.layout.root), __version__, started_at, datetime.now(timezone.utc), totals["ERROR"], totals["WARN"]],
    )


def _issue_summary(issues: IssueLog) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str, str], list[int]] = defaultdict(lambda: [0, 0])
    for issue in issues.items:
        entry = grouped[(issue.severity, issue.code, issue.source_type)]
        entry[0] += 1
        entry[1] += issue.count
    order = {"ERROR": 0, "WARN": 1, "INFO": 2}
    return [
        {"severity": severity, "code": code, "source_type": source, "issues": occurrences, "records": records}
        for (severity, code, source), (occurrences, records) in sorted(grouped.items(), key=lambda item: (order[item[0][0]], item[0][1], item[0][2]))
    ]


def build_report(ctx: IngestContext, fingerprints: dict[str, tuple[int, str]], seconds: float) -> dict[str, object]:
    con = ctx.con
    summary = con.execute("SELECT source_type, segment, files, rows FROM source_summary ORDER BY 1, 2").fetchall()
    xval = dict(con.execute("SELECT status, count(*) FROM xval_account_month GROUP BY 1").fetchall())
    raw_status = dict(con.execute("SELECT raw_status, count(*) FROM bank_txn GROUP BY 1").fetchall())
    advice = dict(con.execute("SELECT control_status, count(*) FROM remittance_advice GROUP BY 1").fetchall())
    files = dict(con.execute("SELECT status, count(*) FROM file_manifest GROUP BY 1").fetchall())
    return {
        "phase": ctx.layout.phase,
        "data_root": str(ctx.layout.root),
        "warehouse": str(ctx.layout.warehouse_path),
        "seconds": round(seconds, 1),
        "issue_totals": ctx.issues.totals(),
        "issues": _issue_summary(ctx.issues),
        "tables": {table: count for table, (count, _) in fingerprints.items()},
        "fingerprint": {table: value for table, (_, value) in fingerprints.items()},
        "xval_account_month": xval,
        "bank_txn_raw_status": raw_status,
        "remittance_control": advice,
        "files_by_status": files,
        "sources": [{"source_type": s, "segment": g, "files": f, "rows": r} for s, g, f, r in summary],
    }


def discard_report(folder: Path) -> None:
    """A run that stops halfway must not leave the previous report looking like its result."""
    (folder / REPORT_NAME).unlink(missing_ok=True)


def write_report(report: dict[str, object], folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / REPORT_NAME
    path.write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    return path


def print_summary(report: dict[str, object]) -> None:
    lines = [f"\nFase {report['phase']}  ·  {report['seconds']} s  ·  {report['warehouse']}", "", "Filas por tabla:"]
    tables: dict[str, int] = report["tables"]  # type: ignore[assignment]
    lines.extend(f"  {table:28s} {count:>10,}" for table, count in tables.items())
    lines.extend(["", f"Validación cruzada cuenta/mes: {report['xval_account_month']}", f"Movimientos frente a crudo:    {report['bank_txn_raw_status']}", f"Control de avisos:             {report['remittance_control']}", f"Ficheros por estado:           {report['files_by_status']}", "", "Incidencias:"])
    issues: list[dict[str, object]] = report["issues"]  # type: ignore[assignment]
    lines.extend(f"  {item['severity']:5s} {item['code']:30s} {item['source_type']:22s} registros={item['records']:>9,}" for item in issues)
    totals = report["issue_totals"]
    lines.append(f"\nTotales: {totals}")
    logger.info("\n".join(lines))
