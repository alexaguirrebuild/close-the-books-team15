"""Local gate before a submission: the official format checks plus what they leave out.

Runs the very code the server runs, with an empty golden, so its format verdict matches the platform's; the score
it would compute is meaningless and never reported. Our rules can only make the verdict stricter: full coverage,
items that exist, the documented format, and rows the server accepts but always scores as wrong.

Bank lines and items come from the CSV files of the phase, the server's own reference, never from the warehouse:
a rejected row or a stale build must not hide a missing line.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import logging
import re
import zlib
from collections import Counter
from collections.abc import Collection, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

import duckdb

from ..roots import STATEMENTS_HOLD, DataRootNotFound, Layout, resolve_layout
from .official import EXCEPTIONS, SCORER_SHA256, SUBMISSION_HEADER, lift_limits, load_official_scorer, read_categories
from .writer import report_path, write_json

TWO_DECIMALS = re.compile(r"^\d+\.\d{2}$")
UNIDENTIFIED = "UNIDENTIFIED"
MAX_REPORTED_ERRORS = 50
MAX_SAMPLES = 10
PHASES_WITHOUT_SIZE_LIMIT = ("train", "sample")  # Never uploaded, and real TRAIN output exceeds the upload limits.
EARLIER_PHASES = {"live": ("train",), "final": ("train", "live")}
ITEM_FILES = ("ar_invoices.csv.gz", "ap_invoices.csv.gz", "credit_notes.csv.gz")
OPENING_FILE = "open_items_at_start.csv.gz"
GZIP_MAGIC = b"\x1f\x8b"
GZIP_WINDOW = 16 + zlib.MAX_WBITS
INFLATE_CHUNK = 1024 * 1024
logger = logging.getLogger("bankrec")


class PhaseDataUnreadable(RuntimeError):
    """A CSV of a phase is missing or damaged, so coverage or items cannot be trusted. The submission is not to blame."""


class _ContentTooLarge(Exception):
    """The inflated file passed the scorer's own guard."""


UNREADABLE = (EOFError, UnicodeDecodeError, zlib.error, csv.Error, _ContentTooLarge)
PHASE_DATA_ERRORS = (EOFError, OSError, ValueError, IndexError, zlib.error, csv.Error)


@dataclass
class _Findings:
    errors: list[dict[str, object]] = field(default_factory=list)
    counts: Counter = field(default_factory=Counter)

    def add(self, code: str, row: int | None, message: str, count: int = 1) -> None:
        self.counts[code] += count
        if len(self.errors) < MAX_REPORTED_ERRORS:
            self.errors.append({"code": code, "row": row, "message": message})


@dataclass
class _Contents:
    """What the rows of the file say, gathered while checking them."""

    rows_with_item: int = 0
    rows_without_item: int = 0
    seen: set[str] = field(default_factory=set)
    item_first_row: dict[str, int] = field(default_factory=dict)
    by_category: Counter = field(default_factory=Counter)
    by_exception: Counter = field(default_factory=Counter)

    @property
    def rows(self) -> int:
        return self.rows_with_item + self.rows_without_item

    def add_row(self, txn: str, item: str, number: int, category: str, exception: str) -> None:
        self.seen.add(txn)
        if item:
            self.rows_with_item += 1
            self.item_first_row.setdefault(item, number)
        else:
            self.rows_without_item += 1
        if category:
            self.by_category[category] += 1
        if exception:
            self.by_exception[exception] += 1

    def breakdown(self) -> dict[str, object]:
        return {
            "rows_with_item": self.rows_with_item,
            "rows_without_item": self.rows_without_item,
            "by_category": dict(sorted(self.by_category.items())),
            "by_exception": dict(sorted(self.by_exception.items())),
        }


def _warning(code: str, samples: Iterable[str], message: str) -> dict[str, object]:
    ordered = sorted(samples)
    return {"code": code, "count": len(ordered), "samples": ordered[:MAX_SAMPLES], "message": message}


