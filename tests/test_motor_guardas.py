"""Guardas del motor: lo que en FINAL costaría puntos sin que nadie lo notara."""
import builtins
import gzip
import json
import os
import sys
from pathlib import Path

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
    def __init__(self, errores):
        self.ra_errors = errores


def test_informa_de_los_avisos_rotos(capsys):
    report_broken_advices(_Eng([("ES01/a.pdf", "ValueError()"), ("ES01/b.pdf", "KeyError()")]))

    salida = capsys.readouterr().out
    assert "2 avisos" in salida
    assert "ES01/a.pdf" in salida


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
        assert pedidos == ["/pdfs/a.pdf"]

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
    def test_fija_la_semilla_y_se_relanza_si_no_esta(self, monkeypatch):
        llamadas = []
        monkeypatch.delenv("PYTHONHASHSEED", raising=False)
        monkeypatch.setattr(os, "execv", lambda exe, argv: llamadas.append((exe, argv)))

        preflight.ensure_deterministic(["--phase", "live"])

        assert os.environ["PYTHONHASHSEED"] == "0"
        assert llamadas == [(sys.executable, [sys.executable, "-m", "bankrec.run", "--phase", "live"])]

    def test_no_se_relanza_si_ya_esta_fijada(self, monkeypatch):
        monkeypatch.setenv("PYTHONHASHSEED", "0")
        monkeypatch.setattr(os, "execv", lambda *_: pytest.fail("no debía relanzarse"))

        preflight.ensure_deterministic([])


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
