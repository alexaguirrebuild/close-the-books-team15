"""Read every advice of the phase into `remittance_advice` and `remittance_line`."""
from __future__ import annotations

import logging
import os
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

from ..csvload import load_file_index
from ..db import IngestContext, bulk_insert
from ..issues import FileRecord
from ..roots import is_excluded, nfc, rel_key
from .common import Advice, check_amounts_storable, check_control
from .csv_fmt import parse_csv_advice
from .pdf_fmt import parse_pdf_advice
from .txt_fmt import parse_txt_advice
from .xlsx_fmt import parse_xlsx_advice

SOURCE = "remittance_advices"
INDEX_NAME = "index.csv"
PROGRESS_EVERY = 5000
logger = logging.getLogger("bankcheck")

CREATE_TABLES = """
CREATE OR REPLACE TABLE remittance_advice (
    advice_id VARCHAR, src_file VARCHAR, entity VARCHAR, payer_name VARCHAR, received_date DATE, month VARCHAR,
    format VARCHAR, layout VARCHAR, payment_ref VARCHAR, payment_date DATE, currency VARCHAR, control_amount DECIMAL(18,2),
    lines_sum DECIMAL(18,2), n_lines INTEGER, n_repeated INTEGER, control_status VARCHAR, on_disk BOOLEAN
);
CREATE OR REPLACE TABLE remittance_line (
    advice_id VARCHAR, line_no INTEGER, document VARCHAR, document_norm VARCHAR, doc_date DATE, gross DECIMAL(18,2),
    deduction DECIMAL(18,2), reason VARCHAR, paid DECIMAL(18,2), your_order VARCHAR, voucher VARCHAR, is_repeated BOOLEAN, src_file VARCHAR
);
"""


def _parse_file(path: Path) -> Advice:
    kind = path.suffix.lower()
    if kind == ".csv":
        return parse_csv_advice(path.read_text(encoding="utf-8"))
    if kind == ".txt":
        return parse_txt_advice(path.read_text(encoding="utf-8"))
    if kind == ".xlsx":
        return parse_xlsx_advice(path)
    if kind == ".pdf":
        return parse_pdf_advice(path)
    raise ValueError(f"formato de aviso desconocido: {kind}")


def _files_on_disk(folder: Path) -> dict[str, Path]:
    """Keyed by the NFC path relative to the folder, the form used by index.csv."""
    found: dict[str, Path] = {}
    for current, subfolders, names in os.walk(folder):
        subfolders[:] = [name for name in subfolders if not name.startswith(".")]
        for name in names:
            path = Path(current) / name
            if name != INDEX_NAME and not name.startswith(".") and not is_excluded(path):
                found[rel_key(path, folder)] = path
    return found


def _read_index(ctx: IngestContext, folder: Path) -> dict[str, dict[str, str]]:
    index_path = folder / INDEX_NAME
    if index_path.is_file():
        return load_file_index(ctx, index_path, SOURCE, "received_date")
    ctx.issues.error("MISSING_FILE", SOURCE, "falta index.csv", file=ctx.layout.relative(index_path))
    return {}


def _line_rows(advice_id: str, src_file: str, parsed: Advice, repeated: frozenset[int]) -> Iterator[list[object]]:
    for position, line in enumerate(parsed.lines):
        yield [
            advice_id, position + 1, line.document, _norm(line.document), line.doc_date, line.gross, line.deduction, line.reason,
            line.paid, line.your_order, line.voucher, position in repeated, src_file,
        ]


def _norm(document: str) -> str:
    return "".join(char for char in document.upper() if char.isascii() and char.isalnum())


def _advice_row(advice_id: str, src_file: str, meta: dict[str, str], entity: str | None, parsed: Advice | None, status: str, lines_sum, repeated: int, on_disk: bool) -> list[object]:
    received = meta.get("received_date")
    return [
        advice_id, src_file, meta.get("entity") or entity, meta.get("payer_name"), received, received[:7] if received else None,
        meta.get("format") or Path(advice_id).suffix.lstrip("."), parsed.layout if parsed else None,
        parsed.payment_ref if parsed else None, parsed.payment_date if parsed else None, parsed.currency if parsed else None,
        parsed.control_amount if parsed else None, lines_sum, len(parsed.lines) if parsed else 0, repeated, status, on_disk,
    ]


