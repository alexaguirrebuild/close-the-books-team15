"""Find the open item behind each line of a linked advice.

Most lines print our document number. From September 2025 a big customer prints it without prefix or leading
zeros next to its own voucher number, so a line that does not resolve directly is looked up by its digits,
amount and date. A digits lookup with more than one candidate stays unresolved: it is never guessed.
"""
from __future__ import annotations

import duckdb

LINES_SQL = """
CREATE OR REPLACE TEMP TABLE advice_line AS
SELECT k.bank_txn_id, k.advice_id, k.adds_up, a.entity, a.control_status,
       l.line_no, l.document, l.document_norm, l.doc_date, l.gross, l.deduction, l.reason, l.paid,
       ltrim(regexp_replace(l.document_norm, '[^0-9]', '', 'g'), '0') AS document_digits
FROM advice_link k
JOIN remittance_advice a USING (advice_id)
JOIN remittance_line l USING (advice_id)
WHERE l.is_repeated IS NOT TRUE
"""

RECEIVABLES_SQL = """
CREATE OR REPLACE TEMP TABLE receivable AS
SELECT item_id, item_type, entity, printed_norm, party_id, issue_date, currency, total_amount, open_amount, payment_terms,
       regexp_replace(printed_norm, '[^0-9]', '', 'g') AS printed_digits
FROM item
WHERE side = 'AR'
"""

ITEM_COLUMNS = """r.item_id, r.item_type, r.party_id, r.issue_date, r.currency AS item_currency,
       r.total_amount, r.open_amount, r.payment_terms"""

ITEMS_SQL = f"""
CREATE OR REPLACE TEMP TABLE advice_item AS
WITH printed AS (
    SELECT l.*, {ITEM_COLUMNS}, 'printed' AS item_how
    FROM advice_line l
    JOIN receivable r ON r.entity = l.entity AND r.printed_norm = l.document_norm
    QUALIFY row_number() OVER (
        PARTITION BY l.bank_txn_id, l.line_no
        ORDER BY (r.total_amount = abs(l.gross)) DESC NULLS LAST, r.item_id
    ) = 1
),
not_printed AS (
    SELECT l.* FROM advice_line l
    LEFT JOIN printed p ON p.bank_txn_id = l.bank_txn_id AND p.line_no = l.line_no
    WHERE p.item_id IS NULL AND l.document_digits <> ''
),
by_digits AS (
    SELECT l.*, {ITEM_COLUMNS}, 'digits' AS item_how
    FROM not_printed l
    JOIN receivable r
      ON r.entity = l.entity AND r.total_amount = abs(l.gross) AND r.issue_date = l.doc_date
     AND ends_with(r.printed_digits, l.document_digits)
    QUALIFY count(*) OVER (PARTITION BY l.bank_txn_id, l.line_no) = 1
)
SELECT * FROM printed UNION ALL SELECT * FROM by_digits
"""

UNRESOLVED_SQL = """
CREATE OR REPLACE TEMP TABLE advice_unresolved AS
SELECT l.advice_id, l.line_no, l.document
FROM advice_line l
LEFT JOIN advice_item i ON i.bank_txn_id = l.bank_txn_id AND i.line_no = l.line_no
WHERE i.item_id IS NULL
"""


def resolve_items(con: duckdb.DuckDBPyConnection) -> None:
    """Needs advice_link. Creates temp tables advice_line, receivable, advice_item and advice_unresolved."""
    for statement in (LINES_SQL, RECEIVABLES_SQL, ITEMS_SQL, UNRESOLVED_SQL):
        con.execute(statement)
