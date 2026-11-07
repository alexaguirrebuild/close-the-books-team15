"""scripts/submit.py: en FINAL solo hay un envío, así que ningún fallo evitable puede llegar después de gastar el hueco."""
import gzip
import importlib.util
import json
import sys
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

RAIZ = Path(__file__).resolve().parents[1]
SUB_ID = "s_prueba"
UPLOAD = {"sub_id": SUB_ID, "upload": {"url": "https://s3.example/x", "fields": {}}}


@pytest.fixture
def submit(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("submit_bajo_prueba", RAIZ / "scripts" / "submit.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    monkeypatch.setattr(modulo, "ROOT", tmp_path)
    monkeypatch.setattr(modulo, "API", "https://api.example")
    monkeypatch.setattr(modulo.time, "sleep", lambda _: None)
    monkeypatch.delenv("HK_TOKEN", raising=False)
    return modulo


@pytest.fixture
def envio(submit, tmp_path, monkeypatch):
    """Todo listo para llegar hasta la subida: fichero, token, validaciones superadas y confirmación."""
    fichero = tmp_path / "sub.csv.gz"
    fichero.write_bytes(b"datos")
    monkeypatch.setenv("HK_TOKEN", "token-de-prueba")
    monkeypatch.setattr(submit, "local_validate", lambda *_: None)
    monkeypatch.setattr(submit, "strict_validate", lambda *_: None)
    monkeypatch.setattr("builtins.input", lambda _: "si")
    monkeypatch.setattr(submit, "s3_upload", lambda *_: 204)
    return fichero


def respuestas(*guion):
    """Sustituye a call: cada entrada es una respuesta (estado, cuerpo) o una excepción. Guarda lo que se ha llamado."""
    pendientes, llamadas = list(guion), []

    def call(method, url, tok=None, body=None, timeout=60):
        llamadas.append((method, url))
        siguiente = pendientes.pop(0)
        if isinstance(siguiente, Exception):
            raise siguiente
        return siguiente

    call.llamadas = llamadas
    return call


def error_http(codigo):
    return urllib.error.HTTPError("https://api.example", codigo, "fallo", {}, None)


def registro(raiz):
    return [json.loads(linea) for linea in (raiz / "out" / "envios.jsonl").read_text(encoding="utf-8").splitlines()]


class TestToken:
    def test_no_sale_sin_https(self, submit, monkeypatch):
        monkeypatch.setattr(submit.urllib.request, "urlopen", lambda *a, **k: pytest.fail("no debe enviarse nada"))

        with pytest.raises(SystemExit, match="https"):
            submit.call("GET", "http://api.example/x", tok="token-de-prueba")

    def test_https_mal_configurado_para_antes_de_validar(self, submit, envio, monkeypatch):
        monkeypatch.setattr(submit, "API", "http://api.example")
        monkeypatch.setattr(submit, "local_validate", lambda *_: pytest.fail("no debe llegar a validar"))

        with pytest.raises(SystemExit, match="https"):
            submit.main("LIVE", str(envio))

    def test_si_falta_no_llega_a_pedir_confirmacion(self, submit, envio, monkeypatch):
        monkeypatch.delenv("HK_TOKEN")
        monkeypatch.setattr("builtins.input", lambda _: pytest.fail("no debe pedir confirmación"))

        with pytest.raises(SystemExit, match="HK_TOKEN"):
            submit.main("LIVE", str(envio))


class TestValidacionLocal:
    def test_crea_la_carpeta_de_salida_si_no_existe(self, submit, tmp_path, monkeypatch):
        (tmp_path / "data" / "masters").mkdir(parents=True)
        (tmp_path / "data" / "masters" / "categories.csv").write_text("category\nBANK_FEES\n", encoding="utf-8")
        fichero = tmp_path / "sub.csv.gz"
        fichero.write_bytes(gzip.compress(b"x"))
        evaluador = SimpleNamespace(
            load_golden=lambda *_: None,
            score_submission=lambda *_: {"ok": True, "metrics": {"rows": 1, "txns_covered": 1, "txns_total": 1}},
        )
        monkeypatch.setitem(sys.modules, "bankrec_score", evaluador)

        submit.local_validate("LIVE", fichero)

        assert (tmp_path / "out" / "_empty_golden.csv.gz").exists()


class TestTrasGastarElHueco:
    def test_un_fallo_de_red_al_sondear_no_pierde_la_nota(self, submit, envio, tmp_path, monkeypatch):
        call = respuestas(
            (201, UPLOAD), (200, {"status": "scoring"}), OSError("red"), error_http(503), (200, {"status": "scored", "score": 87.3}),
        )
        monkeypatch.setattr(submit, "call", call)

        submit.main("LIVE", str(envio))

        assert registro(tmp_path)[0]["score"] == 87.3
        assert registro(tmp_path)[0]["sub_id"] == SUB_ID

    def test_si_falla_pedir_la_nota_se_reintenta(self, submit, envio, tmp_path, monkeypatch):
        call = respuestas((201, UPLOAD), error_http(502), (200, {"status": "scored", "score": 80.0}))
        monkeypatch.setattr(submit, "call", call)

        submit.main("LIVE", str(envio))

        assert [metodo for metodo, _ in call.llamadas] == ["POST", "POST", "POST"]
        assert registro(tmp_path)[0]["status"] == "scored"

    def test_un_4xx_al_pedir_la_nota_pasa_a_sondear(self, submit, envio, tmp_path, monkeypatch):
        """Quizá la primera petición llegó y se perdió la respuesta: el estado lo aclara, repetirla no."""
        call = respuestas((201, UPLOAD), error_http(409), (200, {"status": "scored", "score": 80.0}))
        monkeypatch.setattr(submit, "call", call)

        submit.main("LIVE", str(envio))

        assert [metodo for metodo, _ in call.llamadas] == ["POST", "POST", "GET"]

    def test_si_falla_la_subida_queda_registrado_con_sub_id(self, submit, envio, tmp_path, monkeypatch, capsys):
        def subida_rota(*_):
            raise urllib.error.URLError("sin red")

        monkeypatch.setattr(submit, "call", respuestas((201, UPLOAD)))
        monkeypatch.setattr(submit, "s3_upload", subida_rota)

        with pytest.raises(SystemExit, match=SUB_ID):
            submit.main("LIVE", str(envio))

        assert registro(tmp_path)[0]["sub_id"] == SUB_ID
        assert registro(tmp_path)[0]["status"] == "upload_failed"
        assert f"sub_id={SUB_ID}" in capsys.readouterr().out

    def test_sin_estado_final_avisa_de_no_reenviar_a_ciegas(self, submit, envio, tmp_path, monkeypatch):
        monkeypatch.setattr(submit, "POLL_DEADLINE_SECONDS", 0)
        monkeypatch.setattr(submit, "call", respuestas((201, UPLOAD), (200, {"status": "scoring"})))

        with pytest.raises(SystemExit, match=SUB_ID):
            submit.main("LIVE", str(envio))

        assert registro(tmp_path)[0]["status"] == "scoring"


def test_si_falla_pedir_la_url_no_se_reintenta_porque_gasta_hueco(submit, envio, monkeypatch):
    call = respuestas(error_http(500), pytest.fail)
    monkeypatch.setattr(submit, "call", call)

    with pytest.raises(SystemExit, match="upload-url"):
        submit.main("LIVE", str(envio))

    assert len(call.llamadas) == 1
