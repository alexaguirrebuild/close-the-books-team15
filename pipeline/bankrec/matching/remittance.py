"""M2: turn remittance advices into allocation rows. Reads the warehouse, writes under out/<phase>/matches/."""
from __future__ import annotations

import logging
from collections.abc import Iterator

import duckdb

from ..roots import Layout
from ..submission.writer import write_json
from .contract import open_warehouse, stage_path, write_match_rows
from .remittance_items import resolve_items
from .remittance_link import link_advices
from .remittance_rules import SOURCE, classify_rows

NO_EXCEPTION = "(vacía)"
MAX_SAMPLES = 10
FETCH_SIZE = 50_000
logger = logging.getLogger("bankrec")

ROWS_SQL = """
SELECT bank_txn_id, item_id, amount, category, exception, source, confidence, advice_id, line_no
FROM advice_row
WHERE amount > 0
ORDER BY bank_txn_id, item_id
"""


def _text(value: object) -> str:
    return "" if value is None else str(value)


def _output_rows(con: duckdb.DuckDBPyConnection) -> Iterator[tuple[str, ...]]:
    cursor = con.execute(ROWS_SQL)
    while batch := cursor.fetchmany(FETCH_SIZE):
        for txn, item, amount, category, exception, source, confidence, advice, line in batch:
            yield txn, item, f"{amount:.2f}", _text(category), _text(exception), source, str(confidence), advice, str(line)


def _count(con: duckdb.DuckDBPyConnection, sql: str) -> int:
    return con.execute(sql).fetchone()[0]


def _counts_by(con: duckdb.DuckDBPyConnection, sql: str) -> dict[str, int]:
    return {str(key): count for key, count in con.execute(sql).fetchall()}


def _advice_report(con: duckdb.DuckDBPyConnection) -> dict:
    first, last = con.execute("SELECT min(booking_date), max(booking_date) FROM bank_txn").fetchone()
    before, after = con.execute(
        """SELECT count(*) FILTER (WHERE payment_date < ?), count(*) FILTER (WHERE payment_date > ?)
           FROM remittance_advice
           WHERE on_disk AND n_lines > 0 AND advice_id NOT IN (SELECT advice_id FROM advice_link)""",
        [first, last],
    ).fetchone()
    unlinked = _count(con, "SELECT count(*) FROM remittance_advice WHERE on_disk AND n_lines > 0 AND advice_id NOT IN (SELECT advice_id FROM advice_link)")
    return {
        "total": _count(con, "SELECT count(*) FROM remittance_advice"),
        "on_disk": _count(con, "SELECT count(*) FROM remittance_advice WHERE on_disk"),
        "linked": _counts_by(con, "SELECT how, count(*) FROM advice_link GROUP BY how ORDER BY how"),
        "unlinked": {"before_phase": before, "in_phase": unlinked - before - after, "after_phase": after},
    }


def _line_report(con: duckdb.DuckDBPyConnection) -> dict:
    samples = con.execute(f"SELECT advice_id, line_no, document FROM advice_unresolved ORDER BY 1, 2 LIMIT {MAX_SAMPLES}").fetchall()
    return {
        "in_linked_advices": _count(con, "SELECT count(*) FROM advice_line"),
        "resolved": _counts_by(con, "SELECT item_how, count(*) FROM advice_item GROUP BY item_how ORDER BY item_how"),
        "unresolved": _count(con, "SELECT count(*) FROM advice_unresolved"),
        "unresolved_samples": [list(sample) for sample in samples],
    }


def _row_report(con: duckdb.DuckDBPyConnection, written: int) -> dict:
    return {
        "total": written,
        "bank_lines": _count(con, "SELECT count(DISTINCT bank_txn_id) FROM advice_row WHERE amount > 0"),
        "by_exception": _counts_by(
            con, f"SELECT coalesce(exception, '{NO_EXCEPTION}'), count(*) FROM advice_row WHERE amount > 0 GROUP BY 1 ORDER BY 2 DESC, 1"
        ),
        "doubtful": _count(con, "SELECT count(*) FROM advice_row WHERE amount > 0 AND confidence < 1"),
        "zero_amount_dropped": _count(con, "SELECT count(*) FROM advice_row WHERE amount IS NULL OR amount <= 0"),
        "no_paid_dropped": _count(con, "SELECT count(*) FROM advice_item WHERE paid IS NULL"),
        "repeated_item_dropped": _count(con, "SELECT (SELECT count(*) FROM advice_item WHERE paid IS NOT NULL) - (SELECT count(*) FROM advice_row)"),
        "unknown_reasons": _counts_by(con, "SELECT reason, n FROM advice_unknown_reason"),
    }


def _log_summary(report: dict) -> None:
    advices, lines, rows = report["advices"], report["lines"], report["rows"]
    logger.info(
        "M2 %s: %s filas en %s movimientos. Avisos enlazados %s; sin enlazar %s. Líneas sin partida: %s. Excepciones: %s",
        report["phase"], f"{rows['total']:,}", f"{rows['bank_lines']:,}", advices["linked"], advices["unlinked"], f"{lines['unresolved']:,}", rows["by_exception"],
    )
    if rows["unknown_reasons"]:
        logger.warning("Motivos de aviso desconocidos, tratados como disputa: %s", rows["unknown_reasons"])
    if advices["unlinked"]["in_phase"]:
        logger.warning("%s avisos con pago dentro de la fase quedaron sin movimiento", advices["unlinked"]["in_phase"])


def run_remittance(layout: Layout) -> dict:
    """Writes the stage rows and their report. Returns the report."""
    con = open_warehouse(layout)
    try:
        link_advices(con)
        resolve_items(con)
        classify_rows(con)
        path = stage_path(layout, SOURCE)
        written = write_match_rows(path, _output_rows(con))
        report = {"phase": layout.phase, "file": layout.relative(path), "advices": _advice_report(con), "lines": _line_report(con), "rows": _row_report(con, written)}
    finally:
        con.close()
    write_json(path.with_name(f"{SOURCE}_report.json"), report)
    _log_summary(report)
    return report
