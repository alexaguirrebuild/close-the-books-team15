"""Nombres dentro del texto del banco: primero los terceros de la sociedad, y un corte solo vale con evidencia exacta."""
import datetime as dt
from types import SimpleNamespace

import pytest

from bankrec import nombres_cortados, nombres_sociedad
from bankrec.data import Item, name_key
from bankrec.engine import Engine

DAY = dt.date(2025, 6, 10)
CASH = 10000


def invoice(item_id, party, entity, cents=CASH):
    return Item(id=item_id, kind="ARI", side="AR", entity=entity, number=item_id, party=party, ccy="EUR", total=cents, open=cents, issue=dt.date(2025, 5, 1))


def line(text, entity="CN01", amount=CASH):
    return SimpleNamespace(id="T1", text=text, rmt="", ref1="", ref2="", entity=entity, amount=amount, ccy="EUR", date=DAY, orig_ccy=None)


def engine(names, items):
    """names: tercero -> nombre. Un motor real con solo lo que usan scan_names y open_items_of."""
    party_names = {party: {name_key(name)} for party, name in names.items()}
    name_index = {}
    for party, keys in party_names.items():
        for key in keys:
            name_index.setdefault(key.split()[0], set()).add(party)
    by_party = {}
    for item in items:
        by_party.setdefault(item.party, []).append(item.id)
    eng = Engine.__new__(Engine)
    eng.items = {item.id: item for item in items}
    eng.ph = SimpleNamespace(name_index=name_index, party_names=party_names, items=eng.items, by_party=by_party, children={})
    return eng


LONG_ELSEWHERE = {"CUS1": "Mary Young Service", "CCN1": "Mary Young"}
ITEMS_BOTH = [invoice("A1", "CUS1", "US01"), invoice("A2", "CCN1", "CN01")]


class TestPrimeroLaSociedad:
    def test_sin_el_plugin_gana_el_nombre_mas_largo_de_otra_sociedad(self):
        eng = engine(LONG_ELSEWHERE, ITEMS_BOTH)

        assert eng.scan_names(line("TT IN MARY YOUNG SERVICE SEPTEMBER 2024")) == ["CUS1"]

    def test_con_el_plugin_gana_el_de_la_sociedad_del_movimiento(self):
        eng = engine(LONG_ELSEWHERE, ITEMS_BOTH)
        nombres_sociedad.prepare(eng)

        assert eng.scan_names(line("TT IN MARY YOUNG SERVICE SEPTEMBER 2024")) == ["CCN1"]

    def test_si_en_la_sociedad_no_hay_nadie_queda_lo_de_antes(self):
        eng = engine(LONG_ELSEWHERE, ITEMS_BOTH)
        nombres_sociedad.prepare(eng)

        assert eng.scan_names(line("TT IN MARY YOUNG SERVICE", entity="DE01")) == ["CUS1"]

    def test_la_fabrica_de_pagos_cuenta_como_la_misma_sociedad(self):
        eng = engine({"S1": "Bombas Levante Largo", "S2": "Bombas Levante"}, [invoice("A1", "S1", "US01"), invoice("A2", "S2", "ES01")])
        nombres_sociedad.prepare(eng)

        assert eng.scan_names(line("PAGO BOMBAS LEVANTE LARGO", entity="ES00")) == ["S2"]

    def test_un_tercero_sin_partidas_en_la_sociedad_no_cuenta(self):
        eng = engine(LONG_ELSEWHERE, [invoice("A1", "CUS1", "US01")])
        nombres_sociedad.prepare(eng)

        assert eng.scan_names(line("TT IN MARY YOUNG SERVICE")) == ["CUS1"]

    def test_deja_el_indice_como_estaba_aunque_falle(self):
        eng = engine(LONG_ELSEWHERE, ITEMS_BOTH)
        whole = eng.ph.name_index
        nombres_sociedad.prepare(eng)
        eng.scan_names(line("TT IN MARY YOUNG SERVICE"))
        assert eng.ph.name_index is whole

        with pytest.raises(AttributeError):
            eng.scan_names(SimpleNamespace(entity="CN01"))

        assert eng.ph.name_index is whole


