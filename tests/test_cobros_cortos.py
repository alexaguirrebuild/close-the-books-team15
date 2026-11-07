from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

from bankrec import cobros_cortos


def test_cobros_cortos_overpayment_single_invoice():
    it = SimpleNamespace(
        id="ARI-1",
        number="INV-100",
        party="C001",
        entity="ES01",
        kind="ARI",
        side="AR",
        ccy="EUR",
        total=100_00,
        open=100_00,
        issue=dt.date(2025, 1, 10),
        terms="",
    )
    settled = []

    class DummyEngine:
        ph = SimpleNamespace(party_country={"C001": "ES"}, terms={})

        def _party_amount(self, t, party, how):
            return False

        def open_items_of(self, party, t, kinds):
            return [it]

        def sign(self, i, t):
            return 1

        def settle(self, t, cands, rule, ev="", party=None):
            settled.append((t.id, cands, rule, party))
            return True

    eng = DummyEngine()
    cobros_cortos.prepare(eng)
    t = SimpleNamespace(id="TX-1", amount=105_00, ccy="EUR", date=dt.date(2025, 1, 20))
    assert eng._party_amount(t, "C001", "iban") is True
    assert settled == [("TX-1", ["ARI-1"], "party_overpay_iban", "C001")]


def test_cobros_cortos_ignores_over_10_percent():
    it = SimpleNamespace(
        id="ARI-1",
        number="INV-100",
        party="C001",
        entity="ES01",
        kind="ARI",
        side="AR",
        ccy="EUR",
        total=100_00,
        open=100_00,
        issue=dt.date(2025, 1, 10),
        terms="",
    )

    class DummyEngine:
        ph = SimpleNamespace(party_country={"C001": "ES"}, terms={})

        def _party_amount(self, t, party, how):
            return False

        def open_items_of(self, party, t, kinds):
            return [it]

        def sign(self, i, t):
            return 1

        def settle(self, *args, **kwargs):
            raise AssertionError("No debe forzar sobrepago > 10%")

    eng = DummyEngine()
    cobros_cortos.prepare(eng)
    t = SimpleNamespace(id="TX-2", amount=115_00, ccy="EUR", date=dt.date(2025, 1, 20))
    assert eng._party_amount(t, "C001", "iban") is False
