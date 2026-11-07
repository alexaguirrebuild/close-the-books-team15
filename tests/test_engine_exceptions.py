"""Regresiones contables de POLICIES §2-§3 y §5.5, sin datos del reto."""
import datetime as dt
import unittest
from collections import defaultdict
from types import SimpleNamespace

from bankrec.data import Item
from bankrec.engine import Engine


class ExceptionsTest(unittest.TestCase):
    def engine(self, terms=None, country="ES"):
        eng = Engine.__new__(Engine)
        eng.ph = SimpleNamespace(terms=terms or {}, party_country={"customer": country},
                                 fx=SimpleNamespace(conv=lambda amount, *args: amount))
        eng.items = {}
        eng.rows = []
        eng.settled_by = defaultdict(list)
        eng.iban_learned = {}
        return eng

    def invoice(self, eng, iid, total, terms=None):
        it = Item(id=iid, kind="ARI", side="AR", entity="entity", number=iid, party="customer",
                  ccy="EUR", total=total, open=total, issue=dt.date(2025, 1, 1), terms=terms)
        eng.items[iid] = it
        return it

    def txn(self, amount, date=dt.date(2025, 1, 5), text=""):
        return SimpleNamespace(id="bank", entity="entity", amount=amount, ccy="EUR", date=date,
                               cp_iban="", orig_ccy=None, up=text)

    def discounted_batch(self, date):
        eng = self.engine({"discount": {"early_payment_discount_pct": "2", "discount_days": "10"}})
        self.invoice(eng, "discounted", 100000, "discount")
        self.invoice(eng, "normal", 50000)
        eng.settle(self.txn(148000, date), ["discounted", "normal"], "test")
        return eng

    def test_only_discounted_invoice_clears_in_full_within_grace(self):
        eng = self.discounted_batch(dt.date(2025, 1, 14))
        self.assertEqual([(r.item, r.amount, r.exception) for r in eng.rows],
                         [("discounted", 100000, "SHORT_DISCOUNT"), ("normal", 50000, "")])
        self.assertEqual([it.open for it in eng.items.values()], [0, 0])

    def test_late_discount_stays_open_on_the_correct_invoice(self):
        eng = self.discounted_batch(dt.date(2025, 1, 15))
        self.assertEqual([(r.item, r.amount, r.exception) for r in eng.rows],
                         [("discounted", 98000, "PARTIAL"), ("normal", 50000, "")])
        self.assertEqual(eng.items["discounted"].open, 2000)

    def test_withholding_can_clear_multiple_invoices(self):
        eng = self.engine(country="MX")
        self.invoice(eng, "first", 100000)
        self.invoice(eng, "second", 200000)
        eng.settle(self.txn(270000), ["first", "second"], "test")
        self.assertEqual([(r.amount, r.exception) for r in eng.rows],
                         [(100000, "SHORT_WITHHOLDING"), (200000, "SHORT_WITHHOLDING")])

    def test_unexplained_shortfall_remains_partial(self):
        eng = self.engine()
        self.invoice(eng, "first", 100000)
        self.invoice(eng, "second", 50000)
        eng.settle(self.txn(145321), ["first", "second"], "test")
        self.assertEqual([(r.amount, r.exception) for r in eng.rows], [(100000, ""), (45321, "PARTIAL")])

    def test_duplicate_does_not_reduce_closed_balance(self):
        eng = self.engine()
        it = self.invoice(eng, "invoice", 100000)
        it.open = 0
        eng.emit(self.txn(100000), it, 100000, "DUPLICATE_PAYMENT", "test")
        self.assertEqual(it.open, 0)
        self.assertEqual(eng.rows[0].exception, "DUPLICATE_PAYMENT")

    def test_partial_fx_uses_original_bank_amount(self):
        eng = self.engine()
        it = self.invoice(eng, "invoice", 10000)
        it.ccy = "USD"
        eng.ph.fx.conv = lambda amount, *args: amount * 1.1
        bank = self.txn(4500)
        bank.orig_ccy, bank.orig_amount = "USD", 5000
        eng.settle(bank, [it.id], "test")
        self.assertEqual([(r.amount, r.exception) for r in eng.rows], [(5000, "PARTIAL")])
        self.assertEqual(it.open, 5000)

    def test_only_open_credit_balance_is_netted(self):
        eng = self.engine()
        self.invoice(eng, "invoice", 100000)
        credit = self.invoice(eng, "credit", 10000)
        credit.kind, credit.open = "ARC", 5000
        eng.settle(self.txn(90000), ["invoice", "credit"], "test")
        self.assertEqual({r.item: (r.amount, r.exception) for r in eng.rows},
                         {"invoice": (95000, "PARTIAL"), "credit": (5000, "")})
        self.assertEqual(eng.items["invoice"].open, 5000)

    def test_credit_refund_keeps_empty_exception_across_currencies(self):
        eng = self.engine()
        credit = self.invoice(eng, "credit", 10000)
        credit.kind, credit.side, credit.ccy = "APC", "AP", "USD"
        bank = self.txn(9000)
        bank.orig_ccy, bank.orig_amount = "USD", 10000
        eng.settle(bank, [credit.id], "test")
        self.assertEqual([(r.amount, r.exception) for r in eng.rows], [(10000, "")])

    def test_explicit_international_charges_respect_both_limits(self):
        eng = self.engine(country="CH")
        it = self.invoice(eng, "invoice", 200000)
        bank = self.txn(197500, text="/CHGS/EUR25,00")
        self.assertEqual(eng.explain_short(it, bank, 2500, 197500), "SHORT_BANK_CHARGES")
        self.assertIsNone(eng.explain_short(it, bank, 5000, 195000))  # more than 2 %
        self.assertIsNone(eng.explain_short(it, bank, 6500, 193500))  # more than EUR 60
        self.assertIsNone(eng.explain_short(it, self.txn(197500), 2500, 197500))

    def test_reference_cannot_settle_future_invoice(self):
        eng = self.engine()
        it = self.invoice(eng, "future", 100000)
        it.issue = dt.date(2025, 2, 1)
        eng.ph.by_num = {"FUTURE": {it.id}}
        eng.resolve_parties = lambda t: ([], "")
        bank = self.txn(100000)
        bank.tokens = {"FUTURE"}
        self.assertFalse(eng.m_refs(bank))
        self.assertEqual(it.open, 100000)
        self.assertEqual(eng.rows, [])

    def test_return_uses_cited_batch_before_latest_equal_instalment(self):
        eng = self.engine()
        old = self.invoice(eng, "old", 10000)
        new = self.invoice(eng, "new", 10000)
        old.open = new.open = 0
        records = [dict(batch_id=bid, amount="100.00", collection_date=date,
                        remittance_info=it.id, mandate_id="VTM-ES-1")
                   for bid, date, it in [("11111111111", "2025-01-02", old),
                                         ("22222222222", "2025-01-04", new)]]
        eng.dd_by_mandate = {"VTM-ES-1": records}
        eng.dd_batches = {r["batch_id"]: [r] for r in records}
        eng.item_by_number = lambda number, *args, **kw: eng.items[number]
        bank = self.txn(-10000, text="DEVOLUCION RECIBO REM 11111111111 MANDATO VTM-ES-1")
        self.assertTrue(eng.m_dd_return(bank))
        self.assertEqual([(r.item, r.exception) for r in eng.rows], [("old", "RETURNED")])
        self.assertEqual((old.open, new.open), (10000, 0))

    def test_return_cannot_choose_future_collection(self):
        eng = self.engine()
        record = dict(batch_id="11111111111", amount="100.00", collection_date="2025-01-06",
                      remittance_info="future", mandate_id="VTM-ES-1")
        eng.dd_by_mandate = {"VTM-ES-1": [record]}
        eng.dd_batches = {"11111111111": [record]}
        bank = self.txn(-10000, text="DEVOLUCION RECIBO REM 11111111111 MANDATO VTM-ES-1")
        self.assertFalse(eng.m_dd_return(bank))
        self.assertEqual(eng.rows, [])


if __name__ == "__main__":
    unittest.main()
