"""Guardas del motor: lo que en FINAL costaría puntos sin que nadie lo notara."""
import builtins
import datetime as dt
from collections import defaultdict
import gzip
import json
import os
import subprocess
import sys
import sysconfig
from pathlib import Path
from types import SimpleNamespace

import pytest

from bankrec import devoluciones, m2_avisos, pdfclauses, preflight, remittance
from bankrec.data import Item
from bankrec.engine import Engine, Row, _days_apart, load_plugins, to_cents
from bankrec.run import assemble, report_broken_advices, report_plugin_errors

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

    def test_avisa_de_cuantos_pdf_estan_en_disco_y_no_dan_texto(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(pdfclauses, "OUT", tmp_path)
        monkeypatch.setattr(pdfclauses, "_extract_many", lambda rutas: ["" for _ in rutas])
        (tmp_path / "a.pdf").write_bytes(b"x")
        (tmp_path / "b.pdf").write_bytes(b"x")

        pdfclauses._load_texts(tmp_path, "final", ["a.pdf", "b.pdf"])

        salida = capsys.readouterr().out
        assert "2 PDF sin texto legible" in salida
        assert "no están en disco" not in salida

    def test_un_pdf_que_no_esta_en_disco_no_se_cuenta_como_ilegible(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(pdfclauses, "OUT", tmp_path)
        monkeypatch.setattr(pdfclauses, "_extract_many", lambda rutas: ["" for _ in rutas])
        (tmp_path / "a.pdf").write_bytes(b"x")

        pdfclauses._load_texts(tmp_path, "train", ["a.pdf", "no-esta-1.pdf", "no-esta-2.pdf"])

        salida = capsys.readouterr().out
        assert "2 PDF del índice no están en disco" in salida
        assert "train completo" in salida
        assert "1 PDF sin texto legible" in salida

    def test_sin_faltantes_no_avisa(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(pdfclauses, "OUT", tmp_path)
        monkeypatch.setattr(pdfclauses, "_extract_many", lambda rutas: ["texto" for _ in rutas])
        (tmp_path / "a.pdf").write_bytes(b"x")

        pdfclauses._load_texts(tmp_path, "final", ["a.pdf"])

        assert "AVISO" not in capsys.readouterr().out


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


def plugin(nombre, **metodos):
    return SimpleNamespace(__name__=f"bankrec.{nombre}", **metodos)


def movimiento(amount=-1000, cp_iban=""):
    return SimpleNamespace(id="T1", amount=amount, up="", text="", ref1="", ref2="", rmt="", code="", subcode="", cp_iban=cp_iban, cp_name="", date=None)


def motor_para_plugins(*plugins, movimientos=None, partidas=()):
    """El bucle de `run` con todo lo que no es de plugins apagado: se ve qué hace con cada plugin y con el motor base."""
    eng = Engine.__new__(Engine)
    eng.rows, eng.iban_learned, eng.reversal = [], {}, set()
    eng.settled_by = defaultdict(list)
    eng.items = {item.id: item for item in partidas}
    eng.ph = SimpleNamespace(txns=movimientos or [movimiento()])
    eng.plugins = list(plugins)
    eng.base_llamado = []
    for nombre in ("m_dd_batch", "m_dd_return", "m_card", "m_cheque", "m_payment_run", "m_party_amount"):
        setattr(eng, nombre, lambda t: False)
    eng.m_refs = lambda t: eng.base_llamado.append(t.id) or False
    return eng


def partida(item_id="P1", cents=1000, open_=None):
    return Item(id=item_id, kind="ARI", side="AR", entity="ES01", number=item_id, party="C1", ccy="EUR", total=cents, open=cents if open_ is None else open_)


class TestPluginQueFallaEnMatch:
    def test_no_para_la_fase_y_sigue_con_el_motor_base(self):
        eng = motor_para_plugins(plugin("roto", match=lambda engine, t: 1 / 0))
        filas = eng.run()
        assert eng.base_llamado == ["T1"]
        assert [(r.txn, r.category) for r in filas] == [("T1", "BANK_FEES")]

    def test_deshace_las_filas_y_el_saldo_de_lo_que_emitio(self):
        item = partida()

        def emite_y_falla(engine, t):
            engine.emit(t, item, 400, "PARTIAL", "roto")
            raise ValueError("dato raro")

        eng = motor_para_plugins(plugin("roto", match=emite_y_falla), partidas=[item])
        filas = eng.run()
        assert item.open == 1000
        assert eng.settled_by[item.id] == []
        assert [r.item for r in filas] == [""]

    def test_una_devolucion_vuelve_al_saldo_exacto_aunque_toparan_con_el_total(self):
        item = partida(open_=900)

        def devuelve_y_falla(engine, t):
            engine.emit(t, item, 500, "RETURNED", "roto")  # topa en 1000: sumar y restar 500 no daría 900
            raise ValueError("dato raro")

        motor_para_plugins(plugin("roto", match=devuelve_y_falla), partidas=[item]).run()
        assert item.open == 900

    def test_no_queda_aprendido_el_iban_de_lo_que_se_deshizo(self):
        item = partida()

        def emite_y_falla(engine, t):
            engine.emit(t, item, 400, "", "roto")
            raise ValueError("dato raro")

        eng = motor_para_plugins(plugin("roto", match=emite_y_falla), movimientos=[movimiento(cp_iban="ES1")], partidas=[item])
        eng.iban_learned = {}
        eng.run()
        assert eng.iban_learned == {}

    def test_conserva_el_iban_que_ya_estaba_aprendido(self):
        item = partida()

        def emite_y_falla(engine, t):
            engine.emit(t, item, 400, "", "roto")
            raise ValueError("dato raro")

        eng = motor_para_plugins(plugin("roto", match=emite_y_falla), movimientos=[movimiento(cp_iban="ES1")], partidas=[item])
        eng.iban_learned = {"ES1": "OTRO"}
        eng.run()
        assert eng.iban_learned == {"ES1": "OTRO"}

    def test_deshace_tambien_una_fila_escrita_a_mano_sin_partida_conocida(self):
        def fila_y_falla(engine, t):
            engine.rows.append(Row(t.id, "DESCONOCIDA", 100, "", "", "roto"))
            raise ValueError("dato raro")

        filas = motor_para_plugins(plugin("roto", match=fila_y_falla)).run()
        assert [r.item for r in filas] == [""]

    def test_el_siguiente_plugin_se_sigue_probando(self):
        vistos = []
        bueno = plugin("bueno", match=lambda engine, t: vistos.append(t.id) or False)
        motor_para_plugins(plugin("roto", match=lambda engine, t: 1 / 0), bueno).run()
        assert vistos == ["T1"]

    def test_queda_anotado_quien_fallo_en_que_movimiento_y_por_que(self):
        eng = motor_para_plugins(plugin("roto", match=lambda engine, t: 1 / 0))
        eng.run()
        assert eng.plugin_errors == [("bankrec.roto", "T1", "ZeroDivisionError('division by zero')")]

    def test_system_exit_sigue_parando(self):
        def obligatorio(engine, t):
            raise SystemExit("falta un fichero obligatorio")

        with pytest.raises(SystemExit):
            motor_para_plugins(plugin("obligatorio", match=obligatorio)).run()

    def test_un_fallo_del_motor_base_sigue_parando(self):
        eng = motor_para_plugins()
        eng.m_refs = lambda t: 1 / 0
        with pytest.raises(ZeroDivisionError):
            eng.run()

    def test_una_regla_correcta_no_cambia(self):
        item = partida()
        bueno = plugin("bueno", match=lambda engine, t: engine.emit(t, item, 1000, "", "bueno") or True)
        eng = motor_para_plugins(bueno, partidas=[item])
        filas = eng.run()
        assert [(r.item, r.amount) for r in filas] == [("P1", 1000)]
        assert not getattr(eng, "plugin_errors", [])


class TestPluginQueFallaEnPrepare:
    def carga(self, monkeypatch, *modulos):
        monkeypatch.setattr("bankrec.engine.load_plugins", lambda nombres: list(modulos))
        eng = Engine.__new__(Engine)
        eng.plugins = []
        eng._load_plugins("da igual")
        return eng

    def test_se_desactiva_ese_plugin_y_siguen_los_demas(self, monkeypatch):
        def roto(engine):
            raise KeyError("columna")

        bueno = plugin("bueno", match=lambda engine, t: False)
        eng = self.carga(monkeypatch, plugin("roto", prepare=roto, match=lambda engine, t: False), bueno)
        assert eng.plugins == [bueno]
        assert eng.plugin_errors == [("bankrec.roto", "prepare", "KeyError('columna')")]

    def test_deshace_lo_que_envolvio_antes_de_fallar(self, monkeypatch):
        def envuelve_y_falla(engine):
            engine.settle = lambda *a: pytest.fail("envoltura a medias")
            raise ValueError("dato raro")

        eng = self.carga(monkeypatch, plugin("roto", prepare=envuelve_y_falla))
        assert "settle" not in eng.__dict__

    def test_system_exit_sigue_parando(self, monkeypatch):
        def obligatorio(engine):
            raise SystemExit("Falta m2_remittance")

        with pytest.raises(SystemExit):
            self.carga(monkeypatch, plugin("m2_avisos", prepare=obligatorio))

    def test_un_plugin_sano_se_prepara_una_vez(self, monkeypatch):
        vistos = []
        self.carga(monkeypatch, plugin("bueno", prepare=lambda engine: vistos.append(engine)))
        assert len(vistos) == 1


class TestAvisoDeFallosDePlugin:
    def test_dice_cuantos_y_enseña_los_primeros(self, capsys):
        errores = [("bankrec.roto", f"T{n}", "ValueError('x')") for n in range(7)]
        report_plugin_errors(SimpleNamespace(plugin_errors=errores))
        salida = capsys.readouterr().out
        assert "7 fallos de plugin" in salida
        assert "T4" in salida and "T5" not in salida

    def test_sin_fallos_no_dice_nada(self, capsys):
        report_plugin_errors(SimpleNamespace())
        assert capsys.readouterr().out == ""


class TestConversionesConCamposVacios:
    def test_un_aviso_con_importe_vacio_no_lanza(self):
        item = partida()
        eng = motor_para_plugins(partidas=[item])
        eng.m2_rows = {"T1": [{"item_id": "P1", "amount": "", "exception": "", "advice_id": "A1"}]}
        assert m2_avisos.match(eng, movimiento()) is True

    def test_devoluciones_con_importe_vacio_vale_cero(self):
        assert devoluciones._cents("") == 0

    def test_un_aviso_con_fecha_mala_cuenta_como_roto_y_no_lanza(self, tmp_path, monkeypatch):
        carpeta = tmp_path / "remittance_advices"
        carpeta.mkdir()
        (carpeta / "a.txt").write_text("x", encoding="utf-8")
        filas = [{"file": "a.txt", "format": "txt", "entity": "ES01", "payer_name": "X", "received_date": "no es fecha"}]
        monkeypatch.setattr(remittance, "rows", lambda path: filas)
        monkeypatch.setattr(remittance, "read_advice", lambda path, fmt: {"lines": [{"paid": 1}], "total": None, "ref": ""})
        (carpeta / "index.csv").write_text("x", encoding="utf-8")
        eng = SimpleNamespace(ph=SimpleNamespace(dir=tmp_path), items={}, resolve_name=lambda raw: None)
        remittance.prepare(eng)
        assert [archivo for archivo, _ in eng.ra_errors] == ["a.txt"]


class TestDatosMalosEnFicherosAuxiliares:
    def test_un_importe_no_numerico_vale_cero(self):
        assert to_cents("12,50") == 0 and to_cents(" ") == 0

    def test_un_importe_normal_no_cambia(self):
        assert to_cents("12.50") == 1250 and to_cents("") == 0

    def test_una_fecha_vacia_queda_la_ultima_candidata(self):
        fecha = dt.date(2025, 1, 10)
        assert _days_apart("2025-01-12", fecha) == 2
        assert _days_apart("", fecha) > 1000
        assert _days_apart("no es fecha", fecha) > 1000
