"""Batch detail: payment runs, direct debits, card settlements and cheque deposits."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .csvload import CsvSpec, load_csv
from .db import IngestContext
from .issues import ERROR, Issue, WARN
from .tables import DATE, MONEY

_FILE_FACTS = """regexp_extract(src_file, '([A-Z]{2}[0-9]{2})_[^/]*$', 1) AS entity,
    regexp_replace(regexp_extract(src_file, '_([0-9]{6})(_[0-9]+)?[.]csv$', 1), '([0-9]{4})([0-9]{2})', '\\1-\\2') AS month"""


@dataclass(frozen=True)
class Control:
    code: str
    detail: str
    sql: str
    """Must return one row per problem; the count is reported."""


@dataclass(frozen=True)
class AuxSource:
    folder: str
    spec: CsvSpec
    controls: tuple[Control, ...]
    key: tuple[str, ...]


PAYMENT_RUNS = AuxSource(
    "payment_runs",
    CsvSpec(
        "payment_run_line",
        "payment_runs",
        (
            "run_id", "run_type", "execution_date", "debtor_account_id", "end_to_end_id", "beneficiary", "beneficiary_iban",
            "currency", "transfer_amount", "document_number", "document_type", "document_amount", "remittance_info",
        ),
        {"execution_date": DATE, "transfer_amount": MONEY, "document_amount": MONEY},
        f"bank_of(debtor_account_id) AS bank, {_FILE_FACTS}, pnorm(document_number) AS document_norm",
    ),
    (
        Control(
            "PAYMENT_RUN_TOTAL_MISMATCH",
            "la suma de documentos no cuadra con el importe de la transferencia",
            # end_to_end_id is not unique within a run: the sequence restarts per currency, so the transfer is the whole group.
            """SELECT run_id, end_to_end_id, beneficiary, currency, transfer_amount FROM payment_run_line GROUP BY 1, 2, 3, 4, 5
               HAVING abs(sum(document_amount) - transfer_amount) > 0.01""",
        ),
    ),
    ("run_id", "end_to_end_id", "beneficiary", "currency", "document_number", "document_type", "document_amount"),
)

DIRECT_DEBITS = AuxSource(
    "direct_debit_batches",
    CsvSpec(
        "dd_batch_line",
        "direct_debit_batches",
        (
            "batch_id", "scheme", "collection_date", "account_id", "currency", "batch_total", "end_to_end_id", "mandate_id",
            "debtor_name", "debtor_iban", "amount", "remittance_info", "re_presentation",
        ),
        {"collection_date": DATE, "batch_total": MONEY, "amount": MONEY},
        f"bank_of(account_id) AS bank, {_FILE_FACTS}",
    ),
    (
        Control(
            "DD_BATCH_TOTAL_MISMATCH",
            "la suma de recibos no cuadra con el total de la remesa",
            "SELECT batch_id FROM dd_batch_line GROUP BY 1 HAVING abs(sum(amount) - any_value(batch_total)) > 0.01",
        ),
    ),
    ("batch_id", "end_to_end_id"),
)

CARD_SETTLEMENTS = AuxSource(
    "card_settlements",
    CsvSpec(
        "card_settlement_line",
        "card_settlements",
        (
            "settlement_id", "acquirer", "merchant_id", "settlement_date", "account_id", "currency", "order_number", "card",
            "auth_code", "operation_date", "gross", "fee", "net",
        ),
        {"settlement_date": DATE, "operation_date": DATE, "gross": MONEY, "fee": MONEY, "net": MONEY},
        f"bank_of(account_id) AS bank, {_FILE_FACTS}, pnorm(order_number) AS order_norm",
    ),
    (
        Control(
            "CARD_NET_MISMATCH",
            "bruto menos comisión no es el neto",
            "SELECT settlement_id, order_number FROM card_settlement_line WHERE abs(gross - fee - net) > 0.01",
        ),
    ),
    ("settlement_id", "order_number", "auth_code"),
)

CHEQUE_DEPOSITS = AuxSource(
    "cheque_deposits",
    CsvSpec(
        "cheque_deposit_line",
        "cheque_deposits",
        (
            "deposit_id", "deposit_date", "account_id", "currency", "deposit_total", "cheque_number", "drawer",
            "drawer_bank_routing", "amount", "stub_reference",
        ),
        {"deposit_date": DATE, "deposit_total": MONEY, "amount": MONEY},
        f"bank_of(account_id) AS bank, {_FILE_FACTS}, pnorm(stub_reference) AS stub_norm",
    ),
    (
        Control(
            "CHEQUE_TOTAL_MISMATCH",
            "la suma de cheques no cuadra con el total del depósito",
            "SELECT deposit_id FROM cheque_deposit_line GROUP BY 1 HAVING abs(sum(amount) - any_value(deposit_total)) > 0.01",
        ),
    ),
    ("deposit_id", "cheque_number"),
)

AUX_SOURCES = (PAYMENT_RUNS, DIRECT_DEBITS, CARD_SETTLEMENTS, CHEQUE_DEPOSITS)


def _segment(path: Path) -> str | None:
    return path.name.split("_", 1)[0]


def _run_controls(ctx: IngestContext, source: AuxSource) -> None:
    table = source.spec.table
    for control in source.controls:
        count = ctx.con.execute(f"SELECT count(*) FROM ({control.sql})").fetchone()[0]
        if count:
            ctx.issues.add(Issue(WARN, control.code, source.spec.source_type, control.detail, count=count))
    columns = ", ".join(source.key)
    repeated = ctx.con.execute(f"SELECT coalesce(sum(n - 1), 0) FROM (SELECT count(*) AS n FROM {table} GROUP BY {columns}) WHERE n > 1").fetchone()[0]
    if repeated:
        ctx.issues.add(Issue(WARN, "AUX_DUPLICATE_ROW", source.spec.source_type, f"filas repetidas por ({columns})", count=int(repeated)))


def load_aux_files(ctx: IngestContext) -> None:
    for source in AUX_SOURCES:
        folder = ctx.layout.phase_dir / source.folder
        paths = sorted(path for path in folder.glob("*.csv") if not path.name.startswith(".")) if folder.is_dir() else []
        if not paths:
            ctx.issues.add(Issue(ERROR, "MISSING_FILE", source.spec.source_type, f"carpeta vacía o ausente: {source.folder}"))
        rows = load_csv(ctx, source.spec, paths, _segment)
        if rows:
            _run_controls(ctx, source)
