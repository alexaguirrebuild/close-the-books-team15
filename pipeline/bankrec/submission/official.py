"""The organisers' scorer, imported as published: the same code runs on the server."""
from __future__ import annotations

import csv
import hashlib
from pathlib import Path
from types import ModuleType

from ..roots import Layout

SCORER_FILE = "bankrec_score.py"
SCORER_SHA256 = "1cc74ec1cda3aac6351bf8353cbc38adc81a7b126f9e8c1d75cc5e08bc45fd81"
SIZE_LIMITS = ("MAX_UNCOMPRESSED", "MAX_DECOMPRESS", "MAX_ROWS")
LIFTED_LIMIT = 10**12
EXCEPTIONS = (
    "PARTIAL", "OVERPAYMENT", "SHORT_DISCOUNT", "SHORT_BANK_CHARGES", "SHORT_WITHHOLDING",
    "SHORT_DISPUTE", "DUPLICATE_PAYMENT", "UNIDENTIFIED", "RETURNED", "FX_DIFFERENCE",
)
SUBMISSION_HEADER = ("bank_txn_id", "item_id", "amount", "category", "exception")


class ScorerChanged(RuntimeError):
    """The scorer on disk is not the published one we checked, so no verdict from it can be trusted."""


def load_official_scorer(layout: Layout) -> ModuleType:
    """A fresh module object each time, so changing its limits for TRAIN never leaks into a LIVE or FINAL check.

    Runs the very bytes whose fingerprint was checked, and writes no bytecode into the organisers' folder.
    """
    path = layout.starter_dir / SCORER_FILE
    if not path.is_file():
        raise FileNotFoundError(f"no encuentro el scorer oficial en {path}")
    source = path.read_bytes()
    digest = hashlib.sha256(source).hexdigest()
    if digest != SCORER_SHA256:
        raise ScorerChanged(
            f"{path} tiene SHA-256 {digest} y se esperaba {SCORER_SHA256}. "
            "Si el organizador ha publicado otra versión, revísala y actualiza SCORER_SHA256."
        )
    module = ModuleType("bankrec_score_official")
    module.__file__ = str(path)
    exec(compile(source, str(path), "exec"), module.__dict__)  # noqa: S102 - pinned by SHA-256 just above.
    return module


def lift_limits(scorer: ModuleType) -> ModuleType:
    """Only for phases never uploaded: real TRAIN output is far above the upload limits. The scoring code is untouched."""
    for name in SIZE_LIMITS:
        setattr(scorer, name, LIFTED_LIMIT)
    return scorer


def read_categories(masters_dir: Path) -> list[str]:
    with (masters_dir / "categories.csv").open(encoding="utf-8", newline="") as handle:
        return [row["category"] for row in csv.DictReader(handle)]
