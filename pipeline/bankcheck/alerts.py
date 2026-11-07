"""Cross-table checks: ids nobody knows, dates outside the phase and currencies without a rate."""
from __future__ import annotations

from pathlib import Path

from .db import IngestContext
from .roots import is_excluded, iter_phase_files

MAX_SAMPLES = 5
INDEXED_ONLY_FOLDER = "pdfs"  # These PDFs are indexed on purpose and read on demand.
PHASE_WINDOWS = {
    "sample": ("2025-06-01", "2025-06-30"),
    "train": ("2021-07-01", "2025-06-30"),
    "live": ("2025-07-01", "2025-12-31"),
    "final": ("2026-01-01", "2026-06-30"),
}
BASE_CURRENCY = "EUR"
FX_MARGIN_DAYS = 5  # ECB rates only exist on TARGET business days, so the last one can fall a few days before the phase ends.

# (source_type, table, column, master table, master column, code, optional row filter)
UNKNOWN_ID_CHECKS = (
    ("bank_transactions", "bank_txn", "account_id", "bank_account", "account_id", "UNKNOWN_ACCOUNT", None),
    ("bank_transactions", "bank_txn", "entity", "entity", "entity", "UNKNOWN_ENTITY", None),
    ("bank_statements", "statement_file", "account_id", "bank_account", "account_id", "UNKNOWN_ACCOUNT", None),
    ("ar_invoices", "ar_invoice", "customer_id", "customer", "customer_id", "UNKNOWN_CUSTOMER", None),
    ("ar_invoices", "ar_invoice", "payment_terms", "payment_term", "code", "UNKNOWN_TERMS", None),
    ("ap_invoices", "ap_invoice", "supplier_id", "supplier", "supplier_id", "UNKNOWN_SUPPLIER", None),
    ("ap_invoices", "ap_invoice", "payment_terms", "payment_term", "code", "UNKNOWN_TERMS", None),
    ("credit_notes", "credit_note", "party_id", "customer", "customer_id", "UNKNOWN_CUSTOMER", "side = 'AR'"),
    ("credit_notes", "credit_note", "party_id", "supplier", "supplier_id", "UNKNOWN_SUPPLIER", "side = 'AP'"),
    ("open_items_at_start", "open_item_at_start", "party_id", "customer", "customer_id", "UNKNOWN_CUSTOMER", "item_type LIKE 'AR%'"),
    ("open_items_at_start", "open_item_at_start", "party_id", "supplier", "supplier_id", "UNKNOWN_SUPPLIER", "item_type LIKE 'AP%'"),
    ("items", "item", "entity", "entity", "entity", "UNKNOWN_ENTITY", None),
    ("payment_runs", "payment_run_line", "debtor_account_id", "bank_account", "account_id", "UNKNOWN_ACCOUNT", None),
    ("direct_debit_batches", "dd_batch_line", "account_id", "bank_account", "account_id", "UNKNOWN_ACCOUNT", None),
    ("card_settlements", "card_settlement_line", "account_id", "bank_account", "account_id", "UNKNOWN_ACCOUNT", None),
    ("cheque_deposits", "cheque_deposit_line", "account_id", "bank_account", "account_id", "UNKNOWN_ACCOUNT", None),
)

# (source_type, table, date column)
WINDOW_CHECKS = (
    ("bank_transactions", "bank_txn", "booking_date"),
    ("payment_runs", "payment_run_line", "execution_date"),
    ("direct_debit_batches", "dd_batch_line", "collection_date"),
    ("card_settlements", "card_settlement_line", "settlement_date"),
    ("cheque_deposits", "cheque_deposit_line", "deposit_date"),
)


def _check_unknown_ids(ctx: IngestContext) -> None:
    for source, table, column, master, master_column, code, condition in UNKNOWN_ID_CHECKS:
        where = f"AND {condition}" if condition else ""
        rows, distinct, sample = ctx.con.execute(
            f"""SELECT count(*), count(DISTINCT t.{column}), (list(DISTINCT t.{column}))[:{MAX_SAMPLES}]
                FROM {table} t
                WHERE t.{column} IS NOT NULL {where}
                  AND NOT EXISTS (SELECT 1 FROM {master} m WHERE m.{master_column} = t.{column})"""
        ).fetchone()
        if rows:
            ctx.issues.warn(code, source, f"{table}.{column}: {distinct} valores sin {master}; ejemplos: {sorted(sample)}", count=rows)


