"""Cliente de la API y lectura de PDF con modelo: apagado por defecto, con tope, con caché y sin fiarse de lo que no esté en el texto."""
import json

import pytest

from bankrec import llm, pdfclauses, pdfllm

UNUSUAL = ("Muster GmbH RECHNUNG Nr. 1 Datum 01/02/2025 Bezeichnung Menge Preis Betrag Service 1 1.000,00 1.000,00 "
           "Nettobetrag 1.000,00 EUR MwSt. 0,00 EUR GESAMT 1.000,00 EUR Hinweis: Sicherheitseinbehalt von 100,00 EUR "
           "wird nach Abnahme freigegeben, zahlbar bei Faelligkeit sind 900,00 EUR. Documento sintético de prueba")
BOILERPLATE = ("Muster GmbH RECHNUNG Nr. 1 Datum 01/02/2025 Nettobetrag 1.000,00 EUR GESAMT 1.000,00 EUR "
               "Zahlungsbedingungen: 30 días fecha factura Documento sintético de prueba")


class Sent(list):
    """Llamadas enviadas a la API falsa; `reply` es lo que contesta."""
    reply = "{}"
    stop = "end_turn"


@pytest.fixture
def api(monkeypatch, tmp_path):
    monkeypatch.setenv("BANKREC_LLM", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "clave-de-prueba")
    monkeypatch.setattr(llm, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(llm, "calls", 0)
    sent = Sent()

    def post(payload, key):
        sent.append((payload, key))
        return {"content": [{"type": "text", "text": sent.reply}] if sent.reply else [{"type": "thinking", "thinking": ""}],
                "stop_reason": sent.stop}
    monkeypatch.setattr(llm, "_post", post)
    return sent


class TestCliente:
    def test_apagado_por_defecto_no_llama(self, monkeypatch):
        monkeypatch.delenv("BANKREC_LLM", raising=False)
        monkeypatch.setattr(llm, "_post", lambda *a: pytest.fail("no debe llamar"))
        assert llm.ask("hola") is None

    def test_sin_clave_no_llama(self, monkeypatch):
        monkeypatch.setenv("BANKREC_LLM", "1")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setattr(llm, "ROOT", llm.ROOT / "no-existe")
        assert not llm.enabled()

    def test_misma_pregunta_se_paga_una_vez(self, api):
        api.reply = "uno"
        assert llm.ask("p") == "uno" and llm.ask("p") == "uno"
        assert len(api) == 1

    def test_no_envia_parametros_obsoletos(self, api):
        llm.ask("p", system="s")
        payload = api[0][0]
        assert payload["thinking"] == {"type": "disabled"}   # con razonamiento, el modelo gastaba los tokens sin contestar
        assert "temperature" not in payload and payload["system"] == "s" and payload["messages"][0]["role"] == "user"

    def test_respuesta_vacia_no_se_cachea_y_dice_por_que(self, api):
        api.reply, api.stop = "", "max_tokens"
        assert llm.ask("p") is None
        assert "max_tokens" in llm.last_error
        api.reply, api.stop = "ya hay texto", "end_turn"
        assert llm.ask("p") == "ya hay texto"   # no quedó cacheado el vacío

    def test_tope_de_llamadas(self, api, monkeypatch):
        monkeypatch.setenv("BANKREC_LLM_MAX_CALLS", "1")
        assert llm.ask("a") is not None
        assert llm.ask("b") is None
        assert len(api) == 1

    def test_un_fallo_devuelve_none(self, api, monkeypatch):
        def boom(*a):
            raise OSError("sin red")
        monkeypatch.setattr(llm, "_post", boom)
        assert llm.ask("x") is None

    def test_el_motivo_del_fallo_se_recuerda_sin_la_clave(self, api, monkeypatch):
        import io
        import urllib.error

        def denied(*a):
            raise urllib.error.HTTPError("u", 401, "Unauthorized", {}, io.BytesIO(b'{"error": {"type": "authentication_error", "message": "invalid x-api-key"}}'))
        monkeypatch.setattr(llm, "_post", denied)
        assert llm.ask("x") is None
        assert "401" in llm.last_error and "authentication_error" in llm.last_error
        assert "clave-de-prueba" not in llm.last_error

    def test_la_clave_no_se_guarda_en_la_cache(self, api, tmp_path):
        llm.ask("p")
        assert all("clave-de-prueba" not in f.read_text() for f in (tmp_path / "cache").iterdir())

    def test_ask_json_extrae_el_objeto(self, api):
        api.reply = 'Claro: {"a": 1} listo'
        assert llm.ask_json("p") == {"a": 1}
        api.reply = "nada"
        assert llm.ask_json("otro") is None


class TestPdf:
    @pytest.fixture(autouse=True)
    def wrapped(self, monkeypatch):
        monkeypatch.setattr(pdfclauses, "parse_clauses", pdfclauses.parse_clauses)
        pdfllm.prepare(None)

    def test_clausula_que_la_regex_no_lee_se_pide_al_modelo(self, api):
        api.reply = json.dumps({"ret": "100,00", "net_due": "900,00"})
        out = pdfclauses.parse_clauses(UNUSUAL, "invoice")
        assert out == {"ret": 10000, "net_due": 90000}
        assert len(api) == 1

    def test_importe_inventado_se_descarta(self, api):
        api.reply = json.dumps({"ret": "100,00", "net_due": "777,00"})
        assert pdfclauses.parse_clauses(UNUSUAL, "invoice") == {"ret": 10000}

    def test_clave_desconocida_o_modo_invalido_se_descartan(self, api):
        api.reply = json.dumps({"ret": "100,00", "iban": "ES00", "cn_mode": "refund"})
        assert pdfclauses.parse_clauses(UNUSUAL, "invoice") == {"ret": 10000}   # cn_mode solo en abonos

    def test_texto_estandar_no_llama(self, api):
        assert pdfclauses.parse_clauses(BOILERPLATE, "invoice") == {}
        assert api == []

    def test_proforma_no_llama(self, api):
        pdfclauses.parse_clauses(UNUSUAL, "proforma")
        assert api == []

    def test_si_la_regex_ya_lee_la_clausula_no_llama(self, api):
        text = "GESAMT 1.000,00 EUR " + "Anula y sustituye a la factura FV25-0001234 " + "x" * 90
        assert pdfclauses.parse_clauses(text, "invoice").get("replaces")
        assert api == []

    def test_apagado_devuelve_lo_de_la_regex(self, monkeypatch):
        monkeypatch.delenv("BANKREC_LLM", raising=False)
        assert pdfclauses.parse_clauses(UNUSUAL, "invoice") == {}