class TestNombresCortados:
    def prepara(self, names, items):
        eng = engine(names, items)
        nombres_cortados.prepare(eng)
        return eng

    def test_corte_en_medio_del_texto_con_importe_exacto_y_un_solo_cliente(self):
        eng = self.prepara({"C1": "Aquatics Harris 2000"}, [invoice("A1", "C1", "CN01")])

        assert eng.scan_names(line("FP AQUATICS HARRIS 20 SERVICE DECEMBER 2")) == ["C1"]

    def test_corte_justo_al_final_con_el_mismo_numero_de_palabras(self):
        eng = self.prepara({"C1": "Nova Esperança do Sul Jardins"}, [invoice("A1", "C1", "CN01")])

        assert eng.scan_names(line("LIQUIDACAO BOLETO 5293358777-5 NOVA ESPERANÇA DO SUL JAR")) == ["C1"]

    def test_entre_homonimos_decide_el_importe_exacto(self):
        eng = self.prepara({"C1": "Green Bailey Outdoor Living", "C2": "Green Bailey Outdoor Living"}, [invoice("A1", "C1", "CN01"), invoice("A2", "C2", "CN01", cents=5000)])

        assert eng.scan_names(line("FP GREEN & BAILEY OUT SERVICE")) == ["C1"]

    def test_lo_que_ya_se_encontro_va_primero(self):
        eng = self.prepara({"C0": "Dragon Pools", "C1": "Aquatics Harris 2000"}, [invoice("A0", "C0", "CN01", cents=5000), invoice("A1", "C1", "CN01")])

        assert eng.scan_names(line("FP DRAGON POOLS AQUATICS HARRIS 20 SERVICE")) == ["C0", "C1"]

    def test_no_fuerza_con_dos_clientes_de_importe_exacto(self):
        eng = self.prepara({"C1": "Aquatics Harris 2000", "C2": "Aquatics Harris 2000"}, [invoice("A1", "C1", "CN01"), invoice("A2", "C2", "CN01")])

        assert eng.scan_names(line("FP AQUATICS HARRIS 20 SERVICE")) == []

    def test_no_fuerza_sin_importe_exacto(self):
        eng = self.prepara({"C1": "Aquatics Harris 2000"}, [invoice("A1", "C1", "CN01", cents=9000)])

        assert eng.scan_names(line("FP AQUATICS HARRIS 20 SERVICE")) == []

    def test_no_fuerza_con_una_sola_palabra_completa(self):
        eng = self.prepara({"C1": "Backyard Hollowford"}, [invoice("A1", "C1", "CN01")])

        assert eng.scan_names(line("FP BACKYARD HOLLOWFOR PAYMENT")) == []

    def test_no_fuerza_si_el_importe_es_de_otro_y_el_nombre_no_aparece(self):
        eng = self.prepara({"C1": "Aquatics Harris 2000"}, [invoice("A1", "C1", "CN01")])

        assert eng.scan_names(line("FP OTRA EMPRESA DISTINTA SERVICE")) == []

    def test_no_fuerza_si_el_cliente_es_de_otra_sociedad(self):
        eng = self.prepara({"C1": "Aquatics Harris 2000"}, [invoice("A1", "C1", "US01")])

        assert eng.scan_names(line("FP AQUATICS HARRIS 20 SERVICE")) == []

    def test_no_se_aplica_a_los_cargos(self):
        eng = self.prepara({"C1": "Aquatics Harris 2000"}, [invoice("A1", "C1", "CN01")])

        assert eng.scan_names(line("FP AQUATICS HARRIS 20 SERVICE", amount=-CASH)) == []

    def test_una_palabra_distinta_no_es_un_corte(self):
        eng = self.prepara({"C1": "Aquatics Harris 2000"}, [invoice("A1", "C1", "CN01")])

        assert eng.scan_names(line("FP AQUATICS HARRIS 19 SERVICE")) == []


class TestLosDosJuntos:
    def test_el_orden_de_carga_no_rompe_ninguno(self):
        eng = engine({**LONG_ELSEWHERE, "C1": "Aquatics Harris 2000"}, [*ITEMS_BOTH, invoice("A3", "C1", "CN01", cents=7000)])
        nombres_sociedad.prepare(eng)
        nombres_cortados.prepare(eng)

        assert eng.scan_names(line("TT IN MARY YOUNG SERVICE")) == ["CCN1"]
        assert eng.scan_names(line("FP AQUATICS HARRIS 20 SERVICE", amount=7000)) == ["C1"]

    def test_el_orden_de_carga_inverso_tampoco(self):
        eng = engine({**LONG_ELSEWHERE, "C1": "Aquatics Harris 2000"}, [*ITEMS_BOTH, invoice("A3", "C1", "CN01", cents=7000)])
        nombres_cortados.prepare(eng)
        nombres_sociedad.prepare(eng)

        assert eng.scan_names(line("TT IN MARY YOUNG SERVICE")) == ["CCN1"]
        assert eng.scan_names(line("FP AQUATICS HARRIS 20 SERVICE", amount=7000)) == ["C1"]