def _check_phase_window(ctx: IngestContext) -> None:
    start, end = PHASE_WINDOWS[ctx.layout.phase]
    bank_rows = ctx.con.execute(
        f"SELECT count(*) FROM bank_txn WHERE booking_date NOT BETWEEN DATE '{start}' AND DATE '{end}'"
    ).fetchone()[0]
    if bank_rows:
        ctx.issues.error("BANK_LINE_OUTSIDE_PHASE", "bank_transactions", f"movimientos fuera de {start}..{end}: posible mezcla de fases", count=bank_rows)
    for source, table, column in WINDOW_CHECKS[1:]:
        rows = ctx.con.execute(f"SELECT count(*) FROM {table} WHERE {column} NOT BETWEEN DATE '{start}' AND DATE '{end}'").fetchone()[0]
        if rows:
            ctx.issues.warn("AUX_DATE_OUTSIDE_PHASE", source, f"{table}.{column} fuera de {start}..{end}", count=rows)


def _check_currencies(ctx: IngestContext) -> None:
    rows = ctx.con.execute(
        f"""SELECT currency FROM (SELECT currency FROM bank_txn UNION SELECT currency FROM item)
            WHERE currency IS NOT NULL AND currency <> '{BASE_CURRENCY}'
              AND currency NOT IN (SELECT currency FROM fx_rate WHERE currency IS NOT NULL) ORDER BY 1"""
    ).fetchall()
    if rows:
        ctx.issues.warn("CURRENCY_WITHOUT_RATE", "masters", f"divisas sin tipo en fx_rates: {[row[0] for row in rows]}", count=len(rows))


def _check_fx_coverage(ctx: IngestContext) -> None:
    _, end = PHASE_WINDOWS[ctx.layout.phase]
    rows = ctx.con.execute(
        f"""SELECT currency, max(date) FROM fx_rate
            WHERE currency IN (SELECT currency FROM bank_txn UNION SELECT currency FROM item)
            GROUP BY 1 HAVING max(date) < DATE '{end}' - INTERVAL {FX_MARGIN_DAYS} DAY ORDER BY 1"""
    ).fetchall()
    if rows:
        detail = ", ".join(f"{currency} hasta {last}" for currency, last in rows)
        ctx.issues.warn("FX_RATES_STALE", "masters", f"tipos de cambio que no llegan al final de la fase ({end}): {detail}", count=len(rows))


def _report_printed_coverage(ctx: IngestContext) -> None:
    """How far remittance documents reach items by plain printed number: a hint for matching, not an error."""
    total, found = ctx.con.execute(
        """SELECT count(*), count(*) FILTER (WHERE EXISTS (SELECT 1 FROM item i WHERE i.printed_norm = l.document_norm))
           FROM remittance_line l"""
    ).fetchone()
    if total:
        ctx.issues.info("REMITTANCE_PRINTED_COVERAGE", "remittance_advices", f"{found} de {total} líneas de aviso citan un número impreso exacto de una partida de la fase")


def _is_indexed_pdf(path: Path, phase_dir: Path) -> bool:
    return path.suffix.lower() == ".pdf" and path.relative_to(phase_dir).parts[0] == INDEXED_ONLY_FOLDER


def check_every_file_was_read(ctx: IngestContext) -> None:
    """A file no loader touched would otherwise vanish in silence: a .csv.gz among .csv files, a misplaced statement."""
    phase_dir = ctx.layout.phase_dir
    read = {record.path for record in ctx.manifest.files}
    for path in iter_phase_files(phase_dir):
        if ctx.layout.relative(path) not in read and not _is_indexed_pdf(path, phase_dir):
            ctx.issues.error("FILE_NOT_READ", "phase_folder", "ningún cargador ha leído este fichero", file=ctx.layout.relative(path))
    for path in phase_dir.iterdir():
        if is_excluded(path):
            ctx.issues.info("GOLDEN_EXCLUDED", "phase_folder", "excluido a propósito: solo sirve para evaluar", file=ctx.layout.relative(path))


def run_alerts(ctx: IngestContext) -> None:
    _check_unknown_ids(ctx)
    _check_phase_window(ctx)
    _check_currencies(ctx)
    _check_fx_coverage(ctx)
    _report_printed_coverage(ctx)
