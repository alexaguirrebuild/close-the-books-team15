"""Link raw lines to bank_txn_id, unify overlaps and cross-check totals per account and month.

The normalised view is the reference: it owns the ids. Raw lines join it as a multiset on
(account, date, amount, references, text), because identical lines can legitimately repeat,
while overlapping exports repeat the same line across files and must not count twice.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..db import IngestContext

SOURCE = "bank_statements"
MAX_SAMPLES = 5

# Banks print text in different case between the raw file and the normalised view: compare letters and digits only.
_KEY = """md5(concat_ws('|', account_id, booking_date, amount_cents, coalesce(ref1, ''), coalesce(ref2, ''), pnorm(coalesce(text, ''))))"""

LINK_SQL = f"""
CREATE OR REPLACE TABLE bank_statement_line AS
WITH keyed AS (
    SELECT *, {_KEY} AS match_key FROM _statement_line
), ordinal AS (
    SELECT *, row_number() OVER (PARTITION BY src_file, match_key ORDER BY src_line) AS key_ordinal FROM keyed
), flagged AS (
    SELECT *, row_number() OVER (PARTITION BY account_id, match_key, key_ordinal ORDER BY src_file) > 1 AS is_overlap_dup FROM ordinal
), normalised AS (
    SELECT bank_txn_id, account_id, {_KEY} AS match_key,
           row_number() OVER (PARTITION BY account_id, {_KEY} ORDER BY bank_txn_id) AS key_ordinal
    FROM bank_txn
)
SELECT f.* EXCLUDE (file_month), f.file_month AS month, bank_of(f.account_id) AS bank, n.bank_txn_id,
       CAST(f.amount_cents AS DECIMAL(18,2)) / 100 AS amount
FROM flagged f LEFT JOIN normalised n USING (account_id, match_key, key_ordinal)
"""

UPDATE_BANK_TXN_SQL = """
CREATE OR REPLACE TABLE bank_txn AS
WITH linked AS (
    SELECT bank_txn_id, list(DISTINCT src_file ORDER BY src_file) AS files
    FROM bank_statement_line WHERE bank_txn_id IS NOT NULL GROUP BY 1
), available AS (SELECT DISTINCT file_name FROM statement_file)
SELECT t.* EXCLUDE (raw_status, raw_files),
       CASE WHEN l.files IS NOT NULL THEN 'matched'
            WHEN a.file_name IS NOT NULL THEN 'missing_in_raw'
            ELSE 'no_raw_file' END AS raw_status,
       l.files AS raw_files
FROM bank_txn t
LEFT JOIN linked l USING (bank_txn_id)
LEFT JOIN available a ON a.file_name = regexp_extract(t.statement_file, '[^/]+$')
"""

XVAL_SQL = """
CREATE OR REPLACE TABLE xval_account_month AS
WITH raw AS (
    SELECT account_id, month_of(booking_date) AS month, count(*) AS raw_lines, sum(amount_cents) AS raw_cents
    FROM bank_statement_line WHERE NOT is_overlap_dup GROUP BY 1, 2
), norm AS (
    SELECT account_id, month, count(*) AS norm_lines, sum(amount_cents) AS norm_cents FROM bank_txn GROUP BY 1, 2
), has_file AS (SELECT DISTINCT account_id, month FROM statement_file)
SELECT account_id, month, coalesce(raw_lines, 0) AS raw_lines, coalesce(norm_lines, 0) AS norm_lines,
       coalesce(raw_cents, 0) AS raw_cents, coalesce(norm_cents, 0) AS norm_cents,
       CASE WHEN f.month IS NULL AND coalesce(norm_lines, 0) = 0 THEN 'OUTSIDE_PHASE'
            WHEN f.month IS NULL THEN 'NO_RAW'
            WHEN coalesce(raw_lines, 0) = coalesce(norm_lines, 0) AND coalesce(raw_cents, 0) = coalesce(norm_cents, 0) THEN 'OK'
            ELSE 'DIFF' END AS status
