"""scripts/submit.py: en FINAL solo hay un envío, así que ningún fallo evitable puede llegar después de gastar el hueco."""
import gzip
import hashlib
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


FORMATO_OK = {"official_format_ok": True, "error_counts": {"INGEST_HAD_ERRORS": 1}}


def informe_validacion(submit, raiz, fichero, texto=None, **cambios):
    """El validador rechaza y, al ejecutarse, escribe su informe: por defecto, de este fichero y solo con el error de ingesta."""
    informe = {**FORMATO_OK, "file_sha256": hashlib.sha256(fichero.read_bytes()).hexdigest(), **cambios}
    ruta = raiz / "out" / "final" / "validate__sub.json"

    def validador(*a, **k):
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(json.dumps(informe) if texto is None else texto, encoding="utf-8")
        return SimpleNamespace(returncode=1)

    submit.subprocess.run = validador
    return ruta


@pytest.fixture
def rechazado(submit, tmp_path, monkeypatch):
    """El validador estricto rechaza el fichero, como hace siempre que la ingesta registró algún error."""
    fichero = tmp_path / "sub.csv.gz"
    fichero.write_bytes(b"datos")
    monkeypatch.setattr(submit.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1))
    return fichero


class TestEnviarConErroresDeIngesta:
    def test_sin_la_opcion_no_envia_aunque_el_informe_lo_permitiria(self, submit, rechazado, tmp_path):
        informe_validacion(submit, tmp_path, rechazado)
        with pytest.raises(SystemExit, match="No se envía"):
            submit.strict_validate("FINAL", rechazado)

    def test_con_la_opcion_y_solo_ese_error_lo_acepta_y_avisa(self, submit, rechazado, tmp_path, capsys):
        informe_validacion(submit, tmp_path, rechazado)
        assert submit.strict_validate("FINAL", rechazado, True) is True
        assert "errores de ingesta" in capsys.readouterr().out

    def test_con_la_opcion_y_otro_error_mas_no_envia(self, submit, rechazado, tmp_path):
        informe_validacion(submit, tmp_path, rechazado, error_counts={"INGEST_HAD_ERRORS": 1, "MISSING_LINES": 3})
        with pytest.raises(SystemExit, match="MISSING_LINES"):
            submit.strict_validate("FINAL", rechazado, True)

    def test_con_la_opcion_y_otro_error_sin_el_de_ingesta_no_envia(self, submit, rechazado, tmp_path):
        informe_validacion(submit, tmp_path, rechazado, error_counts={"MISSING_LINES": 3})
        with pytest.raises(SystemExit, match="No se envía"):
            submit.strict_validate("FINAL", rechazado, True)

    def test_con_la_opcion_y_sin_errores_registrados_no_envia(self, submit, rechazado, tmp_path):
        informe_validacion(submit, tmp_path, rechazado, error_counts={})
        with pytest.raises(SystemExit, match="No se envía"):
            submit.strict_validate("FINAL", rechazado, True)

    def test_con_la_opcion_y_un_informe_de_otro_fichero_no_envia(self, submit, rechazado, tmp_path):
        informe_validacion(submit, tmp_path, rechazado, file_sha256="0" * 64)
        with pytest.raises(SystemExit, match="otro fichero"):
            submit.strict_validate("FINAL", rechazado, True)

    def test_con_la_opcion_y_el_formato_oficial_rechazado_no_envia(self, submit, rechazado, tmp_path):
        informe_validacion(submit, tmp_path, rechazado, official_format_ok=False)
        with pytest.raises(SystemExit, match="formato"):
            submit.strict_validate("FINAL", rechazado, True)

    def test_con_la_opcion_y_official_format_ok_no_booleano_no_envia(self, submit, rechazado, tmp_path):
        informe_validacion(submit, tmp_path, rechazado, official_format_ok="true")
        with pytest.raises(SystemExit, match="formato"):
            submit.strict_validate("FINAL", rechazado, True)

    def test_con_la_opcion_y_sin_informe_no_envia(self, submit, rechazado):
        with pytest.raises(SystemExit, match="No se envía"):
            submit.strict_validate("FINAL", rechazado, True)

    def test_con_la_opcion_y_un_informe_corrupto_no_envia(self, submit, rechazado, tmp_path):
        informe_validacion(submit, tmp_path, rechazado, texto="{ no es json")
        with pytest.raises(SystemExit, match="No se envía"):
            submit.strict_validate("FINAL", rechazado, True)

    def test_con_la_opcion_y_un_informe_que_no_es_un_objeto_no_envia(self, submit, rechazado, tmp_path):
        informe_validacion(submit, tmp_path, rechazado, texto="[1, 2]")
        with pytest.raises(SystemExit, match="No se envía"):
            submit.strict_validate("FINAL", rechazado, True)

    @pytest.mark.parametrize("codigo", [1, 2, 127, -9])
    def test_un_informe_anterior_no_vale_si_el_validador_no_llego_a_escribir_otro(self, submit, rechazado, tmp_path, monkeypatch, codigo):
        ruta = informe_validacion(submit, tmp_path, rechazado)
        submit.subprocess.run(None)
        monkeypatch.setattr(submit.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=codigo))
        assert ruta.exists()
        with pytest.raises(SystemExit, match="No se envía"):
            submit.strict_validate("FINAL", rechazado, True)

    def test_sin_la_opcion_no_toca_el_informe(self, submit, rechazado, tmp_path):
        ruta = informe_validacion(submit, tmp_path, rechazado)
        submit.subprocess.run(None)
        submit.subprocess.run = lambda *a, **k: SimpleNamespace(returncode=1)
        with pytest.raises(SystemExit):
            submit.strict_validate("FINAL", rechazado)
        assert ruta.exists()

    def test_si_el_validador_aprueba_no_cuenta_como_aceptado(self, submit, rechazado, monkeypatch):
        monkeypatch.setattr(submit.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
        assert submit.strict_validate("FINAL", rechazado, True) is False

    def test_lee_el_informe_de_la_fase_y_del_fichero_que_se_envia(self, submit, tmp_path):
        assert submit.validation_report_path("FINAL", tmp_path / "sub_final.csv.gz") == tmp_path / "out" / "final" / "validate__sub_final.json"


class TestLineaDeOrdenes:
    def test_sin_la_opcion_queda_desactivada(self, submit):
        args = submit.parse_args(["FINAL", "out/sub_final.csv.gz"])
        assert (args.phase, args.path, args.accept_ingest_errors) == ("FINAL", "out/sub_final.csv.gz", False)

    @pytest.mark.parametrize("orden", [["--accept-ingest-errors", "FINAL", "f.gz"], ["FINAL", "f.gz", "--accept-ingest-errors"]])
    def test_la_opcion_vale_en_cualquier_posicion(self, submit, orden):
        assert submit.parse_args(orden).accept_ingest_errors is True

    def test_faltan_argumentos(self, submit):
        with pytest.raises(SystemExit):
            submit.parse_args(["FINAL"])


def test_el_envio_aceptado_con_errores_queda_registrado(submit, envio, tmp_path, monkeypatch):
    monkeypatch.setattr(submit, "strict_validate", lambda *_: True)
    monkeypatch.setattr(submit, "call", respuestas((201, UPLOAD), (200, {"status": "scored", "score": 90.0})))
    monkeypatch.setattr("builtins.input", lambda _: "FINAL")
    submit.main("FINAL", str(envio), accept_ingest_errors=True)
    assert registro(tmp_path)[-1]["accepted_ingest_errors"] is True


def test_un_envio_normal_no_lleva_esa_marca(submit, envio, tmp_path, monkeypatch):
    monkeypatch.setattr(submit, "call", respuestas((201, UPLOAD), (200, {"status": "scored", "score": 90.0})))
    submit.main("LIVE", str(envio))
    assert "accepted_ingest_errors" not in registro(tmp_path)[-1]


class TestBytesEnviados:
    def preparado(self, submit, monkeypatch, durante_la_confirmacion=lambda: None):
        call = respuestas((201, UPLOAD), (200, {"status": "scored", "score": 90.0}))
        monkeypatch.setattr(submit, "call", call)
        subidos = []
        monkeypatch.setattr(submit, "s3_upload", lambda url, fields, nombre, datos: subidos.append(datos) or 204)

        def confirma(_):
            durante_la_confirmacion()
            return "si"

        monkeypatch.setattr("builtins.input", confirma)
        return call, subidos

    def test_sube_exactamente_lo_validado(self, submit, envio, monkeypatch):
        _, subidos = self.preparado(submit, monkeypatch)
        submit.main("LIVE", str(envio))
        assert subidos == [b"datos"]

    def test_si_el_fichero_cambia_mientras_se_confirma_no_gasta_hueco(self, submit, envio, monkeypatch):
        call, subidos = self.preparado(submit, monkeypatch, lambda: envio.write_bytes(b"otro fichero"))
        with pytest.raises(SystemExit, match="ha cambiado"):
            submit.main("LIVE", str(envio))
        assert call.llamadas == [] and subidos == []

    def test_si_el_fichero_desaparece_mientras_se_confirma_no_gasta_hueco(self, submit, envio, monkeypatch):
        call, _ = self.preparado(submit, monkeypatch, envio.unlink)
        with pytest.raises(SystemExit, match="ha cambiado"):
            submit.main("LIVE", str(envio))
        assert call.llamadas == []

    def test_si_cambia_durante_la_validacion_tampoco(self, submit, envio, monkeypatch):
        call, _ = self.preparado(submit, monkeypatch)
        monkeypatch.setattr(submit, "strict_validate", lambda *_: envio.write_bytes(b"otro fichero") and None)
        with pytest.raises(SystemExit, match="ha cambiado"):
            submit.main("LIVE", str(envio))
        assert call.llamadas == []
