"""scripts/final_run.py: un solo comando, que para en el primer fallo y nunca sube nada."""
import ast
import csv
import gzip
import importlib.util
import json
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
FUENTE = RAIZ / "scripts" / "final_run.py"
BANK_HEADER = "bank_txn_id,entity,amount,currency,text"


@pytest.fixture
def final_run(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("final_run_bajo_prueba", FUENTE)
    modulo = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, modulo)  # dataclass busca el módulo aquí.
    spec.loader.exec_module(modulo)
    monkeypatch.setattr(modulo, "ROOT", tmp_path)
    monkeypatch.delenv("BANKREC_OUT", raising=False)
    return modulo


def nombres(pasos):
    return [paso.name for paso in pasos]


class TestPlan:
    def test_encadena_ingesta_avisos_motor_y_validacion(self, final_run):
        comandos = [" ".join(paso.command) for paso in final_run.plan("final")]

        assert "bankcheck ingest --phase final" in comandos[0]
        assert "bankcheck match --phase final" in comandos[1]
        assert "bankrec.run --phase final --no-score" in comandos[2]
        assert "bankcheck validate --phase final" in comandos[3]
        assert comandos[3].endswith("sub_final.csv.gz")

    def test_la_misma_cadena_sirve_para_el_ensayo(self, final_run):
        assert "--phase live" in " ".join(final_run.plan("live")[2].command)

    def test_permitir_errores_de_ingesta_solo_afecta_a_la_ingesta(self, final_run):
        con_permiso, sin_permiso = final_run.plan("final", allow_ingest_errors=True), final_run.plan("final")

        assert "--allow-errors" in con_permiso[0].command
        assert "--allow-errors" not in sin_permiso[0].command
        assert con_permiso[1:] == sin_permiso[1:]


class TestNuncaSube:
    def test_ningun_paso_llama_a_submit(self, final_run):
        for fase in ("final", "live"):
            for paso in final_run.plan(fase):
                assert "submit" not in " ".join(paso.command)

    def test_el_script_no_importa_nada_que_hable_con_la_red(self):
        arbol = ast.parse(FUENTE.read_text(encoding="utf-8"))
        importados = set()
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.Import):
                importados.update(alias.name.split(".")[0] for alias in nodo.names)
            elif isinstance(nodo, ast.ImportFrom) and nodo.module:
                importados.add(nodo.module.split(".")[0])

        assert not importados & {"urllib", "http", "requests", "socket", "ssl", "submit", "download"}


class TestLimpieza:
    def test_borra_la_cache_de_pdf_y_aparta_los_ficheros_anteriores(self, final_run, tmp_path):
        out = tmp_path / "out"
        out.mkdir()
        (out / "pdf_text_final.json").write_text("{}", encoding="utf-8")
        (out / "pdf_text_live.json").write_text("{}", encoding="utf-8")
        (out / "sub_final.csv.gz").write_bytes(b"anterior")
        (out / "explain_final.csv.gz").write_bytes(b"anterior")

        final_run.clear_stale("final")

        assert not (out / "pdf_text_final.json").exists()
        assert (out / "pdf_text_live.json").exists()
        assert not (out / "sub_final.csv.gz").exists(), "un fichero antiguo también valida: no puede quedarse con ese nombre"
        assert (out / "sub_final.anterior.csv.gz").read_bytes() == b"anterior"
        assert (out / "explain_final.anterior.csv.gz").exists()

    def test_sin_ficheros_anteriores_no_falla(self, final_run):
        final_run.clear_stale("final")


