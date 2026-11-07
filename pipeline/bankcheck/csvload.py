"""Load CSV (plain or gzip) into a typed table, checking header, rows and values, and reporting per file."""
from __future__ import annotations

import csv
import gzip
import re
import zlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .db import IngestContext, lit
from .issues import FileRecord
from .roots import IndexUnreadable, assert_not_golden, read_file_index

MAX_SAMPLES = 5
GZIP_CHUNK_BYTES = 1024 * 1024
UNREADABLE = (OSError, EOFError, UnicodeDecodeError, zlib.error, csv.Error)
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SegmentOf = Callable[[Path], str | None]


@dataclass(frozen=True)
class CsvSpec:
    table: str
    source_type: str
    columns: tuple[str, ...]
    typed: Mapping[str, str] = field(default_factory=dict)
    derived: str = ""
    """Extra SELECT expressions, computed on the typed values of the same row."""


def _read_header(path: Path) -> list[str]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8-sig", newline="") as handle:
        return [cell.strip() for cell in next(csv.reader(handle), [])]


def _read_gzip_to_the_end(path: Path) -> None:
    """DuckDB reads a truncated gzip as if the file were shorter, with no error, so the end of the stream is checked here."""
    with gzip.open(path, "rb") as handle:
        while handle.read(GZIP_CHUNK_BYTES):
            pass


def _has_every_expected_column(header: Sequence[str], spec: CsvSpec) -> bool:
    """Las columnas se leen por su nombre, así que basta con que estén todas, una vez cada una y en cualquier orden."""
    return all(header.count(name) == 1 for name in spec.columns)


def _header_ok(ctx: IngestContext, spec: CsvSpec, path: Path, segment: str | None) -> bool:
    relative = ctx.layout.relative(path)
    try:
        header = _read_header(path)
        if path.suffix == ".gz":
            _read_gzip_to_the_end(path)
    except UNREADABLE as error:
        ctx.issues.error("FILE_UNREADABLE", spec.source_type, str(error), file=relative, segment=segment)
        ctx.manifest.add(FileRecord(relative, spec.source_type, segment, path.stat().st_size, 0, "error", str(error)))
        return False
    if _has_every_expected_column(header, spec):
        extra = [name for name in header if name not in spec.columns]
        if extra:
            ctx.issues.warn("EXTRA_COLUMNS", spec.source_type, f"columnas de más, se ignoran: {extra}", file=relative, segment=segment)
        return True
    detail = f"cabecera inesperada: {header}; se esperaba {list(spec.columns)}"
    ctx.issues.error("BAD_HEADER", spec.source_type, detail, file=relative, segment=segment)
    ctx.manifest.add(FileRecord(relative, spec.source_type, segment, path.stat().st_size, 0, "error", "cabecera inesperada"))
    return False


def _reader_columns(header: Sequence[str], spec: CsvSpec) -> list[str]:
    """Los nombres para leer el fichero por posición. Una columna de más puede venir repetida, sin nombre o llamarse `filename`."""
    return [name if name in spec.columns else f"_extra_{position}" for position, name in enumerate(header)]


def _reader_sql(paths: Sequence[Path], columns_in_file: Sequence[str]) -> str:
    files = "[" + ", ".join(lit(path) for path in paths) + "]"
    columns = ", ".join(f"{lit(name)}: 'VARCHAR'" for name in columns_in_file)
    return (
        f"read_csv({files}, header=true, auto_detect=false, delim=',', quote='\"', escape='\"', "
        f"columns={{{columns}}}, filename=true, store_rejects=true, "
        f"rejects_table='_rejects', rejects_scan='_rejects_scan', rejects_limit=0)"
    )


def _raw_select_sql(paths: Sequence[Path], spec: CsvSpec) -> str:
    """Las columnas esperadas de todos los ficheros, por nombre. Un lector por cada cabecera distinta."""
    by_header: dict[tuple[str, ...], list[Path]] = {}
    for path in paths:
        by_header.setdefault(tuple(_read_header(path)), []).append(path)
    wanted = ", ".join(spec.columns)
    return " UNION ALL ".join(
        f"SELECT {wanted}, filename FROM {_reader_sql(group, _reader_columns(header, spec))}"
        for header, group in by_header.items()
    )


def _report_rejects(ctx: IngestContext, spec: CsvSpec) -> None:
    rows = ctx.con.execute(
        """SELECT DISTINCT s.file_path, r.line, r.error_type, r.csv_line
           FROM _rejects r JOIN _rejects_scan s USING (scan_id, file_id)
           ORDER BY s.file_path, r.line"""
    ).fetchall()
    for path, line, kind, content in rows:
        ctx.issues.error("CSV_ROW_REJECTED", spec.source_type, f"{kind}: {str(content)[:120]}", file=ctx.layout.relative(path), line=int(line))


def _report_bad_values(ctx: IngestContext, spec: CsvSpec) -> None:
    for column, kind in spec.typed.items():
        count, sample = ctx.con.execute(
            f"""SELECT count(*), (list({column} ORDER BY {column}) FILTER ({column} IS NOT NULL))[:{MAX_SAMPLES}]
                FROM _raw WHERE {column} IS NOT NULL AND TRY_CAST({column} AS {kind}) IS NULL"""
        ).fetchone()
        if count:
            ctx.issues.error("BAD_VALUE", spec.source_type, f"{spec.table}.{column} no es {kind}; ejemplos: {sample}", count=count)


