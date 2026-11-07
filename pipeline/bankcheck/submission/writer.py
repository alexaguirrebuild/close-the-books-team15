"""Writes submissions and reports so that nobody ever reads a half-written file. Use it for any submission, matcher included."""
from __future__ import annotations

import csv
import gzip
import io
import json
import os
import tempfile
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, TextIO

from ..roots import Layout
from .official import SUBMISSION_HEADER


@contextmanager
def _replacing(path: Path) -> Iterator[BinaryIO]:
    """The target changes only when writing has finished; a failure leaves it as it was, and no temporary behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(descriptor, "wb") as raw:
            yield raw
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _text_stream(raw: BinaryIO, compress: bool) -> TextIO:
    """No file name or clock in the gzip header: the same rows always give the same bytes."""
    target = gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) if compress else raw
    return io.TextIOWrapper(target, encoding="utf-8", newline="")


def write_submission(path: Path, rows: Iterable[Sequence[str]]) -> int:
    """Rows are (bank_txn_id, item_id, amount, category, exception). Gzip if the name ends in .gz. Returns the rows written."""
    count = 0
    with _replacing(path) as raw, _text_stream(raw, path.suffix == ".gz") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(SUBMISSION_HEADER)
        for row in rows:
            writer.writerow(row)
            count += 1
    return count


def write_text(path: Path, text: str) -> None:
    with _replacing(path) as raw:
        raw.write(text.encode("utf-8"))


def write_json(path: Path, data: dict) -> None:
    write_text(path, json.dumps(data, indent=1, ensure_ascii=False))


def report_path(layout: Layout, kind: str, submission: Path) -> Path:
    """One report per submission, so sessions checking different files do not overwrite each other."""
    name = submission.name.removesuffix(".gz").removesuffix(".csv")
    return layout.report_dir / f"{kind}__{name}.json"