class TestEjecucion:
    def test_para_en_el_primer_fallo_y_devuelve_su_codigo(self, final_run):
        pasos = final_run.plan("final")
        lanzados = []

        def ejecuta(comando):
            lanzados.append(comando)
            return 3 if len(lanzados) == 2 else 0

        codigo, tiempos = final_run.run_steps(pasos, ejecuta, lambda _paso: False)

        assert codigo == 3
        assert lanzados == [pasos[0].command, pasos[1].command]
        assert [nombre for nombre, _, _ in tiempos] == nombres(pasos[:2])

    def test_si_todo_va_bien_lanza_los_cuatro_en_orden(self, final_run):
        pasos = final_run.plan("final")
        lanzados = []

        codigo, tiempos = final_run.run_steps(pasos, lambda c: lanzados.append(c) or 0, lambda _paso: False)

        assert codigo == 0
        assert lanzados == [paso.command for paso in pasos]
        assert len(tiempos) == 4

    def test_un_fallo_tolerado_deja_seguir(self, final_run):
        pasos = final_run.plan("final", allow_ingest_errors=True)
        lanzados = []

        def ejecuta(comando):
            lanzados.append(comando)
            return 1 if len(lanzados) == 1 else 0

        codigo, _ = final_run.run_steps(pasos, ejecuta, lambda paso: paso.name == pasos[0].name)

        assert codigo == 0
        assert len(lanzados) == 4


class TestIngestaConErrores:
    def informe(self, tmp_path, publicado):
        carpeta = tmp_path / "out" / "final"
        carpeta.mkdir(parents=True)
        (carpeta / "ingest_report.json").write_text(json.dumps({"published": publicado}), encoding="utf-8")

    def test_solo_se_sigue_si_se_pidio_y_el_almacen_se_publico(self, final_run, tmp_path):
        self.informe(tmp_path, True)
        ingesta, avisos, *_ = final_run.plan("final", allow_ingest_errors=True)

        assert final_run.tolerated("final", ingesta, allow_ingest_errors=True) is True
        assert final_run.tolerated("final", ingesta, allow_ingest_errors=False) is False
        assert final_run.tolerated("final", avisos, allow_ingest_errors=True) is False

    def test_con_el_almacen_sin_publicar_no_se_sigue(self, final_run, tmp_path):
        self.informe(tmp_path, False)
        ingesta = final_run.plan("final", allow_ingest_errors=True)[0]

        assert final_run.tolerated("final", ingesta, allow_ingest_errors=True) is False

    def test_sin_informe_no_se_sigue(self, final_run):
        ingesta = final_run.plan("final", allow_ingest_errors=True)[0]

        assert final_run.tolerated("final", ingesta, allow_ingest_errors=True) is False


