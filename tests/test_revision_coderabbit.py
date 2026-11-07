"""Guardas que pidió la revisión de la PR #2: datos que no aparecen en TRAIN ni en LIVE no pueden tirar FINAL."""
import csv
import datetime as dt
import subprocess
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

from bankrec import pdfclauses, remittance

RAIZ = Path(__file__).resolve().parents[1]


class TestProformaConClausulas:
    def motor(self, tmp_path):
        carpeta = tmp_path / "final" / "pdfs"
        carpeta.mkdir(parents=True)
        with (carpeta / "index.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, ["file", "document_type", "document_number", "entity", "party_id"])
            writer.writeheader()
            writer.writerow({"file": "pro.pdf", "document_type": "proforma", "document_number": "P1", "entity": "E1", "party_id": "C1"})
        ph = SimpleNamespace(name="final", dir=tmp_path / "final", party_by_iban={})
        return SimpleNamespace(ph=ph, items={}, resolve_party=lambda *_: (None, None), resolve_parties=lambda *_: ([], None))

    def test_no_para_la_fase_y_no_asocia_la_clausula(self, tmp_path, monkeypatch):
        motor = self.motor(tmp_path)
        monkeypatch.setattr(pdfclauses, "_load_texts", lambda *_: {"pro.pdf": "texto"})
        monkeypatch.setattr(pdfclauses, "parse_clauses", lambda *_: {"withholding_pct": 10.0})
        monkeypatch.setattr(pdfclauses, "PHASES", ())

        pdfclauses.prepare(motor)

        assert motor.clauses == {}


class TestPartidaSinFecha:
    def test_empatar_con_una_sin_fecha_no_rompe_el_orden(self, monkeypatch):
        sin_fecha = SimpleNamespace(id="A", kind="ARI", open=100, issue=None, party="C1", entity="E1")
        con_fecha = SimpleNamespace(id="B", kind="ARI", open=100, issue=dt.date(2025, 1, 1), party="C1", entity="E1")
        ph = SimpleNamespace(by_num=defaultdict(set), by_po=defaultdict(set))
        ph.by_po["PO1"] = {"A", "B"}
        motor = SimpleNamespace(ph=ph, items={"A": sin_fecha, "B": con_fecha}, ra_num={}, item_by_number=lambda *a, **k: None)
        monkeypatch.setattr("bankrec.data.num_variants", lambda _: ["PO1"])
        monkeypatch.setattr(remittance, "_family", lambda *_: {"C1"})

        encontrada = remittance._lookup(motor, {"doc": "PO1", "paid": 100}, SimpleNamespace(entity="E1"), "C1")

        assert encontrada is sin_fecha


class TestGanchoPreCommit:
    def test_sin_gitleaks_no_deja_hacer_commit(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        entorno = {"PATH": _path_sin_gitleaks(tmp_path), "HOME": str(tmp_path)}
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=entorno)
        (repo / "a.txt").write_text("x", encoding="utf-8")
        subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True, env=entorno)

        resultado = subprocess.run(["sh", str(RAIZ / ".githooks" / "pre-commit")], cwd=repo, env=entorno, capture_output=True, text=True)

        assert resultado.returncode == 1
        assert "gitleaks" in resultado.stderr


def _path_sin_gitleaks(tmp_path: Path) -> str:
    """Una carpeta con solo lo que el gancho usa, para que `command -v gitleaks` falle también donde sí está instalado."""
    binarios = tmp_path / "bin"
    binarios.mkdir()
    for nombre in ("git", "grep", "sh", "printf"):
        ruta = subprocess.run(["sh", "-c", f"command -v {nombre}"], capture_output=True, text=True).stdout.strip()
        if ruta.startswith("/"):
            (binarios / nombre).symlink_to(ruta)
    return str(binarios)