def _record_files(ctx: IngestContext, spec: CsvSpec, paths: Sequence[Path], segment_of: SegmentOf) -> None:
    rows = dict(ctx.con.execute("SELECT filename, count(*) FROM _raw GROUP BY 1").fetchall())
    for path in paths:
        count = rows.get(str(path), 0)
        ctx.manifest.add(FileRecord(ctx.layout.relative(path), spec.source_type, segment_of(path), path.stat().st_size, count, "ok"))
        if not count:
            ctx.issues.warn("EMPTY_FILE", spec.source_type, "fichero sin filas", file=ctx.layout.relative(path))


def _typed_select(spec: CsvSpec, root: Path) -> str:
    casts = [f"TRY_CAST({name} AS {spec.typed[name]}) AS {name}" if name in spec.typed else name for name in spec.columns]
    source = f"replace(filename, {lit(str(root) + '/')}, '') AS src_file"
    return ", ".join([*casts, source])


def _create_empty(ctx: IngestContext, spec: CsvSpec) -> None:
    derived = f", {spec.derived}" if spec.derived else ""
    ctx.con.execute(f"CREATE OR REPLACE TABLE {spec.table} AS SELECT *{derived} FROM (SELECT * FROM (SELECT NULL AS _x) , (SELECT {_empty_values(spec)}, NULL::VARCHAR AS src_file) WHERE false)")
    ctx.con.execute(f"ALTER TABLE {spec.table} DROP COLUMN _x")


def _empty_values(spec: CsvSpec) -> str:
    return ", ".join(f"NULL::{spec.typed.get(name, 'VARCHAR')} AS {name}" for name in spec.columns)


def is_iso_date(text: str) -> bool:
    if not ISO_DATE.match(text):
        return False
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True


def _blank_bad_dates(ctx: IngestContext, entries: dict[str, dict[str, str]], date_column: str, source: str, relative: str) -> dict[str, dict[str, str]]:
    """These dates go straight into a DATE column, where a single bad one would stop the whole load."""
    bad = {file: row[date_column] for file, row in entries.items() if row.get(date_column) and not is_iso_date(row[date_column])}
    if not bad:
        return entries
    samples = sorted(set(bad.values()))[:MAX_SAMPLES]
    ctx.issues.error("BAD_VALUE", source, f"{date_column} no es una fecha AAAA-MM-DD; ejemplos: {samples}", file=relative, count=len(bad))
    return {file: {**row, date_column: ""} if file in bad else row for file, row in entries.items()}


def load_file_index(ctx: IngestContext, path: Path, source: str, date_column: str) -> dict[str, dict[str, str]]:
    """Rows of an index.csv by file name. Whatever is wrong with it is reported here, so it can never stop the load."""
    relative = ctx.layout.relative(path)
    size = path.stat().st_size
    try:
        entries, repeated, unnamed = read_file_index(path)
    except IndexUnreadable as error:
        ctx.issues.error("FILE_UNREADABLE", source, str(error), file=relative)
        ctx.manifest.add(FileRecord(relative, source, "index", size, 0, "error", str(error)[:200]))
        return {}
    ctx.manifest.add(FileRecord(relative, source, "index", size, len(entries) + repeated + unnamed, "ok"))
    if repeated:
        ctx.issues.warn("INDEX_REPEATED_FILE", source, "ficheros repetidos en index.csv; se conserva la última entrada", file=relative, count=repeated)
    if unnamed:
        ctx.issues.error("BAD_VALUE", source, "filas de index.csv sin nombre de fichero", file=relative, count=unnamed)
    return _blank_bad_dates(ctx, entries, date_column, source, relative)


def load_csv(ctx: IngestContext, spec: CsvSpec, paths: Sequence[Path], segment_of: SegmentOf = lambda _: None) -> int:
    """Create `spec.table` from the files that pass the header check; returns the row count."""
    for path in paths:
        assert_not_golden(path)
    accepted = [path for path in paths if _header_ok(ctx, spec, path, segment_of(path))]
    con = ctx.con
    if not accepted:
        _create_empty(ctx, spec)
        return 0
    con.execute("DROP TABLE IF EXISTS _rejects; DROP TABLE IF EXISTS _rejects_scan; DROP TABLE IF EXISTS _raw")
    con.execute(f"CREATE TEMP TABLE _raw AS {_raw_select_sql(accepted, spec)}")
    _report_rejects(ctx, spec)
    _report_bad_values(ctx, spec)
    _record_files(ctx, spec, accepted, segment_of)
    derived = f", {spec.derived}" if spec.derived else ""
    select = _typed_select(spec, ctx.layout.root)
    con.execute(f"CREATE OR REPLACE TABLE {spec.table} AS SELECT *{derived} FROM (SELECT {select} FROM _raw)")
    con.execute("DROP TABLE _raw")
    return con.execute(f"SELECT count(*) FROM {spec.table}").fetchone()[0]
