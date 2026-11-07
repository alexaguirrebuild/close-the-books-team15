"""Normalised views, items and masters of one phase."""
from __future__ import annotations

from pathlib import Path

from .csvload import CsvSpec, load_csv
from .db import IngestContext

MONEY = "DECIMAL(18,2)"
DATE = "DATE"

BANK_TRANSACTIONS = CsvSpec(
    "_bank_txn_loaded",
    "bank_transactions",
    (
        "bank_txn_id", "account_id", "entity", "bank", "statement_format", "statement_file", "booking_date", "value_date",
        "amount", "currency", "bank_code", "bank_subcode", "ref1", "ref2", "text", "counterparty_name",
        "counterparty_account", "end_to_end_id", "remittance_info", "orig_amount", "orig_currency",
    ),
    {"booking_date": DATE, "value_date": DATE, "amount": MONEY, "orig_amount": MONEY},
)

_MONEY_COLUMNS = {"net_amount": MONEY, "tax_amount": MONEY, "total_amount": MONEY}

AR_INVOICES = CsvSpec(
    "ar_invoice",
    "ar_invoices",
    (
        "item_id", "entity", "document_number", "issue_date", "due_date", "customer_id", "customer_name", "currency",
        "net_amount", "tax_amount", "total_amount", "payment_terms", "customer_po", "payment_method",
    ),
    {"issue_date": DATE, "due_date": DATE, **_MONEY_COLUMNS},
    "month_of(issue_date) AS month",
)

AP_INVOICES = CsvSpec(
    "ap_invoice",
    "ap_invoices",
    (
        "item_id", "entity", "supplier_id", "supplier_name", "supplier_invoice_number", "issue_date", "due_date", "currency",
        "net_amount", "tax_amount", "total_amount", "payment_terms", "our_po", "payment_method",
    ),
    {"issue_date": DATE, "due_date": DATE, **_MONEY_COLUMNS},
    "month_of(issue_date) AS month",
)

CREDIT_NOTES = CsvSpec(
    "credit_note",
    "credit_notes",
    (
        "item_id", "side", "entity", "document_number", "issue_date", "party_id", "party_name", "currency",
        "net_amount", "tax_amount", "total_amount", "related_document", "reason",
    ),
    {"issue_date": DATE, **_MONEY_COLUMNS},
    "month_of(issue_date) AS month",
)

OPEN_ITEMS = CsvSpec(
    "open_item_at_start",
    "open_items_at_start",
    ("item_id", "item_type", "entity", "party_id", "document_number", "issue_date", "due_date", "currency", "total_amount", "open_amount"),
    {"issue_date": DATE, "due_date": DATE, "total_amount": MONEY, "open_amount": MONEY},
    "month_of(issue_date) AS month",
)

MASTERS = (
    CsvSpec("entity", "masters", ("entity", "legal_name", "country", "functional_currency", "role", "in_group_since"), {"in_group_since": DATE}),
    CsvSpec(
        "bank_account",
        "masters",
        (
            "account_id", "entity", "bank", "bank_name", "bic", "currency", "iban", "account_number", "purpose",
            "statement_format", "statement_format_from_2025_10_01", "opened",
        ),
        {"opened": DATE},
    ),
    CsvSpec(
        "customer",
        "masters",
        (
            "customer_id", "entity", "name", "trade_name", "customer_type", "country", "tax_id", "iban", "sepa_mandate_id",
            "payment_terms", "invoice_currency", "group_id", "parent_customer_id", "created", "blocked_since",
        ),
        {"created": DATE, "blocked_since": DATE},
    ),
    CsvSpec(
        "supplier",
        "masters",
        (
            "supplier_id", "entity", "name", "country", "tax_id", "supplier_type", "iban", "account_number", "payment_method",
            "payment_terms", "invoice_currency", "created", "blocked_since",
        ),
        {"created": DATE, "blocked_since": DATE},
    ),
    CsvSpec("category", "masters", ("category", "description", "gl_account")),
    CsvSpec(
        "payment_term",
        "masters",
        ("code", "days", "early_payment_discount_pct", "discount_days", "description"),
        {"days": "INTEGER", "early_payment_discount_pct": "DECIMAL(5,2)", "discount_days": "INTEGER"},
    ),
    CsvSpec("fx_rate", "masters", ("date", "currency", "units_per_eur", "source"), {"date": DATE, "units_per_eur": "DOUBLE"}),
)

MASTER_FILES = {
    "entity": "entities.csv",
    "bank_account": "bank_accounts.csv",
    "customer": "customers.csv",
    "supplier": "suppliers.csv",
    "category": "categories.csv",
    "payment_term": "payment_terms.csv",
    "fx_rate": "fx_rates.csv",
}


def _master_path(ctx: IngestContext, file_name: str) -> Path:
    override = ctx.layout.masters_override_dir
    if override is None:
        return ctx.layout.masters_dir / file_name
    if (override / file_name).is_file():
        return override / file_name
    ctx.issues.warn("MASTER_NOT_UPDATED", "masters", f"{file_name} no está en {override.name}; se usa el maestro anterior")
    return ctx.layout.masters_dir / file_name


