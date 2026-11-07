"""Smallest valid output: a printed number in the bank text plus the exact open amount, and a default row otherwise.

This is not the matcher. It exists so validation and scoring can be exercised end to end.
"""
from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path

import duckdb

from ..roots import Layout
from .writer import write_submission

MIN_TOKEN_LENGTH = 5
CANDIDATES_SQL = f"""
WITH tokens AS (
    SELECT bank_txn_id, amount_cents, currency, booking_date, pnorm(token) AS token
    FROM (
        SELECT bank_txn_id, amount_cents, currency, booking_date,
               unnest(regexp_split_to_array(upper(concat_ws(' ', text, ref1, ref2, remittance_info)), '[\\s|,;/]+')) AS token
        FROM bank_txn
    )
    WHERE length(token) >= {MIN_TOKEN_LENGTH}
)
SELECT DISTINCT t.bank_txn_id, i.item_id, i.open_cents, i.issue_date
FROM tokens t
JOIN item i
  ON i.printed_norm = t.token
 AND i.currency = t.currency
 AND i.open_cents = abs(t.amount_cents)
 AND i.side = CASE WHEN t.amount_cents > 0 THEN 'AR' ELSE 'AP' END
 AND left(i.item_id, 3) IN ('ARI', 'API')
ORDER BY t.bank_txn_id, i.issue_date, i.item_id
"""


def _choose_items(con: duckdb.DuckDBPyConnection) -> dict[str, tuple[str, int]]:
    """Each item is used once: same text and amount settle the oldest item first."""
    chosen: dict[str, tuple[str, int]] = {}
    used: set[str] = set()
    for txn, item, cents, _ in con.execute(CANDIDATES_SQL).fetchall():
        if txn not in chosen and item not in used:
            chosen[txn] = (item, cents)
            used.add(item)
    return chosen


def _rows(lines: Sequence[tuple[str, int]], chosen: dict[str, tuple[str, int]]) -> Iterator[tuple[str, str, str, str, str]]:
    for txn, cents in lines:
        if txn in chosen:
            item, item_cents = chosen[txn]
            yield txn, item, f"{item_cents / 100:.2f}", "", ""
        elif cents > 0:
            yield txn, "", "", "UNIDENTIFIED", "UNIDENTIFIED"
        else:
            yield txn, "", "", "BANK_FEES", ""


def write_baseline(layout: Layout, out: Path) -> tuple[int, int]:
    """Returns (rows written, bank lines matched to an item)."""
    con = duckdb.connect(str(layout.warehouse_path), read_only=True)
    try:
        chosen = _choose_items(con)
        lines = con.execute("SELECT bank_txn_id, amount_cents FROM bank_txn ORDER BY bank_txn_id").fetchall()
    finally:
        con.close()
    return write_submission(out, _rows(lines, chosen)), len(chosen)
