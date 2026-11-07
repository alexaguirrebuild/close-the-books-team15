"""scripts/download.py: a las 18:00 tiene que decir con claridad qué pasa, y nunca dar por bueno lo que no ha comprobado."""
import hashlib
import importlib.util
import io
import tarfile
import urllib.error
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
URL = "/data/v1/bankrec-final.tar.gz"


def tar_de(*rutas: str) -> bytes:
    salida = io.BytesIO()
    with tarfile.open(fileobj=salida, mode="w:gz") as tar:
        for ruta in rutas:
            info = tarfile.TarInfo(ruta)
            info.size = 1
            tar.addfile(info, io.BytesIO(b"x"))
    return salida.getvalue()


COMPLETO = tar_de("final/bank_transactions.csv.gz", "final/open_items_at_start.csv.gz", "final/masters_final/customers.csv")
SHA_COMPLETO = hashlib.sha256(COMPLETO).hexdigest()


@pytest.fixture
def download(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("download_bajo_prueba", RAIZ / "scripts" / "download.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    monkeypatch.setattr(modulo, "RAW", tmp_path / "raw")
    monkeypatch.setattr(modulo, "OUT", tmp_path)
    monkeypatch.setattr(modulo.time, "sleep", lambda _: None)
    return modulo


def publica(download, monkeypatch, sha=SHA_COMPLETO, available=True):
    monkeypatch.setattr(download, "config", lambda: {"downloads": [{"id": "final", "url": URL, "size": "100 MB", "sha256": sha, "available": available}]})


def sirve(download, monkeypatch, contenido=COMPLETO):
    monkeypatch.setattr(download, "fetch", lambda url, dest, tries=30: dest.write_bytes(contenido))


def error_http(codigo):
    return urllib.error.HTTPError("https://x", codigo, "error", {}, None)


class TestFetch:
    def test_un_403_no_se_reintenta(self, download, monkeypatch, tmp_path):
        llamadas = []

        def rechaza(*_args, **_kwargs):
            llamadas.append(1)
            raise error_http(403)

        monkeypatch.setattr(download.urllib.request, "urlopen", rechaza)

        with pytest.raises(download.Refused, match="403"):
            download.fetch("https://x/f", tmp_path / "f")

        assert len(llamadas) == 1

    def test_un_503_si_se_reintenta(self, download, monkeypatch, tmp_path):
        llamadas = []

        def falla(*_args, **_kwargs):
            llamadas.append(1)
            raise error_http(503)

        monkeypatch.setattr(download.urllib.request, "urlopen", falla)

        with pytest.raises(RuntimeError):
            download.fetch("https://x/f", tmp_path / "f", tries=3)

        assert len(llamadas) == 3

    def test_un_416_con_el_fichero_entero_no_es_un_fallo(self, download, monkeypatch, tmp_path):
        destino = tmp_path / "f"
        destino.write_bytes(b"entero")

        def fuera_de_rango(*_args, **_kwargs):
            raise error_http(416)

        monkeypatch.setattr(download.urllib.request, "urlopen", fuera_de_rango)

        download.fetch("https://x/f", destino)

        assert destino.read_bytes() == b"entero"


class TestMain:
    def test_sin_publicar_lo_dice_y_falla(self, download, monkeypatch, capsys):
        publica(download, monkeypatch, sha="", available=False)

        assert download.main(["final"]) == 1
        assert "no disponible" in capsys.readouterr().out

    def test_un_403_con_config_disponible_lo_explica_y_falla(self, download, monkeypatch, capsys):
        publica(download, monkeypatch)

        def rechaza(url, dest, tries=30):
            raise download.Refused("el servidor responde 403")

        monkeypatch.setattr(download, "fetch", rechaza)

        assert download.main(["final"]) == 1
        assert "403" in capsys.readouterr().out

    def test_sin_conexion_con_config_lo_explica_y_falla(self, download, monkeypatch, capsys):
        def sin_red():
            raise urllib.error.URLError("sin red")

        monkeypatch.setattr(download, "config", sin_red)

        assert download.main(["final"]) == 1
        assert "config.json" in capsys.readouterr().out

    def test_sin_sha_publicado_no_extrae_ni_dice_que_esta_bien(self, download, monkeypatch, tmp_path, capsys):
        publica(download, monkeypatch, sha="")
        sirve(download, monkeypatch)

        assert download.main(["final"]) == 1
        salida = capsys.readouterr().out
        assert "sha256 OK" not in salida
        assert "--sin-sha" in salida
        assert not (tmp_path / "final").exists()

    def test_sin_sha_se_puede_seguir_si_se_pide_a_proposito(self, download, monkeypatch, tmp_path, capsys):
        publica(download, monkeypatch, sha="")
        sirve(download, monkeypatch)

        assert download.main(["final"], allow_no_sha=True) == 0
        salida = capsys.readouterr().out
        assert "sha256 OK" not in salida
        assert "SIN COMPROBAR" in salida
        assert (tmp_path / "final" / "bank_transactions.csv.gz").is_file()

    def test_un_sha_distinto_borra_el_fichero_para_no_reanudar_sobre_algo_roto(self, download, monkeypatch, tmp_path, capsys):
        publica(download, monkeypatch, sha="0" * 64)
        sirve(download, monkeypatch)

        assert download.main(["final"]) == 1
        assert "NO COINCIDE" in capsys.readouterr().out
        assert not (tmp_path / "raw" / "bankrec-final.tar.gz").exists()
        assert not (tmp_path / "final").exists()

    def test_un_sha_correcto_deja_final_donde_lo_espera_el_motor(self, download, monkeypatch, tmp_path, capsys):
        publica(download, monkeypatch)
        sirve(download, monkeypatch)

        assert download.main(["final"]) == 0
        assert "sha256 OK" in capsys.readouterr().out
        assert (tmp_path / "final" / "masters_final" / "customers.csv").is_file()

    @pytest.mark.parametrize(
        ("rutas", "falta"),
        [
            (("final/bank_transactions.csv.gz", "final/open_items_at_start.csv.gz"), "masters_final"),
            (("final/masters_final/customers.csv", "final/open_items_at_start.csv.gz"), "bank_transactions"),
            (("bank_transactions.csv.gz", "open_items_at_start.csv.gz", "masters_final/customers.csv"), "final/bank_transactions"),
        ],
        ids=["sin_maestros", "sin_movimientos", "sin_carpeta_raiz"],
    )
    def test_un_archivo_valido_con_otra_forma_lo_dice_antes_de_seguir(self, download, monkeypatch, capsys, rutas, falta):
        contenido = tar_de(*rutas)
        publica(download, monkeypatch, sha=hashlib.sha256(contenido).hexdigest())
        sirve(download, monkeypatch, contenido)

        assert download.main(["final"]) == 1
        assert falta in capsys.readouterr().out
