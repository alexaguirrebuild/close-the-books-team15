"""Locate the data folder of one phase and keep every read inside it."""
from __future__ import annotations

import csv
import os
import unicodedata
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

PHASES = ("sample", "train", "live", "final")
DOCS_DIRNAME = "bankrec-docs-and-masters"
DATA_DIRNAME = "data"
ROOT_ENV_VAR = "BANKREC_DATA_ROOT"
EXCLUDED_FRAGMENTS = ("golden",)
STATEMENTS_HOLD = "bank_transactions.csv.gz"
INDEX_FILE_COLUMN = "file"


class DataRootNotFound(RuntimeError):
    """No candidate folder holds both the phase and the masters."""


class GoldenAccessError(RuntimeError):
    """Something tried to load the labels into the ingestion."""


class IndexUnreadable(ValueError):
    """An index.csv that cannot be decoded or has no column naming each file."""


@dataclass(frozen=True)
class Layout:
    root: Path
    phase: str
    phase_dir: Path
    masters_dir: Path
    masters_override_dir: Path | None
    starter_dir: Path
    warehouse_path: Path
    report_dir: Path

    def relative(self, path: Path | str) -> str:
        """Provenance path, stable across machines and in NFC form."""
        return nfc(os.path.relpath(path, self.root))


def nfc(text: str) -> str:
    """macOS may return NFD file names while the index files are NFC."""
    return unicodedata.normalize("NFC", text)


def read_file_index(path: Path) -> tuple[dict[str, dict[str, str]], int, int]:
    """Rows of an index.csv keyed by NFC file name, how many repeated a file already seen, and how many name no file."""
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if INDEX_FILE_COLUMN not in (reader.fieldnames or ()):
                raise IndexUnreadable(f"falta la columna '{INDEX_FILE_COLUMN}'; cabecera: {reader.fieldnames}")
            rows = list(reader)
    except (UnicodeDecodeError, csv.Error) as error:
        raise IndexUnreadable(f"no se puede leer: {error}") from error
    named = [row for row in rows if row[INDEX_FILE_COLUMN]]
    entries = {nfc(row[INDEX_FILE_COLUMN]): row for row in named}
    return entries, len(named) - len(entries), len(rows) - len(named)


def is_excluded(path: Path) -> bool:
    name = path.name.lower()
    return any(fragment in name for fragment in EXCLUDED_FRAGMENTS)


def assert_not_golden(path: Path) -> None:
    if is_excluded(path):
        raise GoldenAccessError(f"golden nunca entra en la ingesta: {path}")


def iter_phase_files(phase_dir: Path) -> Iterator[Path]:
    """Every data file of the phase, without hidden entries or labels."""
    for folder, subfolders, files in os.walk(phase_dir):
        subfolders[:] = sorted(name for name in subfolders if not name.startswith("."))
        for name in sorted(files):
            path = Path(folder) / name
            if not name.startswith(".") and not is_excluded(path):
                yield path


def _candidates(data_root: Path | None, env: Mapping[str, str], cwd: Path, home: Path) -> list[Path]:
    if data_root is not None:
        return [Path(data_root)]
    from_env = [Path(env[ROOT_ENV_VAR])] if env.get(ROOT_ENV_VAR) else []
    return [*from_env, cwd / "data", cwd, cwd.parent, home / "Downloads"]


def _phase_dir(base: Path, phase: str) -> Path | None:
    for folder in (base / phase, base / DOCS_DIRNAME / phase):
        if (folder / STATEMENTS_HOLD).is_file():
            return folder
    return None


def _first_existing(*folders: Path) -> Path | None:
    return next((folder for folder in folders if folder.is_dir()), None)


def _layout_at(candidate: Path, phase: str) -> Layout | None:
    base = candidate.absolute()
    phase_dir = _phase_dir(base, phase)
    masters = _first_existing(base / "masters", base / DOCS_DIRNAME / "masters")
    if phase_dir is None or masters is None:
        return None
    project = base.parent if base.name == DATA_DIRNAME else base  # El motor guarda las fases en data/ y out/ fuera.
    starter = _first_existing(base / "starter", base / DOCS_DIRNAME / "starter", project / DOCS_DIRNAME / "starter") or base / "starter"
    override = phase_dir / "masters_final"
    return Layout(
        root=base,
        phase=phase,
        phase_dir=phase_dir,
        masters_dir=masters,
        masters_override_dir=override if override.is_dir() else None,
        starter_dir=starter,
        warehouse_path=project / "cache" / "warehouse" / f"{phase}.duckdb",
        report_dir=project / "out" / phase,
    )


def resolve_layout(
    phase: str,
    data_root: Path | None = None,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
    home: Path | None = None,
) -> Layout:
    if phase not in PHASES:
        raise ValueError(f"fase desconocida: {phase}. Usa una de {', '.join(PHASES)}")
    candidates = _candidates(
        data_root,
        os.environ if env is None else env,
        Path.cwd() if cwd is None else cwd,
        Path.home() if home is None else home,
    )
    for base in candidates:
        layout = _layout_at(base, phase)
        if layout is not None:
            return layout
    searched = "\n  ".join(str(base) for base in candidates)
    raise DataRootNotFound(
        f"No encuentro la fase '{phase}' con maestros. Hace falta <raíz>/{phase}/{STATEMENTS_HOLD} y "
        f"<raíz>/masters o <raíz>/{DOCS_DIRNAME}/masters. Busqué en:\n  {searched}"
    )