def _read_column(path: Path, column: str) -> Iterator[str]:
    """With Python's gzip on purpose: DuckDB reads a truncated .csv.gz without error and returns only part of the rows."""
    try:
        with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            position = next(reader, []).index(column)
            yield from (row[position].strip() for row in reader if row)
    except PHASE_DATA_ERRORS as error:
        raise PhaseDataUnreadable(f"{path}: {error}") from error


def _bank_line_ids(layout: Layout) -> list[str]:
    """The same lines the scorer's load_golden takes on the server."""
    return list(_read_column(layout.phase_dir / STATEMENTS_HOLD, "bank_txn_id"))


def _phase_item_ids(layout: Layout) -> set[str]:
    """LIVE and FINAL must ship their opening items: a missing file fails loudly instead of rejecting valid ids."""
    names = [*ITEM_FILES, OPENING_FILE] if layout.phase in EARLIER_PHASES else list(ITEM_FILES)
    return {item for name in names for item in _read_column(layout.phase_dir / name, "item_id") if item}


def _scan_earlier_phase(root: Path, phase: str, candidates: Collection[str]) -> tuple[set[str], bool]:
    """The candidates this phase issued, and whether all of it could be read."""
    try:
        folder = resolve_layout(phase, data_root=root).phase_dir
    except DataRootNotFound:
        return set(), False
    found: set[str] = set()
    complete = True
    for name in ITEM_FILES:
        try:
            found.update(item for item in _read_column(folder / name, "item_id") if item in candidates)
        except PhaseDataUnreadable as error:
            logger.warning("Fase %s incompleta: %s", phase, error)
            complete = False
    return found, complete


def _issued_earlier(layout: Layout, candidates: Collection[str]) -> tuple[set[str], list[str]]:
    """Which ids exist in an earlier phase, and the earlier phases that could not be fully checked."""
    found: set[str] = set()
    unavailable: list[str] = []
    for phase in EARLIER_PHASES.get(layout.phase, ()):
        in_phase, complete = _scan_earlier_phase(layout.root, phase, candidates)
        found |= in_phase
        if not complete:
            unavailable.append(phase)
    return found, unavailable


def _check_item_ids(layout: Layout, contents: _Contents, findings: _Findings) -> list[dict[str, object]]:
    """A return or a duplicate can hit an item closed before the phase, which is in no table of the phase.
    An id we could not look up is neither that nor unknown: it stops the gate under its own code."""
    if not contents.item_first_row:
        return []
    phase_items = _phase_item_ids(layout)
    outside = {item: row for item, row in contents.item_first_row.items() if item not in phase_items}
    if not outside:
        return []
    found, unavailable = _issued_earlier(layout, outside)
    warnings = []
    if found:
        warnings.append(_warning("ITEM_FROM_PRIOR_PHASE", found, "partidas de una fase anterior: legítimas en devoluciones y duplicados; revísalas"))
    unresolved = sorted((row, item) for item, row in outside.items() if item not in found)
    if unresolved and unavailable:
        warnings.append(_warning("PRIOR_PHASE_MISSING", unavailable, "fase anterior incompleta en disco: hay ids que no se pudieron comprobar"))
    for row, item in unresolved:
        if unavailable:
            findings.add("ITEM_UNVERIFIED", row, f"item_id {item} no está en esta fase y no se pudo comprobar en: {', '.join(unavailable)}")
        else:
            findings.add("ITEM_UNKNOWN", row, f"item_id {item} no existe en esta fase ni en las anteriores")
    return warnings


def _check_ingest(layout: Layout, findings: _Findings) -> list[dict[str, object]]:
    """Coverage and items do not depend on the warehouse, but the file was built on it. A warehouse published with
    --allow-errors stops the gate on purpose: whoever forced it must decide while looking at this error."""
    try:
        with duckdb.connect(str(layout.warehouse_path), read_only=True) as con:
            errors = con.execute("SELECT max(errors) FROM ingest_run").fetchone()[0]
    except duckdb.Error as error:
        return [_warning("INGEST_NOT_CHECKED", [layout.relative(layout.warehouse_path)], f"no se pudo leer el estado de la ingesta: {error}")]
    if errors:
        findings.add("INGEST_HAD_ERRORS", None, f"la ingesta de {layout.phase} registró {errors} errores: revisa ingest_report.json", count=errors)
    return []


