"""Guardas del motor: lo que en FINAL costaría puntos sin que nadie lo notara."""
import builtins
import gzip
import json
import os
import subprocess
import sys
import sysconfig
from pathlib import Path
from types import SimpleNamespace

import pytest

from bankrec import pdfclauses, preflight
from bankrec.engine import Engine, Row, load_plugins
from bankrec.run import assemble, report_broken_advices

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


class _Txn:
    def __init__(self, txn_id):
        self.id = txn_id
        self.date = None


class _Phase:
    def __init__(self, *ids):
        self.txns = [_Txn(i) for i in ids]


class TestAssemble:
    def test_una_fila_con_importe_cero_no_se_envia(self):
        rows = [Row("T1", "ARI-2025-0000001", 1000), Row("T1", "ARI-2025-0000002", 0)]

        out = assemble(_Phase("T1"), rows)

        assert [r.item for r in out] == ["ARI-2025-0000001"]

    def test_un_importe_negativo_tampoco(self):
        rows = [Row("T1", "ARI-2025-0000001", 1000), Row("T1", "ARI-2025-0000002", -5)]

        assert [r.item for r in assemble(_Phase("T1"), rows)] == ["ARI-2025-0000001"]

    def test_si_todas_las_partidas_quedan_a_cero_el_movimiento_sigue_teniendo_fila(self):
        rows = [Row("T1", "ARI-2025-0000001", 0)]

        out = assemble(_Phase("T1"), rows)

        assert len(out) == 1
        assert out[0].item == ""
        assert out[0].category == "UNIDENTIFIED"

    def test_si_quedan_a_cero_usa_la_fila_sin_partida_que_hubiera(self):
        rows = [Row("T1", "ARI-2025-0000001", 0), Row("T1", "", None, "BANK_FEES")]

        out = assemble(_Phase("T1"), rows)

        assert [(r.item, r.category) for r in out] == [("", "BANK_FEES")]

    def test_dos_filas_de_la_misma_partida_se_suman_antes_de_filtrar(self):
        rows = [Row("T1", "ARI-2025-0000001", 400), Row("T1", "ARI-2025-0000001", 600)]

        out = assemble(_Phase("T1"), rows)

        assert [r.amount for r in out] == [1000]

    def test_todo_movimiento_tiene_fila(self):
        out = assemble(_Phase("T1", "T2"), [Row("T1", "", None, "BANK_FEES")])

        assert [r.txn for r in out] == ["T1", "T2"]


class _Eng:
    def __init__(self, errores, vacios=()):
        self.ra_errors = errores
        self.ra_empty = list(vacios)


def test_informa_de_los_avisos_rotos(capsys):
    report_broken_advices(_Eng([("ES01/a.pdf", "ValueError()"), ("ES01/b.pdf", "KeyError()")]))

    salida = capsys.readouterr().out
    assert "2 avisos" in salida
    assert "ES01/a.pdf" in salida


def test_informa_de_los_avisos_sin_lineas(capsys):
    report_broken_advices(_Eng([], ["ES01/c.pdf"]))

    assert "1 avisos sin líneas" in capsys.readouterr().out


def test_no_dice_nada_si_no_hay_avisos_rotos(capsys):
    report_broken_advices(object())

    assert capsys.readouterr().out == ""


class TestDependencias:
    def test_falla_con_un_mensaje_claro_si_falta_una_dependencia(self, monkeypatch):
        real = builtins.__import__

        def sin_pypdf(name, *args, **kwargs):
            if name == "pypdf":
                raise ModuleNotFoundError(name=name)
            return real(name, *args, **kwargs)

        monkeypatch.delitem(sys.modules, "pypdf", raising=False)
        monkeypatch.setattr(builtins, "__import__", sin_pypdf)

        with pytest.raises(SystemExit) as parada:
            preflight.check_dependencies()

        assert "pypdf" in str(parada.value)
        assert "uv run" in str(parada.value)

    def test_pasa_si_estan_todas(self):
        preflight.check_dependencies()

    def test_un_plugin_obligatorio_que_no_carga_para_el_motor(self):
        with pytest.raises(ModuleNotFoundError):
            load_plugins("no_existe_este_plugin")

    def test_un_plugin_opcional_ausente_se_salta(self):
        assert load_plugins("learned") == []

    def test_carga_los_plugins_por_defecto(self):
        assert [mod.__name__ for mod in load_plugins("remittance,pdfclauses")] == ["bankrec.remittance", "bankrec.pdfclauses"]


