"""Avisos en un formato nuevo: el modelo solo se consulta si el parser no lee nada, y solo vale lo que está en el fichero."""
import json
from types import SimpleNamespace

import pytest

from bankrec import advice_llm, llm, remittance

NEW_LAYOUT = """Customer remittance
Ref: PAY-778812
Settled 2026-02-03 in EUR, total 1.500,00
Doc FV26-000123 invoice 1.000,00 deduction -100,00 (price difference) paid 900,00
Doc FV26-000124 invoice 600,00 paid 600,00
"""
REPLY = {"ref": "PAY-778812", "pay_date": "2026-02-03", "currency": "EUR", "total": "1.500,00",
         "lines": [{"doc": "FV26-000123", "gross": "1.000,00", "deduction": "-100,00", "paid": "900,00", "reason": "price difference"},
                   {"doc": "FV26-000124", "gross": "600,00", "deduction": "0", "paid": "600,00", "reason": ""}]}


class Sent(list):
    reply = "{}"


@pytest.fixture
def api(monkeypatch, tmp_path):
    monkeypatch.setenv("BANKREC_LLM", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "clave-de-prueba")
    monkeypatch.setattr(llm, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(llm, "calls", 0)
    sent = Sent()
    monkeypatch.setattr(llm, "_post", lambda payload, key: sent.append(payload) or
                        {"content": [{"type": "text", "text": sent.reply}], "stop_reason": "end_turn"})
    return sent


@pytest.fixture
def wrapped(monkeypatch):
    monkeypatch.setattr(remittance, "read_advice", remittance.read_advice)
    advice_llm.prepare(None)


@pytest.fixture
def advice_file(tmp_path):
    path = tmp_path / "aviso.txt"
    path.write_text(NEW_LAYOUT, encoding="utf-8")
    return path


def unreadable(monkeypatch):
    monkeypatch.setitem(remittance.READERS, "txt", lambda path: remittance._blank())


def test_aviso_que_el_parser_no_lee_se_pide_al_modelo(api, wrapped, advice_file, monkeypatch):
    unreadable(monkeypatch)
    api.reply = json.dumps(REPLY)
    adv = remittance.read_advice(advice_file, "txt")
    assert [(x["doc"], x["gross"], x["ded"], x["paid"]) for x in adv["lines"]] == [
        ("FV26-000123", 100000, -10000, 90000), ("FV26-000124", 60000, 0, 60000)]
    assert adv["total"] == 150000 and adv["ref"] == "PAY-778812" and adv["ccy"] == "EUR" and str(adv["pay_date"]) == "2026-02-03"


def test_linea_con_importe_inventado_se_descarta(api, wrapped, advice_file, monkeypatch):
    unreadable(monkeypatch)
    bad = json.loads(json.dumps(REPLY))
    bad["lines"][1]["paid"] = "555,00"
    api.reply = json.dumps(bad)
    adv = remittance.read_advice(advice_file, "txt")
    assert [x["doc"] for x in adv["lines"]] == ["FV26-000123"]


def test_documento_que_no_esta_en_el_texto_se_descarta(api, wrapped, advice_file, monkeypatch):
    unreadable(monkeypatch)
    bad = json.loads(json.dumps(REPLY))
    bad["lines"][0]["doc"] = "FV26-999999"
    api.reply = json.dumps(bad)
    assert [x["doc"] for x in remittance.read_advice(advice_file, "txt")["lines"]] == ["FV26-000124"]


def test_si_no_queda_ninguna_linea_verificada_todo_sigue_como_antes(api, wrapped, advice_file, monkeypatch):
    unreadable(monkeypatch)
    api.reply = json.dumps({"lines": [{"doc": "FV26-999999", "paid": "1,23"}]})
    assert remittance.read_advice(advice_file, "txt")["lines"] == []


def test_si_el_parser_lee_el_aviso_no_llama(api, wrapped, advice_file, monkeypatch):
    ok = remittance._blank()
    ok["lines"] = [{"doc": "X"}]
    monkeypatch.setitem(remittance.READERS, "txt", lambda path: ok)
    assert remittance.read_advice(advice_file, "txt") is ok
    assert api == []


def test_apagado_no_llama_ni_cambia_el_comportamiento(monkeypatch, wrapped, advice_file):
    monkeypatch.delenv("BANKREC_LLM", raising=False)
    unreadable(monkeypatch)
    assert remittance.read_advice(advice_file, "txt")["lines"] == []


def test_el_fallo_original_se_conserva_si_el_modelo_tampoco_lo_lee(api, wrapped, advice_file, monkeypatch):
    def boom(path):
        raise ValueError("formato roto")
    monkeypatch.setitem(remittance.READERS, "txt", boom)
    api.reply = "{}"
    with pytest.raises(ValueError):
        remittance.read_advice(advice_file, "txt")