def _categories(layout: Layout) -> list[str]:
    override = layout.masters_override_dir
    return read_categories(override if override and (override / "categories.csv").is_file() else layout.masters_dir)


def _scorer_for(layout: Layout) -> ModuleType:
    scorer = load_official_scorer(layout)
    return lift_limits(scorer) if layout.phase in PHASES_WITHOUT_SIZE_LIMIT else scorer


def _empty_golden(scorer: ModuleType, txns: list[str], categories: list[str]):
    golden = scorer.Golden()
    golden.txns = {txn: ({}, "", "", "unlabelled") for txn in txns}
    golden.exceptions = list(EXCEPTIONS)
    golden.categories = categories
    return golden


def _inflate(raw: bytes, limit: int) -> bytes:
    """Bounded, and unlike the scorer's own it refuses a stream that ends early instead of keeping what it got."""
    if raw[:2] != GZIP_MAGIC:
        return raw
    parts: list[bytes] = []
    size = 0
    pending = raw
    while pending.strip(b"\x00"):
        stream = zlib.decompressobj(GZIP_WINDOW)
        while not stream.eof:
            chunk = stream.decompress(pending, INFLATE_CHUNK)
            pending = stream.unconsumed_tail
            if not stream.eof and not chunk and not pending:
                raise EOFError("el gzip está truncado")
            size += len(chunk)
            if size > limit:
                raise _ContentTooLarge(f"descomprimido supera {limit / 1e6:.0f} MB")
            parts.append(chunk)
        pending = stream.unused_data
    return b"".join(parts)


def _rows(content: bytes) -> Iterator[list[str]]:
    yield from csv.reader(io.TextIOWrapper(io.BytesIO(content), encoding="utf-8-sig", newline=""))


def _row_problems(item: str, amount: str, category: str, exception: str) -> Iterator[tuple[str, str]]:
    """Rules the server does not enforce. None of them fires on any of the 5.4 M rows of the TRAIN labels."""
    if item and not TWO_DECIMALS.match(amount):
        yield "AMOUNT_NOT_2_DECIMALS", f"amount '{amount}' debe llevar 2 decimales"
    elif item and float(amount) == 0:
        yield "AMOUNT_NOT_POSITIVE", "amount debe ser mayor que 0"
    if not item and not category:
        yield "NO_ITEM_WITHOUT_CATEGORY", "una fila sin partida necesita category"
    if (category == UNIDENTIFIED) != (exception == UNIDENTIFIED):
        yield "UNIDENTIFIED_MISMATCH", "UNIDENTIFIED va a la vez en category y en exception, y solo en filas sin partida"
    if not item and exception not in ("", UNIDENTIFIED):
        yield "NO_ITEM_EXCEPTION", f"una fila sin partida no admite la excepción {exception}"


def _inspect_rows(rows: Iterator[list[str]], findings: _Findings) -> _Contents:
    contents = _Contents()
    header = next(rows, [])
    if [cell.strip().lower() for cell in header] != list(SUBMISSION_HEADER):
        message = f"cabecera distinta de la documentada: {header}; se espera {','.join(SUBMISSION_HEADER)} separada por comas"
        findings.add("NOT_CANONICAL", 1, f"{message}. El servidor puede aceptar otro orden o ';', pero nuestro escritor nunca los genera")
        return contents
    for number, row in enumerate(rows, start=2):
        if len(row) != len(SUBMISSION_HEADER):
            continue
        txn, item, amount = row[0].strip(), row[1].strip(), row[2].strip()
        category, exception = row[3].strip().upper(), row[4].strip().upper()
        contents.add_row(txn, item, number, category, exception)
        for code, message in _row_problems(item, amount, category, exception):
            findings.add(code, number, message)
    return contents


