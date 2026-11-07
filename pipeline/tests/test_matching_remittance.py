"""Remittance advices to rows: tested on small in-memory warehouses, plus the real sample against its golden."""
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from bankcheck.matching.remittance_items import resolve_items
from bankcheck.matching.remittance_link import link_advices
from bankcheck.matching.remittance_rules import classify_rows

SCHEMA = """
CREATE TABLE remittance_advice (advice_id VARCHAR, entity VARCHAR, payer_name VARCHAR, format VARCHAR, layout VARCHAR,
    payment_ref VARCHAR, payment_date DATE, currency VARCHAR, control_amount DECIMAL(18,2), lines_sum DECIMAL(18,2),
    n_lines INTEGER, control_status VARCHAR, on_disk BOOLEAN);
CREATE TABLE remittance_line (advice_id VARCHAR, line_no INTEGER, document VARCHAR, document_norm VARCHAR, doc_date DATE,
    gross DECIMAL(18,2), deduction DECIMAL(18,2), reason VARCHAR, paid DECIMAL(18,2), is_repeated BOOLEAN);
CREATE TABLE bank_txn (bank_txn_id VARCHAR, entity VARCHAR, amount DECIMAL(18,2), currency VARCHAR, booking_date DATE,
    orig_amount DECIMAL(18,2), ref2 VARCHAR, end_to_end_id VARCHAR, text VARCHAR);
CREATE TABLE item (item_id VARCHAR, item_type VARCHAR, side VARCHAR, entity VARCHAR, printed_norm VARCHAR, party_id VARCHAR,
    issue_date DATE, currency VARCHAR, total_amount DECIMAL(18,2), open_amount DECIMAL(18,2), is_opening BOOLEAN, payment_terms VARCHAR);
CREATE TABLE customer (customer_id VARCHAR, entity VARCHAR, payment_terms VARCHAR);
CREATE TABLE payment_term (code VARCHAR, early_payment_discount_pct DECIMAL(5,2), discount_days INTEGER);
INSERT INTO payment_term VALUES ('T30', NULL, NULL), ('D2N30', 2.00, 10);
"""
PAY_DAY = date(2025, 7, 15)
ISSUED_IN_TIME = date(2025, 7, 10)


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect()
    connection.execute(SCHEMA)
    return connection


def add_advice(con, advice_id="A1", ref="250715123456", control="100.00", entity="ES01", pay_date=PAY_DAY, currency="EUR", status="ok", layout="csv"):
    con.execute(
        "INSERT INTO remittance_advice VALUES (?, ?, 'Pagador SA', 'csv', ?, ?, ?, ?, ?, ?, 1, ?, true)",
        [advice_id, entity, layout, ref, pay_date, currency, control, control, status],
    )


