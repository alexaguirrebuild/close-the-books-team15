"""SHORT_BANK_CHARGES (POLICIES §3): solo con banco intermediario, nunca en un pago nacional."""
import datetime as dt
from types import SimpleNamespace

import pytest

from bankrec.data import Item
from bankrec.engine import Engine

TOTAL = 100000
SMALL_GAP = 1500


def engine(country, entity_country):
    eng = Engine.__new__(Engine)
    eng.ph = SimpleNamespace(
        party_country={"C1": country},
        entity_country=entity_country,
        fx=SimpleNamespace(conv=lambda cents, _from, _to, _day: cents),
    )
    eng.discount_ok = lambda *_: None
    return eng


def invoice():
    return Item(id="A", kind="ARI", side="AR", entity="US01", number="A", party="C1", ccy="USD", total=TOTAL, open=TOTAL)


def movement(text="WIRE IN /ORG/PAGADOR", orig_ccy=None):
    return SimpleNamespace(up=text, orig_ccy=orig_ccy, date=dt.date(2025, 6, 10))


def explain(eng, t, gap=SMALL_GAP):
    return eng.explain_short(invoice(), t, gap, TOTAL - gap)


def test_cliente_de_otro_pais_fuera_de_sepa():
    assert explain(engine("CA", {"US01": "US"}), movement()) == "SHORT_BANK_CHARGES"


@pytest.mark.parametrize("country", ["US", "AU", "CN"])
def test_un_pago_nacional_no_tiene_banco_intermediario(country):
    assert explain(engine(country, {"US01": country}), movement("ACH CREDIT PAGADOR")) is None


def test_sociedad_sin_pais_conocido_no_se_trata_como_nacional():
    assert explain(engine("CA", {}), movement()) == "SHORT_BANK_CHARGES"


def test_chgs_en_el_texto_es_evidencia_aunque_el_pais_este_en_sepa():
    assert explain(engine("CH", {"US01": "IT"}), movement("BONIFICO DA PAGADOR /CHGS/EUR15,00")) == "SHORT_BANK_CHARGES"


def test_pais_sepa_sin_chgs_no_es_comision():
    assert explain(engine("FR", {"US01": "US"}), movement()) is None


def test_el_importe_original_en_otra_divisa_sigue_contando():
    assert explain(engine("US", {"US01": "US"}), movement(orig_ccy="EUR")) == "SHORT_BANK_CHARGES"


def test_mas_de_60_euros_no_es_comision():
    assert explain(engine("CA", {"US01": "US"}), movement(), gap=6001) is None


def test_mas_del_2_por_ciento_no_es_comision():
    eng = engine("CA", {"US01": "US"})
    small = Item(id="A", kind="ARI", side="AR", entity="US01", number="A", party="C1", ccy="USD", total=10000, open=10000)
    assert eng.explain_short(small, movement(), 201, 9799) is None


def test_una_factura_de_proveedor_no_es_comision():
    eng = engine("CA", {"US01": "US"})
    supplier = Item(id="A", kind="API", side="AP", entity="US01", number="A", party="C1", ccy="USD", total=TOTAL, open=TOTAL)
    assert eng.explain_short(supplier, movement(), SMALL_GAP, TOTAL - SMALL_GAP) is None
