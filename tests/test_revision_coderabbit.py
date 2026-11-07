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


def motor_vacio(**campos):
    from bankrec.engine import Engine

    motor = Engine.__new__(Engine)
    for nombre, valor in campos.items():
        setattr(motor, nombre, valor)
    return motor


class TestDesempatesDeterministas:
    def test_mismo_numero_y_fecha_gana_el_identificador_menor(self, monkeypatch):
        ids = ["H", "C", "A", "F", "B", "G", "E", "D"]
        partidas = {i: SimpleNamespace(id=i, kind="ARI", entity="E", party="C", open=10, issue=dt.date(2025, 1, 1)) for i in ids}
        motor = motor_vacio(ph=SimpleNamespace(by_num={"N1": set(ids)}), items=partidas)
        monkeypatch.setattr("bankrec.engine.num_variants", lambda numero: [numero])

        assert motor.item_by_number("N1").id == "A"


class TestChequeSobreFacturaCerrada:
    def test_elige_la_abierta_aunque_la_cerrada_comparta_sufijo(self):
        filas = []
        motor = motor_vacio(
            used_batches=set(),
            cheque_items={},
            cheque_deps={"RCH1": [{"amount": "10.00", "stub_reference": "123456", "drawer": "X", "cheque_number": "9"}]},
            ph=SimpleNamespace(by_suffix={"123456": {"ARI-1", "ARI-2"}}, by_num={}),
            items={
                "ARI-1": SimpleNamespace(id="ARI-1", side="AR", open=0, total=1000),
                "ARI-2": SimpleNamespace(id="ARI-2", side="AR", open=1000, total=1000),
            },
            entity_ok=lambda *_: True,
            find_by_party_amount=lambda *_, **__: None,
            emit=lambda t, it, amt, exc, rule, ev="": filas.append((it.id, amt)),
        )

        assert motor.m_cheque(SimpleNamespace(amount=1000, up="RCH1", account="A")) is True
        assert filas == [("ARI-2", 1000)]


class TestAbonoYaAplicadoEnParte:
    def test_la_factura_recibe_lo_cobrado_mas_el_saldo_del_abono(self):
        factura = SimpleNamespace(id="ARI-1", ccy="EUR", open=10000, total=10000, terms=None)
        abono = SimpleNamespace(id="ARC-1", ccy="EUR", open=2000, total=5000, terms=None)
        filas = []

        def emit(t, it, amt, exc, rule, ev=""):
            filas.append((it.id, amt, exc))
            it.open -= amt

        motor = motor_vacio(
            items={"ARI-1": factura, "ARC-1": abono},
            ph=SimpleNamespace(fx=SimpleNamespace(conv=lambda c, *_: c)),
            sign=lambda it, t: 1 if it is factura else -1,
            to_bank=lambda importe, it, t: importe,
            explain_short=lambda *_: None,
            emit=emit,
        )

        motor.settle(SimpleNamespace(amount=5000, ccy="EUR", date=None), ["ARI-1", "ARC-1"], "regla")

        assert filas == [("ARC-1", 2000, ""), ("ARI-1", 7000, "PARTIAL")]
