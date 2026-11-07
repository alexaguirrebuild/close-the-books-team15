"""Tie each advice to the one bank line that carries its cash.

An advice is found by its payment reference; failing that, by its announced amount around the payment date.
A reference can also appear in an unrelated line, so a candidate whose amount adds up always wins, and a reference
that does not add up does not stop the search by amount. An advice that loses its line to a better one is not tried
again on its next candidate; the report counts any advice left unlinked inside the phase.
"""
from __future__ import annotations

import duckdb

MIN_REF_LENGTH = 5
AMOUNT_TOLERANCE = 0.01
AMOUNT_WINDOW_DAYS = 7
REF_WINDOW_DAYS = 45
PAYER_NAME_PREFIX = 8

LINK_SQL = f"""
CREATE OR REPLACE TEMP TABLE advice_link AS
WITH usable AS (
    SELECT advice_id, entity, payer_name, payment_ref, payment_date, control_amount
    FROM remittance_advice
    WHERE on_disk AND n_lines > 0
),
credits AS (
    SELECT bank_txn_id, entity, amount, orig_amount, booking_date, ref2, end_to_end_id, text
    FROM bank_txn
    WHERE amount > 0
),
by_ref AS (
    SELECT u.advice_id, c.bank_txn_id, 'ref' AS how, 1 AS how_rank
    FROM usable u
    JOIN credits c ON c.entity = u.entity
    WHERE length(u.payment_ref) >= {MIN_REF_LENGTH}
      AND (u.payment_date IS NULL OR abs(date_diff('day', c.booking_date, u.payment_date)) <= {REF_WINDOW_DAYS})
      AND (c.ref2 = u.payment_ref OR c.end_to_end_id = u.payment_ref OR contains(c.text, u.payment_ref))
),
ref_adds_up AS (
    SELECT k.advice_id
    FROM by_ref k
    JOIN usable u USING (advice_id)
    JOIN credits c USING (bank_txn_id)
    WHERE abs(c.amount - u.control_amount) <= {AMOUNT_TOLERANCE} OR abs(c.orig_amount - u.control_amount) <= {AMOUNT_TOLERANCE}
),
without_ref AS (
    SELECT * FROM usable WHERE advice_id NOT IN (SELECT advice_id FROM ref_adds_up)
),
by_amount AS (
    SELECT u.advice_id, c.bank_txn_id, 'amount' AS how, 2 AS how_rank
    FROM without_ref u
    JOIN credits c ON c.entity = u.entity
    WHERE abs(c.amount - u.control_amount) <= {AMOUNT_TOLERANCE}
      AND abs(date_diff('day', c.booking_date, u.payment_date)) <= {AMOUNT_WINDOW_DAYS}
),
without_amount AS (
    SELECT * FROM without_ref WHERE advice_id NOT IN (SELECT advice_id FROM by_amount)
),
by_orig_amount AS (
    SELECT u.advice_id, c.bank_txn_id, 'orig_amount' AS how, 3 AS how_rank
    FROM without_amount u
    JOIN credits c ON c.entity = u.entity
    WHERE abs(c.orig_amount - u.control_amount) <= {AMOUNT_TOLERANCE}
      AND abs(date_diff('day', c.booking_date, u.payment_date)) <= {AMOUNT_WINDOW_DAYS}
),
candidates AS (
    SELECT k.advice_id, k.bank_txn_id, k.how, k.how_rank,
           coalesce(abs(c.amount - u.control_amount) <= {AMOUNT_TOLERANCE}, false)
             OR coalesce(abs(c.orig_amount - u.control_amount) <= {AMOUNT_TOLERANCE}, false) AS adds_up,
           abs(date_diff('day', c.booking_date, u.payment_date)) AS days_off,
           contains(upper(c.text), upper(left(u.payer_name, {PAYER_NAME_PREFIX}))) AS names_payer
    FROM (SELECT * FROM by_ref UNION ALL SELECT * FROM by_amount UNION ALL SELECT * FROM by_orig_amount) k
    JOIN usable u USING (advice_id)
    JOIN credits c USING (bank_txn_id)
),
best_per_advice AS (
    SELECT * FROM candidates
    QUALIFY row_number() OVER (
        PARTITION BY advice_id
        ORDER BY adds_up DESC, how_rank, days_off NULLS LAST, names_payer DESC NULLS LAST, bank_txn_id
    ) = 1
)
SELECT advice_id, bank_txn_id, how, adds_up
FROM best_per_advice
QUALIFY row_number() OVER (
    PARTITION BY bank_txn_id
    ORDER BY adds_up DESC, how_rank, days_off NULLS LAST, advice_id
) = 1
"""


def link_advices(con: duckdb.DuckDBPyConnection) -> None:
    """Creates temp table advice_link(advice_id, bank_txn_id, how, adds_up): one to one."""
    con.execute(LINK_SQL)
