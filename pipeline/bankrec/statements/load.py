"""Read every raw statement of the phase into a working table and record its internal controls."""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from pathlib import Path
from xml.etree.ElementTree import ParseError

from ..csvload import is_iso_date
from ..db import IngestContext, bulk_insert
from ..issues import FileRecord
from .camt053 import parse_camt053
from .detect import EXTENSION_FORMATS, detect_format
from .model import ParsedStatement
from .mt940 import parse_mt940
from .n43 import parse_n43

SOURCE = "bank_statements"
HEAD_BYTES = 64
MONTH_IN_NAME = re.compile(r"_(\d{4})(\d{2})\.[^.]+$")
PARSERS: dict[str, Callable[[bytes], ParsedStatement]] = {"n43": parse_n43, "mt940": parse_mt940, "camt053": parse_camt053}
logger = logging.getLogger("bankrec")

CREATE_TABLES = """
CREATE OR REPLACE TABLE _statement_line (
    src_file VARCHAR, src_line INTEGER, account_id VARCHAR, file_month VARCHAR, format VARCHAR, booking_date DATE, value_date DATE,
    amount_cents BIGINT, currency VARCHAR, bank_code VARCHAR, bank_subcode VARCHAR, ref1 VARCHAR, ref2 VARCHAR, text VARCHAR,
    counterparty_name VARCHAR, counterparty_account VARCHAR, end_to_end_id VARCHAR, remittance_info VARCHAR,
    orig_amount DECIMAL(18,2), orig_currency VARCHAR
);
CREATE OR REPLACE TABLE statement_file (
    src_file VARCHAR, file_name VARCHAR, account_id VARCHAR, bank VARCHAR, month VARCHAR, format VARCHAR, lines INTEGER,
    debits INTEGER, credits INTEGER, opening_cents BIGINT, closing_cents BIGINT, control_status VARCHAR
);
"""


def _month_of(path: Path) -> str | None:
    match = MONTH_IN_NAME.search(path.name)
    return f"{match.group(1)}-{match.group(2)}" if match else None


def _bank_of(account: str) -> str | None:
    parts = account.split("-")
    return parts[1] if len(parts) > 1 else None


def _statement_files(folder: Path) -> list[Path]:
    return sorted(
        path
        for account in folder.iterdir()
        if account.is_dir() and not account.name.startswith(".")
        for path in account.iterdir()
        if path.is_file() and not path.name.startswith(".")
    )


def _check_dates(parsed: ParsedStatement) -> None:
    """These dates go straight into DATE columns, where a single bad one would stop the whole load."""
    for line in parsed.lines:
        for day in (line.booking_date, line.value_date):
            if day and not is_iso_date(day):
                raise ValueError(f"línea {line.src_line}: fecha imposible {day}")


def _parse(ctx: IngestContext, path: Path, relative: str) -> ParsedStatement | None:
    segment = f"{path.suffix.lstrip('.')}/{_bank_of(path.parent.name)}"
    data = path.read_bytes()
    detected = detect_format(data[:HEAD_BYTES])
    expected = EXTENSION_FORMATS.get(path.suffix.lower())
    if detected is None:
        ctx.issues.error("STATEMENT_UNKNOWN_FORMAT", SOURCE, "contenido no reconocido", file=relative, segment=segment)
        ctx.manifest.add(FileRecord(relative, SOURCE, segment, len(data), 0, "error", "formato desconocido"))
        return None
    if detected != expected:
        ctx.issues.warn("STATEMENT_EXTENSION_MISMATCH", SOURCE, f"extensión {path.suffix}, contenido {detected}", file=relative, segment=segment)
    try:
        parsed = PARSERS[detected](data)
        _check_dates(parsed)
        return parsed
    except (ValueError, IndexError, ParseError, ArithmeticError) as error:
        ctx.issues.error("STATEMENT_PARSE_ERROR", SOURCE, f"{type(error).__name__}: {error}", file=relative, segment=segment)
        ctx.manifest.add(FileRecord(relative, SOURCE, segment, len(data), 0, "error", str(error)[:200]))
        return None


def _report_quality(ctx: IngestContext, parsed: ParsedStatement, relative: str, segment: str) -> str:
    for problem in parsed.problems:
        ctx.issues.error("STATEMENT_PARTIAL", SOURCE, problem, file=relative, segment=segment)
    control_problems = parsed.control.problems()
    for problem in control_problems:
        ctx.issues.error("STATEMENT_CONTROL", SOURCE, problem, file=relative, segment=segment)
    return "bad" if control_problems or parsed.problems else "ok"


def _line_row(line, relative: str, account: str, month: str | None, kind: str) -> list[object]:
    return [
        relative, line.src_line, account, month, kind, line.booking_date, line.value_date, line.amount_cents, line.currency,
        line.bank_code, line.bank_subcode, line.ref1, line.ref2, line.text, line.counterparty_name, line.counterparty_account,
        line.end_to_end_id, line.remittance_info, line.orig_amount, line.orig_currency,
    ]


def load_statements(ctx: IngestContext) -> int:
    """Returns the number of raw lines read."""
    ctx.con.execute(CREATE_TABLES)
    folder = ctx.layout.phase_dir / SOURCE
    if not folder.is_dir():
        ctx.issues.error("MISSING_FILE", SOURCE, "falta la carpeta de extractos")
        return 0
    files = _statement_files(folder)
    line_rows: list[list[object]] = []
    file_rows: list[list[object]] = []
    for path in files:
        relative = ctx.layout.relative(path)
        account = path.parent.name
        segment = f"{path.suffix.lstrip('.')}/{_bank_of(account)}"
        parsed = _parse(ctx, path, relative)
        if parsed is None:
            continue
        status = _report_quality(ctx, parsed, relative, segment)
        month = _month_of(path)
        line_rows.extend(_line_row(line, relative, account, month, parsed.format) for line in parsed.lines)
        control = parsed.control
        file_rows.append(
            [relative, path.name, account, _bank_of(account), month, parsed.format, len(parsed.lines), control.counted_debits,
             control.counted_credits, control.opening_cents, control.closing_cents, status]
        )
        ctx.manifest.add(FileRecord(relative, SOURCE, segment, path.stat().st_size, len(parsed.lines), "ok" if status == "ok" else "partial"))
    bulk_insert(ctx.con, "_statement_line", line_rows)
    bulk_insert(ctx.con, "statement_file", file_rows)
    logger.info("Extractos: %d ficheros, %d líneas crudas", len(files), len(line_rows))
    return len(line_rows)
