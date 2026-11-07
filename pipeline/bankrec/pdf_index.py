"""PDFs are indexed but not parsed: their text is extracted on demand and cached in the warehouse."""
from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import duckdb
import pypdfium2

from .csvload import load_file_index
from .db import IngestContext, bulk_insert
from .remittance.pdf_fmt import read_pdf_text
from .roots import Layout, nfc

SOURCE = "pdfs"
INDEX_NAME = "index.csv"

CREATE_TABLES = """
CREATE OR REPLACE TABLE pdf_index (
    file VARCHAR, src_file VARCHAR, document_type VARCHAR, document_number VARCHAR, document_norm VARCHAR, entity VARCHAR,
    party_id VARCHAR, document_date DATE, month VARCHAR, on_disk BOOLEAN, size_bytes BIGINT
);
CREATE OR REPLACE TABLE pdf_text_cache (file VARCHAR PRIMARY KEY, text VARCHAR);
"""


def _pdfs_on_disk(folder: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for current, subfolders, names in os.walk(folder):
        subfolders[:] = [name for name in subfolders if not name.startswith(".")]
        for name in names:
            if name.lower().endswith(".pdf"):
                path = Path(current) / name
                found[nfc(os.path.relpath(path, folder))] = path
    return found


def _norm(text: str) -> str:
    return "".join(char for char in text.upper() if char.isascii() and char.isalnum())


def _rows(ctx: IngestContext, folder: Path, entries: dict[str, dict[str, str]], disk: dict[str, Path]) -> Iterator[list[object]]:
    for file in sorted(set(entries) | set(disk)):
        entry = entries.get(file, {})
        path = disk.get(file)
        date = entry.get("document_date") or None
        number = entry.get("document_number") or None
        yield [
            file, ctx.layout.relative(folder / file), entry.get("document_type"), number, _norm(number) if number else None,
            entry.get("entity") or file.split("/", 1)[0], entry.get("party_id") or None, date, date[:7] if date else None,
            path is not None, path.stat().st_size if path else None,
        ]


def index_pdfs(ctx: IngestContext) -> None:
    ctx.con.execute(CREATE_TABLES)
    folder = ctx.layout.phase_dir / SOURCE
    index_path = folder / INDEX_NAME
    if not index_path.is_file():
        ctx.issues.error("MISSING_FILE", SOURCE, "falta pdfs/index.csv", file=ctx.layout.relative(index_path))
        return
    entries = load_file_index(ctx, index_path, SOURCE, "document_date")
    disk = _pdfs_on_disk(folder)
    bulk_insert(ctx.con, "pdf_index", _rows(ctx, folder, entries, disk))
    missing, extra = len(set(entries) - set(disk)), len(set(disk) - set(entries))
    if missing:
        report = ctx.issues.warn if ctx.layout.phase in ("train", "sample") else ctx.issues.error
        report("INDEXED_FILE_MISSING", SOURCE, "PDFs del índice que no están en disco (esperado en TRAIN lite)", count=missing)
    if extra:
        ctx.issues.warn("FILE_NOT_IN_INDEX", SOURCE, "PDFs en disco que no están en index.csv", count=extra)


def _resolve(con: duckdb.DuckDBPyConnection, reference: str) -> list[str]:
    rows = con.execute(
        "SELECT file FROM pdf_index WHERE on_disk AND (file = ? OR document_norm = ?) ORDER BY file",
        [nfc(reference), _norm(reference)],
    ).fetchall()
    return [row[0] for row in rows]


def pdf_text(con: duckdb.DuckDBPyConnection, layout: Layout, reference: str) -> dict[str, str]:
    """Text of the PDFs a file path or printed document number points to, extracted once and then cached."""
    found: dict[str, str] = {}
    for file in _resolve(con, reference):
        cached = con.execute("SELECT text FROM pdf_text_cache WHERE file = ?", [file]).fetchone()
        if cached:
            found[file] = cached[0]
            continue
        path = layout.phase_dir / SOURCE / file
        try:
            text = read_pdf_text(path)
        except (pypdfium2.PdfiumError, OSError) as error:
            raise RuntimeError(f"no se pudo leer el PDF {file}: {error}") from error
        con.execute("INSERT INTO pdf_text_cache VALUES (?, ?)", [file, text])
        found[file] = text
    return found
