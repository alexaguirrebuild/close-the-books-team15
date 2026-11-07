"""Las filas de los avisos enlazados (M2) entran por el libro de saldos del motor, no por encima de su salida."""
import csv
import gzip
from pathlib import Path
from types import SimpleNamespace

import pytest

from bankrec import m2_avisos
from bankrec.engine import Engine, Row

HEADER = ["bank_txn_id", "item_id", "amount", "category", "exception", "source", "confidence", "advice_id", "line_no"]


def escribe(path: Path, *filas) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(HEADER)
        writer.writerows(filas)
    return path


def motor(fase: str, **items) -> Engine:
    engine = Engine.__new__(Engine)
    engine.ph = SimpleNamespace(name=fase)
    engine.items = items
    engine.rows = []
    engine.settled_by = __import__("collections").defaultdict(list)
    engine.iban_learned = {}
    return engine


def factura(item_id: str, abierto: int) -> SimpleNamespace:
    return SimpleNamespace(id=item_id, open=abierto, total=abierto, party="C1")


def cobro(txn_id: str = "T1") -> SimpleNamespace:
    return SimpleNamespace(id=txn_id, cp_iban=None)


@pytest.fixture
def fichero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("BANKREC_M2_DIR", str(tmp_path))
    return tmp_path / "live" / "matches" / "m2_remittance.csv.gz"


class TestPrepare:
    def test_carga_solo_las_filas_con_partida(self, fichero):
        escribe(fichero, ["T1", "ARI-1", "100.00", "", "", "m2", "1", "A", "1"], ["T2", "", "", "BANK_FEES", "", "m2", "1", "A", "2"])
        engine = motor("live")

        m2_avisos.prepare(engine)

        assert set(engine.m2_rows) == {"T1"}

    def test_en_live_y_final_sin_fichero_para_el_motor(self, fichero):
        with pytest.raises(SystemExit) as parada:
            m2_avisos.prepare(motor("live"))

        assert "bankcheck match" in str(parada.value)

    def test_en_las_fases_de_evaluacion_sin_fichero_avisa_y_sigue(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("BANKREC_M2_DIR", str(tmp_path))
        engine = motor("sample")

        m2_avisos.prepare(engine)

        assert engine.m2_rows == {}
        assert "AVISO" in capsys.readouterr().out


class TestMatch:
    def preparado(self, fichero, *filas, **items) -> Engine:
        escribe(fichero, *filas)
        engine = motor("live", **items)
        m2_avisos.prepare(engine)
        return engine

    def test_emite_una_fila_por_partida_con_importe_y_excepcion(self, fichero):
        engine = self.preparado(
            fichero,
            ["T1", "ARI-1", "100.00", "", "", "m2", "1", "A", "1"],
            ["T1", "ARI-2", "50.50", "", "SHORT_DISPUTE", "m2", "1", "A", "2"],
            **{"ARI-1": factura("ARI-1", 10000), "ARI-2": factura("ARI-2", 9000)},
        )

        assert m2_avisos.match(engine, cobro()) is True
        assert [(r.item, r.amount, r.exception, r.rule) for r in engine.rows] == [
            ("ARI-1", 10000, "", "m2_avisos"),
            ("ARI-2", 5050, "SHORT_DISPUTE", "m2_avisos"),
        ]

    def test_gasta_el_saldo_para_que_otro_movimiento_no_la_vuelva_a_usar(self, fichero):
        item = factura("ARI-1", 10000)
        engine = self.preparado(fichero, ["T1", "ARI-1", "100.00", "", "", "m2", "1", "A", "1"], **{"ARI-1": item})

        m2_avisos.match(engine, cobro())

        assert item.open == 0

    def test_un_movimiento_sin_aviso_sigue_su_camino(self, fichero):
        engine = self.preparado(fichero, ["T1", "ARI-1", "100.00", "", "", "m2", "1", "A", "1"], **{"ARI-1": factura("ARI-1", 10000)})

        assert m2_avisos.match(engine, cobro("OTRO")) is False
        assert engine.rows == []

    def test_una_partida_que_el_motor_no_conoce_se_emite_igualmente(self, fichero):
        """El motor descarta partidas cerradas antiguas; el aviso las cita y el importe es del aviso, no del saldo."""
        engine = self.preparado(fichero, ["T1", "ARI-9", "100.00", "", "", "m2", "1", "A", "1"])

        assert m2_avisos.match(engine, cobro()) is True
        assert isinstance(engine.rows[0], Row)
        assert (engine.rows[0].item, engine.rows[0].amount) == ("ARI-9", 10000)