def _report_quality(ctx: IngestContext, relative: str, segment: str, parsed: Advice, status: str) -> None:
    for problem in parsed.problems:
        ctx.issues.error("REMITTANCE_PARTIAL", SOURCE, problem, file=relative, segment=segment)
    if status == "sum_mismatch":
        ctx.issues.warn("REMITTANCE_SUM_MISMATCH", SOURCE, f"las líneas no suman lo anunciado ({parsed.control_amount})", file=relative, segment=segment)
    elif status == "no_lines":
        ctx.issues.error("REMITTANCE_NO_LINES", SOURCE, "aviso sin líneas", file=relative, segment=segment)


def load_remittance(ctx: IngestContext) -> None:
    ctx.con.execute(CREATE_TABLES)
    folder = ctx.layout.phase_dir / SOURCE
    if not folder.is_dir():
        ctx.issues.error("MISSING_FILE", SOURCE, "falta la carpeta de avisos")
        return
    index = _read_index(ctx, folder)
    disk = _files_on_disk(folder)
    advice_rows, line_rows, statuses = _parse_all(ctx, folder, index, disk)
    bulk_insert(ctx.con, "remittance_advice", advice_rows)
    bulk_insert(ctx.con, "remittance_line", line_rows)
    logger.info("Avisos: %s", dict(statuses))
    _report_coverage(ctx, index, disk)


def _parse_all(ctx: IngestContext, folder: Path, index: dict, disk: dict[str, Path]) -> tuple[list, list, Counter]:
    advice_rows: list[list[object]] = []
    line_rows: list[list[object]] = []
    statuses: Counter = Counter()
    for done, advice_id in enumerate(sorted(set(index) | set(disk)), start=1):
        meta = index.get(advice_id, {})
        entity = advice_id.split("/", 1)[0]
        src_file = ctx.layout.relative(folder / advice_id)
        path = disk.get(advice_id)
        if path is None:
            statuses["not_on_disk"] += 1
            advice_rows.append(_advice_row(advice_id, src_file, meta, entity, None, "not_on_disk", None, 0, False))
            continue
        status = _parse_one(ctx, advice_id, src_file, path, meta, entity, advice_rows, line_rows)
        statuses[status] += 1
        if done % PROGRESS_EVERY == 0:
            logger.info("Avisos procesados: %d", done)
    return advice_rows, line_rows, statuses


def _parse_one(ctx: IngestContext, advice_id: str, src_file: str, path: Path, meta: dict, entity: str, advice_rows: list, line_rows: list) -> str:
    segment = f"{path.suffix.lstrip('.')}/{entity}"
    size = path.stat().st_size if path.exists() else 0  # A broken link has no size, and is reported below like any other bad file.
    try:
        parsed = _parse_file(path)
        check_amounts_storable(parsed)
        control = check_control(parsed)
        parsed_lines = list(_line_rows(advice_id, src_file, parsed, control.repeated))
    except Exception as error:  # One untrusted file must never stop the load: it is reported here, by name and type.
        ctx.issues.error("REMITTANCE_PARSE_ERROR", SOURCE, f"{type(error).__name__}: {error}", file=src_file, segment=segment)
        ctx.manifest.add(FileRecord(src_file, SOURCE, segment, size, 0, "error", str(error)[:200]))
        advice_rows.append(_advice_row(advice_id, src_file, meta, entity, None, "error", None, 0, True))
        return "error"
    _report_quality(ctx, src_file, segment, parsed, control.status)
    advice_rows.append(_advice_row(advice_id, src_file, meta, entity, parsed, control.status, control.lines_sum, len(control.repeated), True))
    line_rows.extend(parsed_lines)
    ctx.manifest.add(FileRecord(src_file, SOURCE, segment, size, len(parsed.lines), "ok" if not parsed.problems else "partial"))
    return control.status


def _report_coverage(ctx: IngestContext, index: dict, disk: dict[str, Path]) -> None:
    missing = len(set(index) - set(disk))
    extra = len(set(disk) - set(index))
    if missing:
        severity = ctx.issues.warn if ctx.layout.phase in ("train", "sample") else ctx.issues.error
        severity("INDEXED_FILE_MISSING", SOURCE, "ficheros del índice que no están en disco (esperado en TRAIN lite)", count=missing)
    if extra:
        ctx.issues.warn("FILE_NOT_IN_INDEX", SOURCE, "ficheros en disco que no están en index.csv", count=extra)