FROM raw FULL OUTER JOIN norm USING (account_id, month)
LEFT JOIN has_file f USING (account_id, month)
"""


@dataclass(frozen=True)
class Reconciliation:
    raw_lines: int
    overlap_duplicates: int
    outside_view: int
    raw_only: int
    missing_in_raw: int


def _report_unlinked(ctx: IngestContext) -> tuple[int, int]:
    """A raw line without id is an error unless it belongs to another period's view, as overlapping exports do."""
    outside, inside = ctx.con.execute(
        """SELECT count(*) FILTER (WHERE outside), count(*) FILTER (WHERE NOT outside)
           FROM (SELECT l.booking_date < (SELECT min(booking_date) FROM bank_txn)
                        OR l.booking_date > (SELECT max(booking_date) FROM bank_txn) AS outside
                 FROM bank_statement_line l WHERE l.bank_txn_id IS NULL)"""
    ).fetchone()
    if outside:
        ctx.issues.info("RAW_LINE_OUTSIDE_PHASE", SOURCE, "líneas de otro periodo repetidas por el solape de la exportación", count=outside)
    if inside:
        samples = ctx.con.execute(
            f"""SELECT src_file, src_line, booking_date, amount_cents FROM bank_statement_line
                WHERE bank_txn_id IS NULL AND booking_date BETWEEN (SELECT min(booking_date) FROM bank_txn) AND (SELECT max(booking_date) FROM bank_txn)
                ORDER BY src_file, src_line LIMIT {MAX_SAMPLES}"""
        ).fetchall()
        ctx.issues.error("RAW_ONLY_LINE", SOURCE, f"líneas del extracto sin movimiento en la vista normalizada; ejemplos: {samples}", count=inside)
    return outside, inside


def _report_missing(ctx: IngestContext) -> int:
    missing = ctx.con.execute("SELECT count(*) FROM bank_txn WHERE raw_status = 'missing_in_raw'").fetchone()[0]
    if missing:
        samples = ctx.con.execute(
            f"SELECT bank_txn_id, statement_file FROM bank_txn WHERE raw_status = 'missing_in_raw' ORDER BY 1 LIMIT {MAX_SAMPLES}"
        ).fetchall()
        ctx.issues.error("MISSING_IN_RAW", "bank_transactions", f"movimientos cuyo extracto existe pero no los contiene; ejemplos: {samples}", count=missing)
    without_file = ctx.con.execute("SELECT count(*) FROM bank_txn WHERE raw_status = 'no_raw_file'").fetchone()[0]
    if without_file:
        report = ctx.issues.warn if ctx.layout.phase in ("train", "sample") else ctx.issues.error
        report("NO_RAW_STATEMENT", "bank_transactions", "movimientos cuyo extracto no está en disco (esperado en TRAIN lite)", count=without_file)
    return missing


def _report_xval(ctx: IngestContext) -> None:
    rows = ctx.con.execute(
        f"""SELECT account_id, month, raw_lines, norm_lines, raw_cents, norm_cents
            FROM xval_account_month WHERE status = 'DIFF' ORDER BY 1, 2"""
    ).fetchall()
    for account, month, raw_lines, norm_lines, raw_cents, norm_cents in rows:
        ctx.issues.error(
            "XVAL_DIFF", SOURCE, f"{month}: crudo {raw_lines} líneas / {raw_cents} cts; normalizado {norm_lines} / {norm_cents}", segment=account
        )


def reconcile(ctx: IngestContext) -> Reconciliation:
    ctx.con.execute(LINK_SQL)
    ctx.con.execute(UPDATE_BANK_TXN_SQL)
    ctx.con.execute(XVAL_SQL)
    ctx.con.execute("DROP TABLE _statement_line")
    raw_lines, overlaps = ctx.con.execute("SELECT count(*), count(*) FILTER (WHERE is_overlap_dup) FROM bank_statement_line").fetchone()
    if overlaps:
        ctx.issues.info("RAW_OVERLAP", SOURCE, "líneas repetidas entre ficheros solapados, marcadas y unificadas", count=overlaps)
    outside, inside = _report_unlinked(ctx)
    missing = _report_missing(ctx)
    _report_xval(ctx)
    return Reconciliation(raw_lines, overlaps, outside, inside, missing)
