"""POLICIES §2 and §3 applied to advice lines: which exception each row carries and how much of the item it clears."""
from __future__ import annotations

import duckdb

SOURCE = "m2_remittance"
CONFIDENCE_ADDS_UP = 1.0
CONFIDENCE_DOUBTFUL = 0.8
CONTROL_ADDS_UP = ("ok", "ok_without_repeats", "no_control")
GRACE_DAYS = 3
WITHHOLDING_RATES = (0.0125, 0.015, 0.10)
GAP_NONE = 0.005
GAP_TOLERANCE = 0.011
AMOUNT_TOLERANCE = 0.01
REASON_WITHHOLDING = "RET"
REASON_DISCOUNT = "DISCOUNT"
KNOWN_DISPUTE_REASONS = ("DAMAGED GOODS", "PRICE DIFF", "COOP MKT", "SHORT DELIVERY", "PENAL. RETRASO", "LOGISTIC FEE")


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


WITHHOLDING_GAP = " OR ".join(f"abs(gap - round({rate} * total_amount, 2)) <= {GAP_TOLERANCE}" for rate in WITHHOLDING_RATES)

RULES_SQL = f"""
CREATE OR REPLACE TEMP TABLE advice_row AS
WITH with_terms AS (
    SELECT i.*, b.booking_date, b.currency AS bank_currency, t.early_payment_discount_pct AS discount_pct, t.discount_days
    FROM advice_item i
    JOIN bank_txn b USING (bank_txn_id)
    LEFT JOIN customer c ON c.customer_id = i.party_id AND c.entity = i.entity
    LEFT JOIN payment_term t ON t.code = coalesce(i.payment_terms, c.payment_terms)
),
measured AS (
    SELECT *,
           abs(coalesce(gross, paid)) - abs(paid) AS gap,
           coalesce(booking_date <= issue_date + CAST(discount_days + {GRACE_DAYS} AS INTEGER), false) AS in_discount_time,
           item_currency <> bank_currency AS is_fx
    FROM with_terms
    WHERE paid IS NOT NULL
),
classified AS (
    SELECT *,
        CASE
            WHEN paid < 0 OR item_type = 'ARC' THEN 'CREDIT_NOTE'
            WHEN abs(paid - open_amount) <= {AMOUNT_TOLERANCE} THEN 'FULL'
            WHEN paid > open_amount THEN 'OVERPAYMENT'
            WHEN gap <= {GAP_NONE} THEN 'PARTIAL'
            WHEN reason = '{REASON_WITHHOLDING}' THEN 'SHORT_WITHHOLDING'
            WHEN reason IS NOT NULL AND reason <> '{REASON_DISCOUNT}' THEN 'SHORT_DISPUTE'
            WHEN reason = '{REASON_DISCOUNT}'
              OR (discount_pct > 0 AND abs(gap - round(discount_pct / 100.0 * total_amount, 2)) <= {GAP_TOLERANCE})
                THEN CASE WHEN in_discount_time THEN 'SHORT_DISCOUNT' ELSE 'PARTIAL' END
            WHEN {WITHHOLDING_GAP} THEN 'SHORT_WITHHOLDING'
            ELSE 'PARTIAL'
        END AS outcome
    FROM measured
)
SELECT bank_txn_id, item_id,
    CAST(CASE
        WHEN outcome = 'CREDIT_NOTE' THEN abs(paid)
        WHEN outcome IN ('PARTIAL', 'SHORT_DISPUTE') THEN paid
        ELSE open_amount
    END AS DECIMAL(18,2)) AS amount,
    CAST(NULL AS VARCHAR) AS category,
    CASE
        WHEN outcome = 'CREDIT_NOTE' THEN NULL
        WHEN outcome IN ('PARTIAL', 'SHORT_DISPUTE', 'OVERPAYMENT') THEN outcome
        WHEN is_fx THEN 'FX_DIFFERENCE'
        WHEN outcome = 'FULL' THEN NULL
        ELSE outcome
    END AS exception,
    '{SOURCE}' AS source,
    CASE WHEN adds_up AND control_status IN ({_quoted(CONTROL_ADDS_UP)}) THEN {CONFIDENCE_ADDS_UP} ELSE {CONFIDENCE_DOUBTFUL} END AS confidence,
    advice_id, line_no
FROM classified
QUALIFY row_number() OVER (PARTITION BY bank_txn_id, item_id ORDER BY line_no) = 1
"""

UNKNOWN_REASONS_SQL = f"""
CREATE OR REPLACE TEMP TABLE advice_unknown_reason AS
SELECT reason, count(*) AS n
FROM advice_item
WHERE reason IS NOT NULL
  AND reason NOT IN ({_quoted((*KNOWN_DISPUTE_REASONS, REASON_WITHHOLDING, REASON_DISCOUNT))})
GROUP BY reason
ORDER BY n DESC, reason
"""


def classify_rows(con: duckdb.DuckDBPyConnection) -> None:
    """Needs advice_item. Creates temp tables advice_row and advice_unknown_reason."""
    con.execute(RULES_SQL)
    con.execute(UNKNOWN_REASONS_SQL)