def _read_contents(raw: bytes, limit: int, findings: _Findings) -> tuple[_Contents, int | None]:
    try:
        content = _inflate(raw, limit)
        return _inspect_rows(_rows(content), findings), len(content)
    except UNREADABLE as error:
        findings.add("UNREADABLE_FILE", None, f"no se puede leer el fichero: {error}")
        return _Contents(), None


def _coverage(txns: list[str], contents: _Contents, findings: _Findings) -> dict[str, object]:
    expected = set(txns)
    missing = sorted(expected - contents.seen)
    if missing and contents.rows:
        findings.add("MISSING_LINES", None, f"movimientos de la fase sin ninguna fila: {len(missing):,}; cuentan como fallo", count=len(missing))
    return {"bank_lines": len(txns), "covered": len(expected & contents.seen), "missing": len(missing), "missing_samples": missing[:MAX_SAMPLES]}


def validate_submission(layout: Layout, path: Path) -> dict:
    target = report_path(layout, "validate", path)
    target.unlink(missing_ok=True)  # Whatever fails below, an older "ok": true must not survive it.
    raw = path.read_bytes()
    txns = _bank_line_ids(layout)
    scorer = _scorer_for(layout)
    official = scorer.score_submission(_empty_golden(scorer, txns, _categories(layout)), raw, path.name)
    findings = _Findings()
    for error in official["errors"]:
        findings.add("OFFICIAL", error["row"], error["message"])
    contents, size = _read_contents(raw, scorer.MAX_DECOMPRESS, findings)
    coverage = _coverage(txns, contents, findings)
    warnings = [*_check_item_ids(layout, contents, findings), *_check_ingest(layout, findings)]
    report = {
        "ok": bool(official["ok"]) and not findings.counts,
        "official_format_ok": bool(official["ok"]),
        "phase": layout.phase,
        "file": str(path),
        "file_sha256": hashlib.sha256(raw).hexdigest(),
        "scorer_sha256": SCORER_SHA256,
        "rows": contents.rows,
        "uncompressed_mb": None if size is None else round(size / 1e6, 2),
        "file_mb": round(len(raw) / 1e6, 2),
        "limits_applied": layout.phase not in PHASES_WITHOUT_SIZE_LIMIT,
        "coverage": coverage,
        "breakdown": contents.breakdown(),
        "error_counts": dict(sorted(findings.counts.items())),
        "errors": findings.errors,
        "warnings": warnings,
    }
    write_json(target, report)
    return {**report, "report_path": str(target)}


def print_validation(report: dict, layout: Layout) -> None:
    coverage = report["coverage"]
    verdict = "VÁLIDO" if report["ok"] else "NO VÁLIDO"
    limits = "60 MB y 3 M filas aplicados" if report["limits_applied"] else "sin límite de tamaño: esta fase no se envía"
    size = "tamaño sin comprimir desconocido" if report["uncompressed_mb"] is None else f"{report['uncompressed_mb']} MB sin comprimir"
    lines = [
        f"\n{verdict}  ·  {report['file']}",
        f"Formato oficial (código del servidor): {'OK' if report['official_format_ok'] else 'RECHAZADO'}",
        f"Cobertura: {coverage['covered']:,} de {coverage['bank_lines']:,} movimientos; faltan {coverage['missing']:,}",
        f"Tamaño: {report['rows']:,} filas, {size} ({limits})",
    ]
    if report["error_counts"]:
        lines.append(f"Errores por tipo: {report['error_counts']}")
    lines.extend(f"  aviso {entry['code']} ({entry['count']:,}): {entry['message']}. Ejemplos: {entry['samples']}" for entry in report["warnings"])
    lines.extend(f"  fila {error['row']} [{error['code']}]: {error['message']}" for error in report["errors"][:MAX_SAMPLES * 2])
    lines.append(f"Informe: {layout.relative(report['report_path'])}")
    logger.info("\n".join(lines))