class TestSociedadesNuevas:
    def prepara(self, tmp_path, movimientos, filas):
        (tmp_path / "data" / "masters").mkdir(parents=True)
        (tmp_path / "data" / "final").mkdir()
        (tmp_path / "out").mkdir()
        (tmp_path / "data" / "masters" / "entities.csv").write_text("entity,legal_name\nES01,Vieja\n", encoding="utf-8")
        with gzip.open(tmp_path / "data" / "final" / "bank_transactions.csv.gz", "wt", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(BANK_HEADER.split(","))
            writer.writerows(movimientos)
        with gzip.open(tmp_path / "out" / "explain_final.csv.gz", "wt", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["bank_txn_id", "item_id", "amount", "category", "exception", "rule", "evidence"])
            writer.writerows(filas)

    def test_cuenta_que_sale_de_cada_sociedad_que_no_estaba_en_los_maestros(self, final_run, tmp_path):
        self.prepara(
            tmp_path,
            [("T1", "ZZ09", "10.00", "ZAR", "COBRO KWI1"), ("T2", "ZZ09", "20.00", "ZAR", "COBRO RARO"), ("T3", "ES01", "5.00", "EUR", "OTRO")],
            [("T1", "ARI-1", "10.00", "", "", "ref", ""), ("T2", "", "", "UNIDENTIFIED", "UNIDENTIFIED", "fallback_receipt", ""), ("T3", "", "", "UNIDENTIFIED", "UNIDENTIFIED", "fallback_receipt", "")],
        )

        texto = "\n".join(final_run.new_entities_report("final"))

        assert "ZZ09" in texto and "2 movimientos" in texto
        assert "con partida 1" in texto and "UNIDENTIFIED 1" in texto
        assert "T2" in texto and "COBRO RARO" in texto
        assert "ES01" not in texto and "T3" not in texto

    def test_si_casi_nada_tiene_partida_avisa_de_que_hay_que_revisarlo(self, final_run, tmp_path):
        self.prepara(
            tmp_path,
            [("T1", "ZZ09", "10.00", "ZAR", "A"), ("T2", "ZZ09", "20.00", "ZAR", "B")],
            [("T1", "", "", "UNIDENTIFIED", "UNIDENTIFIED", "fallback_receipt", ""), ("T2", "", "", "BANK_FEES", "", "fallback_debit", "")],
        )

        assert "REVISAR" in "\n".join(final_run.new_entities_report("final"))

    def test_sin_sociedades_nuevas_lo_dice(self, final_run, tmp_path):
        self.prepara(tmp_path, [("T3", "ES01", "5.00", "EUR", "OTRO")], [("T3", "ARI-1", "5.00", "", "", "ref", "")])

        assert "Ninguna" in "\n".join(final_run.new_entities_report("final"))


class TestFinalDelComando:
    def test_el_envio_se_deja_al_usuario(self, final_run):
        texto = final_run.next_step("final")

        assert "! uv run python scripts/submit.py FINAL out/sub_final.csv.gz" in texto
        assert "OK" in texto

    def test_en_el_ensayo_no_propone_enviar(self, final_run):
        assert "submit" not in final_run.next_step("live")


class TestValidacionConErroresDeIngesta:
    def lanza(self, final_run, tmp_path, monkeypatch, capsys, informe):
        ruta = tmp_path / "out" / "final" / "validate__sub_final.json"
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(json.dumps(informe), encoding="utf-8")
        monkeypatch.setattr(final_run, "clear_stale", lambda phase: None)
        monkeypatch.setattr(final_run, "run_steps", lambda *a: (1, [(final_run.VALIDATE, 1.0, 1)]))
        monkeypatch.setattr(final_run, "new_entities_report", lambda phase: ["INFORME DE SOCIEDADES"])
        codigo = final_run.main(["--phase", "final", "--allow-ingest-errors"])
        return codigo, capsys.readouterr().out

    def test_solo_ingesta_ensena_el_informe_y_el_comando_pero_no_lo_da_por_valido(self, final_run, tmp_path, monkeypatch, capsys):
        codigo, salida = self.lanza(final_run, tmp_path, monkeypatch, capsys, {"official_format_ok": True, "error_counts": {"INGEST_HAD_ERRORS": 2}})
        assert codigo == 1
        assert "INFORME DE SOCIEDADES" in salida
        assert "--accept-ingest-errors" in salida
        assert "No hay fichero listo" not in salida
        assert "Fichero listo y validado" not in salida

    @pytest.mark.parametrize("informe", [
        {"official_format_ok": False, "error_counts": {"INGEST_HAD_ERRORS": 2}},
        {"official_format_ok": True, "error_counts": {"INGEST_HAD_ERRORS": 2, "MISSING_LINES": 1}},
        {"official_format_ok": True, "error_counts": {}},
    ])
    def test_con_otro_problema_sigue_parado(self, final_run, tmp_path, monkeypatch, capsys, informe):
        codigo, salida = self.lanza(final_run, tmp_path, monkeypatch, capsys, informe)
        assert codigo == 1
        assert "No hay fichero listo" in salida
        assert "--accept-ingest-errors" not in salida.split("No hay fichero listo")[0]

    def test_sin_informe_sigue_parado(self, final_run, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(final_run, "clear_stale", lambda phase: None)
        monkeypatch.setattr(final_run, "run_steps", lambda *a: (1, [(final_run.VALIDATE, 1.0, 1)]))
        assert final_run.main(["--phase", "final"]) == 1
        assert "No hay fichero listo" in capsys.readouterr().out