def _fase(base: Path, nombre: str, *ficheros: str) -> None:
    (base / nombre).mkdir(parents=True, exist_ok=True)
    for fichero in ficheros:
        (base / nombre / fichero).write_bytes(b"")


class TestEntradasDeLaFase:
    def _completa(self, base: Path) -> None:
        _fase(base, "train", "ar_invoices.csv.gz")
        _fase(base, "live", "ar_invoices.csv.gz", "open_items_at_start.csv.gz")
        _fase(base, "final", "open_items_at_start.csv.gz")
        (base / "final" / "masters_final").mkdir()

    def test_final_completa_pasa(self, tmp_path):
        self._completa(tmp_path)

        preflight.check_phase_inputs("final", tmp_path)

    @pytest.mark.parametrize(
        ("quitar", "mensaje"),
        [
            ("final/open_items_at_start.csv.gz", "open_items_at_start"),
            ("final/masters_final", "masters_final"),
            ("live/ar_invoices.csv.gz", "live"),
            ("train/ar_invoices.csv.gz", "train"),
        ],
    )
    def test_final_incompleta_para_el_motor_antes_de_generar_nada(self, tmp_path, quitar, mensaje):
        self._completa(tmp_path)
        objetivo = tmp_path / quitar
        objetivo.rmdir() if objetivo.is_dir() else objetivo.unlink()

        with pytest.raises(SystemExit) as parada:
            preflight.check_phase_inputs("final", tmp_path)

        assert mensaje in str(parada.value)

    def test_live_necesita_train_pero_no_masters_final(self, tmp_path):
        _fase(tmp_path, "train", "ar_invoices.csv.gz")
        _fase(tmp_path, "live", "open_items_at_start.csv.gz")

        preflight.check_phase_inputs("live", tmp_path)

    def test_las_fases_con_golden_no_exigen_nada_de_esto(self, tmp_path):
        preflight.check_phase_inputs("sample", tmp_path)
        preflight.check_phase_inputs("holdout", tmp_path)


def test_un_descuento_tardio_sin_referencia_se_imputa_como_parcial():
    """POLICIES §3: descuento fuera de plazo es PARTIAL. Antes caía a UNIDENTIFIED."""
    factura = SimpleNamespace(id="ARI-1", open=10000, ccy="EUR")
    motor = Engine.__new__(Engine)
    motor.open_items_of = lambda *_: [factura]
    motor.sign = lambda *_: 1
    motor.to_bank = lambda cents, *_: cents
    motor.best_subset = lambda *_, **__: None
    motor.explain_short = lambda *_: "LATE"
    imputadas = []
    motor.settle = lambda t, ids, rule, ev="", party=None: imputadas.append(ids) or True
    cobro = SimpleNamespace(amount=9800, ccy="EUR", up="TRANSFERENCIA")

    assert motor._party_amount(cobro, "C1", "iban") is True
    assert imputadas == [["ARI-1"]]