def load_masters(ctx: IngestContext) -> None:
    if ctx.layout.phase == "final" and ctx.layout.masters_override_dir is None:
        ctx.issues.error("MASTERS_FINAL_MISSING", "masters", "FINAL debe traer final/masters_final/: sin él no están la filial nueva ni los cambios del semestre")
    for spec in MASTERS:
        path = _master_path(ctx, MASTER_FILES[spec.table])
        if not path.is_file():
            ctx.issues.error("MISSING_FILE", "masters", f"falta el maestro {MASTER_FILES[spec.table]}", file=ctx.layout.relative(path))
        load_csv(ctx, spec, [path] if path.is_file() else [], lambda _: spec.table)


def _phase_table(ctx: IngestContext, spec: CsvSpec, name: str, required: bool = True) -> int:
    path = ctx.layout.phase_dir / name
    if path.is_file():
        return load_csv(ctx, spec, [path], lambda _: spec.table)
    if required:
        ctx.issues.error("MISSING_FILE", spec.source_type, f"falta {name}", file=ctx.layout.relative(path))
    return load_csv(ctx, spec, [])


def load_bank_transactions(ctx: IngestContext) -> None:
    """One row per bank_txn_id. A repeated id is reported and kept once, so the view stays unique."""
    _phase_table(ctx, BANK_TRANSACTIONS, "bank_transactions.csv.gz")
    repeated = ctx.con.execute("SELECT count(*) - count(DISTINCT bank_txn_id) FROM _bank_txn_loaded").fetchone()[0]
    if repeated:
        ctx.issues.error("DUPLICATE_BANK_TXN", "bank_transactions", "bank_txn_id repetidos en la vista normalizada", count=repeated)
    ctx.con.execute(
        """CREATE OR REPLACE TABLE bank_txn AS
           SELECT * EXCLUDE (src_file), cents_of(amount) AS amount_cents, month_of(booking_date) AS month, src_file,
                  CAST(NULL AS VARCHAR) AS raw_status, CAST(NULL AS VARCHAR[]) AS raw_files
           FROM _bank_txn_loaded
           QUALIFY row_number() OVER (PARTITION BY bank_txn_id ORDER BY booking_date, value_date) = 1"""
    )
    ctx.con.execute("DROP TABLE _bank_txn_loaded")


def load_items(ctx: IngestContext) -> None:
    _phase_table(ctx, AR_INVOICES, "ar_invoices.csv.gz")
    _phase_table(ctx, AP_INVOICES, "ap_invoices.csv.gz")
    _phase_table(ctx, CREDIT_NOTES, "credit_notes.csv.gz")
    has_opening = (ctx.layout.phase_dir / "open_items_at_start.csv.gz").is_file()
    if ctx.layout.phase in ("live", "final") and not has_opening:
        ctx.issues.error("MISSING_FILE", "open_items_at_start", "LIVE y FINAL deben traer partidas abiertas al inicio")
    _phase_table(ctx, OPEN_ITEMS, "open_items_at_start.csv.gz", required=False)
    _build_item_table(ctx)


ITEM_SQL = """
CREATE OR REPLACE TABLE item AS
WITH unified AS (
    SELECT item_id, 'ARI' AS item_type, 'AR' AS side, entity, document_number AS printed_number, customer_id AS party_id,
           customer_name AS party_name, issue_date, due_date, currency, net_amount, tax_amount, total_amount,
           total_amount AS open_amount, false AS is_opening, payment_terms, customer_po, CAST(NULL AS VARCHAR) AS our_po,
           payment_method, CAST(NULL AS VARCHAR) AS related_document, CAST(NULL AS VARCHAR) AS reason, src_file
    FROM ar_invoice
    UNION ALL
    SELECT item_id, 'API', 'AP', entity, supplier_invoice_number, supplier_id, supplier_name, issue_date, due_date, currency,
           net_amount, tax_amount, total_amount, total_amount, false, payment_terms, NULL, our_po, payment_method, NULL, NULL, src_file
    FROM ap_invoice
    UNION ALL
    SELECT item_id, left(item_id, 3), side, entity, document_number, party_id, party_name, issue_date, NULL, currency,
           net_amount, tax_amount, total_amount, total_amount, false, NULL, NULL, NULL, NULL, related_document, reason, src_file
    FROM credit_note
    UNION ALL
    SELECT o.item_id, o.item_type, CASE WHEN o.item_type LIKE 'AR%' THEN 'AR' ELSE 'AP' END, o.entity, o.document_number, o.party_id,
           coalesce(c.name, s.name), o.issue_date, o.due_date, o.currency, NULL, NULL, o.total_amount, o.open_amount, true,
           NULL, NULL, NULL, NULL, NULL, NULL, o.src_file
    FROM open_item_at_start o
    LEFT JOIN customer c ON o.item_type LIKE 'AR%' AND c.customer_id = o.party_id
    LEFT JOIN supplier s ON o.item_type LIKE 'AP%' AND s.supplier_id = o.party_id
)
SELECT *, pnorm(printed_number) AS printed_norm, cents_of(open_amount) AS open_cents, month_of(issue_date) AS month
FROM unified
QUALIFY row_number() OVER (PARTITION BY item_id ORDER BY is_opening DESC, src_file) = 1
"""


def _build_item_table(ctx: IngestContext) -> None:
    sources = ("ar_invoice", "ap_invoice", "credit_note", "open_item_at_start")
    loaded = sum(ctx.con.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in sources)
    ctx.con.execute(ITEM_SQL)
    unique = ctx.con.execute("SELECT count(*) FROM item").fetchone()[0]
    if loaded != unique:
        ctx.issues.error("DUPLICATE_ITEM", "items", "item_id repetido entre ficheros; se conserva uno", count=loaded - unique)