def add_bank(con, txn="BT-1", amount="100.00", text="TRANSFER", entity="ES01", booked=PAY_DAY, currency="EUR", orig=None, ref2=None, e2e=None):
    con.execute("INSERT INTO bank_txn VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [txn, entity, amount, currency, booked, orig, ref2, e2e, text])


def add_line(con, advice_id="A1", line_no=1, document="FV25-0000001", gross="100.00", paid="100.00", reason=None, deduction=None, doc_date=date(2025, 7, 1), repeated=False):
    norm = "".join(char for char in document.upper() if char.isalnum())
    con.execute(
        "INSERT INTO remittance_line VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [advice_id, line_no, document, norm, doc_date, gross, deduction, reason, paid, repeated],
    )


def add_item(con, item_id="ARI-2025-0000001", printed="FV250000001", total="100.00", open_amount=None, entity="ES01", item_type="ARI", currency="EUR", issued=date(2025, 7, 1), terms="T30", party="C1", opening=False):
    side = "AR" if item_type.startswith("AR") else "AP"
    con.execute(
        "INSERT INTO item VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [item_id, item_type, side, entity, printed, party, issued, currency, total, open_amount or total, opening, terms],
    )


def link_pairs(con) -> list[tuple[str, str, str]]:
    link_advices(con)
    return con.execute("SELECT advice_id, bank_txn_id, how FROM advice_link ORDER BY 1, 2").fetchall()


def resolved(con) -> list[tuple]:
    link_advices(con)
    resolve_items(con)
    return con.execute("SELECT bank_txn_id, line_no, item_id FROM advice_item ORDER BY 1, 2").fetchall()


def rows(con) -> list[tuple[str, Decimal, str | None]]:
    link_advices(con)
    resolve_items(con)
    classify_rows(con)
    return con.execute("SELECT item_id, amount, exception FROM advice_row ORDER BY line_no").fetchall()


class TestLink:
    def test_links_by_payment_ref_in_text(self, con):
        add_advice(con)
        add_bank(con, text="SEPA SVWZ+250715123456")
        assert link_pairs(con) == [("A1", "BT-1", "ref")]

    def test_links_by_payment_ref_in_ref2(self, con):
        add_advice(con)
        add_bank(con, ref2="250715123456")
        assert link_pairs(con) == [("A1", "BT-1", "ref")]

    def test_links_by_payment_ref_in_end_to_end_id(self, con):
        add_advice(con)
        add_bank(con, e2e="250715123456")
        assert link_pairs(con) == [("A1", "BT-1", "ref")]

    def test_links_by_amount_within_seven_days(self, con):
        add_advice(con)
        add_bank(con, booked=date(2025, 7, 22))
        assert link_pairs(con) == [("A1", "BT-1", "amount")]

    def test_does_not_link_by_amount_past_seven_days(self, con):
        add_advice(con)
        add_bank(con, booked=date(2025, 7, 23))
        assert link_pairs(con) == []

    def test_links_foreign_currency_advice_by_original_amount(self, con):
        add_advice(con, control="500.00", currency="USD")
        add_bank(con, amount="430.00", orig="500.00")
        assert link_pairs(con) == [("A1", "BT-1", "orig_amount")]

    def test_ignores_other_entity(self, con):
        add_advice(con)
        add_bank(con, text="250715123456", entity="FR01")
        assert link_pairs(con) == []

    def test_ignores_debits(self, con):
        add_advice(con)
        add_bank(con, amount="-100.00", text="250715123456")
        assert link_pairs(con) == []

    def test_ignores_too_short_reference(self, con):
        add_advice(con, ref="1234", control="999.00")
        add_bank(con, text="FACTURA 1234")
        assert link_pairs(con) == []

    def test_reference_in_a_foreign_line_loses_to_the_line_with_the_right_amount(self, con):
        add_advice(con)
        add_bank(con, txn="BT-1", amount="55.00", text="OTRO 250715123456")
        add_bank(con, txn="BT-2", amount="100.00", text="PAGO 250715123456")
        assert link_pairs(con) == [("A1", "BT-2", "ref")]

    def test_reference_in_a_foreign_line_does_not_hide_the_line_with_the_right_amount(self, con):
        add_advice(con)
        add_bank(con, txn="BT-1", amount="55.00", text="OTRO 250715123456")
        add_bank(con, txn="BT-2", amount="100.00", text="TRANSFERENCIA SIN REFERENCIA")
        assert link_pairs(con) == [("A1", "BT-2", "amount")]

    def test_reference_that_does_not_add_up_is_kept_when_nothing_else_fits(self, con):
        add_advice(con)
        add_bank(con, amount="55.00", text="OTRO 250715123456")
        assert link_pairs(con) == [("A1", "BT-1", "ref")]

    def test_a_bank_line_keeps_only_the_advice_that_adds_up(self, con):
        add_advice(con, advice_id="A1", ref="250715123456", control="100.00")
        add_advice(con, advice_id="A2", ref="99999", control="70.00")
        add_bank(con, text="250715123456 Y 99999")
        assert link_pairs(con) == [("A1", "BT-1", "ref")]

    def test_two_lines_of_same_amount_take_the_closest_date(self, con):
        add_advice(con)
        add_bank(con, txn="BT-1", booked=date(2025, 7, 20))
        add_bank(con, txn="BT-2", booked=date(2025, 7, 16))
        assert link_pairs(con) == [("A1", "BT-2", "amount")]

    def test_same_amount_and_date_prefers_the_line_naming_the_payer(self, con):
        add_advice(con)
        add_bank(con, txn="BT-1", text="TRANSFERENCIA OTRO CLIENTE")
        add_bank(con, txn="BT-2", text="TRANSFERENCIA PAGADOR SA")
        assert link_pairs(con) == [("A1", "BT-2", "amount")]

    def test_reference_far_from_the_payment_date_does_not_link(self, con):
        add_advice(con, control="999.00")
        add_bank(con, text="250715123456", booked=date(2025, 11, 1))
        assert link_pairs(con) == []

    def test_advice_with_payment_outside_the_phase_stays_unlinked(self, con):
        add_advice(con, pay_date=date(2025, 6, 20))
        add_bank(con, booked=date(2025, 7, 15))
        assert link_pairs(con) == []


class TestItems:
    def test_resolves_printed_number_within_the_entity(self, con):
        add_advice(con)
        add_bank(con, text="250715123456")
        add_line(con)
        add_item(con)
        add_item(con, item_id="ARI-2025-0000009", entity="FR01")
        assert resolved(con) == [("BT-1", 1, "ARI-2025-0000001")]

    def test_voucher_number_without_prefix_or_leading_zeros(self, con):
        add_advice(con, layout="xlsx_voucher")
        add_bank(con, text="250715123456")
        add_line(con, document="1")
        add_item(con, printed="FV250000001")
        assert resolved(con) == [("BT-1", 1, "ARI-2025-0000001")]

    def test_voucher_requires_same_amount_and_date(self, con):
        add_advice(con, layout="xlsx_voucher")
        add_bank(con, text="250715123456")
        add_line(con, document="1", gross="100.00")
        add_item(con, item_id="ARI-2025-0000002", printed="FV250000001", total="999.00")
        add_item(con, item_id="ARI-2025-0000003", printed="FV250000001", total="100.00", issued=date(2025, 1, 1))
        assert resolved(con) == []

    def test_voucher_with_two_candidates_is_not_guessed(self, con):
        add_advice(con, layout="xlsx_voucher")
        add_bank(con, text="250715123456")
        add_line(con, document="1")
        add_item(con, item_id="ARI-2025-0000002", printed="FV250000001")
        add_item(con, item_id="ARI-2025-0000003", printed="RE250000001")
        assert resolved(con) == []

    def test_negative_line_resolves_to_credit_note(self, con):
        add_advice(con)
        add_bank(con, text="250715123456")
        add_line(con, document="AB25-0000001", gross="-20.00", paid="-20.00")
        add_item(con, item_id="ARC-2025-0000001", item_type="ARC", printed="AB250000001", total="20.00")
        assert resolved(con) == [("BT-1", 1, "ARC-2025-0000001")]

    def test_supplier_items_are_never_used(self, con):
        add_advice(con)
        add_bank(con, text="250715123456")
        add_line(con)
        add_item(con, item_id="API-2025-0000001", item_type="API")
        assert resolved(con) == []

    def test_line_without_repeated_flag_is_kept(self, con):
        add_advice(con)
        add_bank(con, text="250715123456")
        add_line(con, repeated=None)
        add_item(con)
        assert resolved(con) == [("BT-1", 1, "ARI-2025-0000001")]

    def test_repeated_line_is_skipped(self, con):
        add_advice(con)
        add_bank(con, text="250715123456")
        add_line(con, line_no=1)
        add_line(con, line_no=2, repeated=True)
        add_item(con)
        assert resolved(con) == [("BT-1", 1, "ARI-2025-0000001")]

    def test_same_document_twice_gives_one_row(self, con):
        add_advice(con)
        add_bank(con, text="250715123456")
        add_line(con, line_no=1)
        add_line(con, line_no=2)
        add_item(con)
        assert len(rows(con)) == 1

    def test_unresolved_document_is_listed_and_not_invented(self, con):
        add_advice(con)
        add_bank(con, text="250715123456")
        add_line(con, document="NOEXISTE")
        assert resolved(con) == []
        assert con.execute("SELECT advice_id, line_no, document FROM advice_unresolved").fetchall() == [("A1", 1, "NOEXISTE")]


def linked_case(con, **line) -> None:
    add_advice(con)
    add_bank(con, text="250715123456")
    add_line(con, **line)


class TestRules:
    def test_full_payment_has_no_exception(self, con):
        linked_case(con)
        add_item(con)
        assert rows(con) == [("ARI-2025-0000001", Decimal("100.00"), None)]

    def test_credit_note_amount_is_positive_and_without_exception(self, con):
        linked_case(con, document="AB25-0000001", gross="-20.00", paid="-20.00")
        add_item(con, item_id="ARC-2025-0000001", item_type="ARC", printed="AB250000001", total="20.00")
        assert rows(con) == [("ARC-2025-0000001", Decimal("20.00"), None)]

    def test_credit_note_stays_empty_with_fx(self, con):
        linked_case(con, document="AB25-0000001", gross="-20.00", paid="-20.00")
        add_item(con, item_id="ARC-2025-0000001", item_type="ARC", printed="AB250000001", total="20.00", currency="USD")
        assert rows(con)[0][2] is None

    def test_stated_dispute_keeps_what_was_paid(self, con):
        linked_case(con, paid="90.00", deduction="10.00", reason="PRICE DIFF")
        add_item(con)
        assert rows(con) == [("ARI-2025-0000001", Decimal("90.00"), "SHORT_DISPUTE")]

    def test_withholding_reason_clears_the_item(self, con):
        linked_case(con, paid="90.00", deduction="10.00", reason="RET")
        add_item(con)
        assert rows(con) == [("ARI-2025-0000001", Decimal("100.00"), "SHORT_WITHHOLDING")]

    @pytest.mark.parametrize("paid", ["98.75", "98.50", "90.00"])
    def test_withholding_rate_without_reason_clears_the_item(self, con, paid):
        linked_case(con, paid=paid)
        add_item(con)
        assert rows(con) == [("ARI-2025-0000001", Decimal("100.00"), "SHORT_WITHHOLDING")]

    def test_discount_within_terms_clears_the_item(self, con):
        linked_case(con, paid="98.00", deduction="2.00", reason="DISCOUNT")
        add_item(con, terms="D2N30", issued=ISSUED_IN_TIME)
        assert rows(con) == [("ARI-2025-0000001", Decimal("100.00"), "SHORT_DISCOUNT")]

    def test_discount_amount_without_reason_is_recognised(self, con):
        linked_case(con, paid="98.00")
        add_item(con, terms="D2N30", issued=ISSUED_IN_TIME)
        assert rows(con)[0][2] == "SHORT_DISCOUNT"

    def test_discount_on_the_last_day_of_grace(self, con):
        linked_case(con, paid="98.00", reason="DISCOUNT")
        add_item(con, terms="D2N30", issued=PAY_DAY.replace(day=2))
        assert rows(con)[0][2] == "SHORT_DISCOUNT"

    def test_discount_one_day_after_grace_is_partial_with_what_was_paid(self, con):
        linked_case(con, paid="98.00", reason="DISCOUNT")
        add_item(con, terms="D2N30", issued=PAY_DAY.replace(day=1))
        assert rows(con) == [("ARI-2025-0000001", Decimal("98.00"), "PARTIAL")]

    def test_terms_come_from_the_customer_when_the_item_has_none(self, con):
        linked_case(con, paid="98.00", reason="DISCOUNT")
        add_item(con, terms=None, opening=True, issued=ISSUED_IN_TIME)
        con.execute("INSERT INTO customer VALUES ('C1', 'ES01', 'D2N30')")
        assert rows(con)[0][2] == "SHORT_DISCOUNT"

    def test_unexplained_gap_is_partial(self, con):
        linked_case(con, paid="40.00")
        add_item(con)
        assert rows(con) == [("ARI-2025-0000001", Decimal("40.00"), "PARTIAL")]

    def test_paying_the_remainder_after_a_partial_has_no_exception(self, con):
        linked_case(con, gross="100.00", paid="60.00")
        add_item(con, total="100.00", open_amount="60.00", opening=True)
        assert rows(con) == [("ARI-2025-0000001", Decimal("60.00"), None)]

    def test_line_without_gross_compares_paid_with_the_balance(self, con):
        linked_case(con, gross=None, paid="100.00")
        add_item(con)
        assert rows(con) == [("ARI-2025-0000001", Decimal("100.00"), None)]

    def test_advice_paying_in_full_what_it_lists_but_less_than_the_balance_is_partial(self, con):
        linked_case(con, gross="40.00", paid="40.00")
        add_item(con, total="100.00")
        assert rows(con) == [("ARI-2025-0000001", Decimal("40.00"), "PARTIAL")]

    def test_paying_more_than_the_balance_clears_it_as_overpayment(self, con):
        linked_case(con, gross="120.00", paid="120.00")
        add_item(con, total="100.00")
        assert rows(con) == [("ARI-2025-0000001", Decimal("100.00"), "OVERPAYMENT")]

    def test_fx_keeps_overpayment(self, con):
        linked_case(con, gross="120.00", paid="120.00")
        add_item(con, total="100.00", currency="USD")
        assert rows(con)[0][2] == "OVERPAYMENT"

    def test_fx_replaces_empty_exception(self, con):
        linked_case(con)
        add_item(con, currency="USD")
        assert rows(con) == [("ARI-2025-0000001", Decimal("100.00"), "FX_DIFFERENCE")]

    def test_fx_replaces_discount(self, con):
        linked_case(con, paid="98.00", reason="DISCOUNT")
        add_item(con, currency="USD", terms="D2N30", issued=ISSUED_IN_TIME)
        assert rows(con) == [("ARI-2025-0000001", Decimal("100.00"), "FX_DIFFERENCE")]

    def test_fx_keeps_dispute(self, con):
        linked_case(con, paid="90.00", reason="DAMAGED GOODS")
        add_item(con, currency="USD")
        assert rows(con) == [("ARI-2025-0000001", Decimal("90.00"), "SHORT_DISPUTE")]

    def test_fx_keeps_partial(self, con):
        linked_case(con, paid="40.00")
        add_item(con, currency="USD")
        assert rows(con)[0][2] == "PARTIAL"

    def test_unknown_reason_is_treated_as_dispute_and_listed(self, con):
        linked_case(con, paid="90.00", reason="NEW REASON")
        add_item(con)
        assert rows(con)[0][2] == "SHORT_DISPUTE"
        assert con.execute("SELECT reason, n FROM advice_unknown_reason").fetchall() == [("NEW REASON", 1)]


WAREHOUSE = Path(__file__).resolve().parents[2] / "cache" / "warehouse" / "sample.duckdb"
GOLDEN = Path(__file__).resolve().parents[2] / "bankrec-docs-and-masters" / "sample" / "golden_allocations.csv.gz"


@pytest.mark.data
def test_sample_rows_match_the_golden_on_linked_lines():
    if not WAREHOUSE.is_file() or not GOLDEN.is_file():
        pytest.skip("hace falta la ingesta de sample")
    con = duckdb.connect()
    con.execute(f"ATTACH '{WAREHOUSE}' AS w (READ_ONLY); USE w")
    link_advices(con)
    resolve_items(con)
    classify_rows(con)
    con.execute(f"CREATE TEMP TABLE g AS SELECT bank_txn_id, item_id, TRY_CAST(amount AS DECIMAL(18,2)) amount, exception FROM read_csv('{GOLDEN}', all_varchar=true)")
    submitted, golden, matched = con.execute(
        """SELECT (SELECT count(*) FROM advice_row),
                  (SELECT count(*) FROM g WHERE item_id IS NOT NULL AND bank_txn_id IN (SELECT bank_txn_id FROM advice_row)),
                  (SELECT count(*) FROM advice_row r JOIN g USING (bank_txn_id, item_id)
                    WHERE abs(r.amount - g.amount) <= greatest(0.01, 0.005 * g.amount)
                      AND coalesce(r.exception, '') = coalesce(g.exception, ''))"""
    ).fetchone()
    assert submitted > 7000
    assert golden == submitted
    assert matched / submitted >= 0.995