class TestCacheDePdf:
    def test_un_pdf_que_no_se_pudo_leer_no_queda_vacio_para_siempre(self, tmp_path, monkeypatch):
        """Una ejecución sin pypdf guardaba textos vacíos y las siguientes los reutilizaban aunque ya estuviera instalado."""
        monkeypatch.setattr(pdfclauses, "OUT", tmp_path)
        (tmp_path / "pdf_text_final.json").write_text(json.dumps({"a.pdf": "", "b.pdf": "texto"}), encoding="utf-8")
        pedidos = []

        def extrae(paths):
            pedidos.extend(paths)
            return ["leido" for _ in paths]

        monkeypatch.setattr(pdfclauses, "_extract_many", extrae)

        textos = pdfclauses._load_texts(Path("/pdfs"), "final", ["a.pdf", "b.pdf"])

        assert textos == {"a.pdf": "leido", "b.pdf": "texto"}
        assert [Path(x) for x in pedidos] == [Path("/pdfs/a.pdf")]  # Path: en Windows el separador es «\»

    def test_no_guarda_vacios_en_la_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pdfclauses, "OUT", tmp_path)
        monkeypatch.setattr(pdfclauses, "_extract_many", lambda paths: ["" for _ in paths])

        pdfclauses._load_texts(Path("/pdfs"), "final", ["a.pdf"])

        assert json.loads((tmp_path / "pdf_text_final.json").read_text(encoding="utf-8")) == {}

    def test_avisa_de_cuantos_pdf_quedan_sin_texto(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(pdfclauses, "OUT", tmp_path)
        monkeypatch.setattr(pdfclauses, "_extract_many", lambda paths: ["" for _ in paths])

        pdfclauses._load_texts(Path("/pdfs"), "final", ["a.pdf", "b.pdf"])

        assert "2 PDF" in capsys.readouterr().out


class TestDeterminismo:
    def test_fija_la_semilla_se_relanza_y_sale_con_el_codigo_del_hijo(self, monkeypatch):
        llamadas = []
        monkeypatch.delenv("PYTHONHASHSEED", raising=False)
        monkeypatch.setattr(os, "execv", lambda *_: pytest.fail("os.execv no espera al hijo en Windows"))
        monkeypatch.setattr(subprocess, "call", lambda argv, env: llamadas.append((argv, env["PYTHONHASHSEED"])) or 3)

        with pytest.raises(SystemExit) as salida:
            preflight.ensure_deterministic(["--phase", "live"])

        assert salida.value.code == 3
        assert llamadas == [([sys.executable, "-m", "bankrec.run", "--phase", "live"], "0")]

    def test_no_se_relanza_si_ya_esta_fijada(self, monkeypatch):
        monkeypatch.setenv("PYTHONHASHSEED", "0")
        monkeypatch.setattr(os, "execv", lambda *_: pytest.fail("no debía relanzarse"))
        monkeypatch.setattr(subprocess, "call", lambda *_, **__: pytest.fail("no debía relanzarse"))

        preflight.ensure_deterministic([])


class TestRelanzamientoReal:
    """Sin PYTHONHASHSEED, el motor se relanza a sí mismo. Con os.execv eso fallaba en Windows cuando la ruta del
    intérprete tenía un espacio: el proceso salía con 0 al instante y el hijo moría con «can't open file». No se
    escribía ningún fichero. Aquí se lanza un intérprete de verdad desde una carpeta con espacios."""

    def test_con_espacios_en_la_ruta_el_motor_arranca_y_devuelve_su_codigo(self, tmp_path):
        venv = tmp_path / "carpeta con espacios" / "venv"
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True, capture_output=True)
        python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        entorno = {k: v for k, v in os.environ.items() if k != "PYTHONHASHSEED"}
        # El venv vacío ve el motor y las dependencias obligatorias (pypdf, openpyxl) de este intérprete.
        entorno["PYTHONPATH"] = os.pathsep.join([str(ROOT), sysconfig.get_paths()["purelib"]])

        # Una fase que no existe hace que argparse pare con 2 antes de cargar datos: así se ve que el hijo llegó a arrancar.
        resultado = subprocess.run([str(python), "-m", "bankrec.run", "--phase", "fase con espacios"], cwd=tmp_path,
                                   env=entorno, capture_output=True, text=True, timeout=120)

        assert resultado.returncode == 2, resultado.stderr
        assert "'fase con espacios'" in resultado.stderr  # el argumento con espacios llega entero


@pytest.mark.skipif(not (DATA / "sample" / "golden_allocations.csv.gz").exists(), reason="hace falta data/sample")
def test_generar_un_fichero_nunca_abre_el_golden_aunque_este_al_lado(monkeypatch):
    """Lo que se envía en LIVE y FINAL sale de este camino. Sample tiene golden al lado, así que aquí se notaría."""
    from bankrec.data import Phase

    abiertos = []
    real_gzip, real_open = gzip.open, builtins.open

    def espia_gzip(path, *args, **kwargs):
        abiertos.append(str(path))
        return real_gzip(path, *args, **kwargs)

    def espia_open(path, *args, **kwargs):
        abiertos.append(str(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(gzip, "open", espia_gzip)
    monkeypatch.setattr(builtins, "open", espia_open)

    phase = Phase("sample")
    Engine(phase).run()

    assert not [path for path in abiertos if "golden" in path.lower()]
    assert any("bank_transactions" in path for path in abiertos)
