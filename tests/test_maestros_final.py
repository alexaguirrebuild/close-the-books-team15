"""Maestros de FINAL: pueden no traer todos los ficheros, y los clientes que cambiaron de cuenta siguen pagando desde la vieja."""
import gzip
from pathlib import Path

import pytest

from bankrec import data as data_module
from bankrec import preflight

CUSTOMER_HEADER = "customer_id,name,trade_name,iban,country,parent_customer_id"
SUPPLIER_HEADER = "supplier_id,name,iban,country"
BANK_HEADER = (
    "bank_txn_id,account_id,entity,bank,statement_format,statement_file,booking_date,value_date,amount,currency,bank_code,"
    "bank_subcode,ref1,ref2,text,counterparty_name,counterparty_account,end_to_end_id,remittance_info,orig_amount,orig_currency"
)
OTHER_MASTERS = {
    "entities.csv": "entity,country\nES01,ES\n",
    "bank_accounts.csv": "account_id\nA1\n",
    "categories.csv": "category\nBANK_FEES\n",
    "payment_terms.csv": "code\nT30\n",
    "fx_rates.csv": "date,currency,units_per_eur\n2025-12-31,USD,1.10\n",
}


def write_masters(folder: Path, customers: list[str], suppliers: list[str], others: dict[str, str] = OTHER_MASTERS) -> None:
    folder.mkdir(parents=True)
    (folder / "customers.csv").write_text("\n".join([CUSTOMER_HEADER, *customers]) + "\n", encoding="utf-8")
    (folder / "suppliers.csv").write_text("\n".join([SUPPLIER_HEADER, *suppliers]) + "\n", encoding="utf-8")
    for name, content in others.items():
        (folder / name).write_text(content, encoding="utf-8")


@pytest.fixture
def base(tmp_path, monkeypatch):
    monkeypatch.setattr(data_module, "DATA", tmp_path)
    write_masters(tmp_path / "masters", ["C1,Cliente Uno,,ES00VIEJA,ES,", "C2,Cliente Dos,,ES00COMPARTIDA,ES,"], ["S1,Proveedor,ES00VIEJOPROV,ES"])
    for phase in ("final", "live"):
        (tmp_path / phase).mkdir()
        with gzip.open(tmp_path / phase / "bank_transactions.csv.gz", "wt", encoding="utf-8") as handle:
            handle.write(BANK_HEADER + "\n")
    return tmp_path


def final_masters(base: Path, others: dict[str, str] = OTHER_MASTERS) -> None:
    write_masters(
        base / "final" / "masters_final",
        ["C1,Cliente Uno,,ES00NUEVA,ES,", "C3,Cliente Tres,,ES00COMPARTIDA,ES,"],
        ["S1,Proveedor,ES00NUEVOPROV,ES"],
        others,
    )


class TestIbanAntiguos:
    def test_el_iban_nuevo_y_el_viejo_llevan_al_mismo_cliente(self, base):
        final_masters(base)

        phase = data_module.Phase("final")

        assert phase.party_by_iban["ES00NUEVA"] == "C1"
        assert phase.party_by_iban["ES00VIEJA"] == "C1"

    def test_tambien_vale_para_proveedores(self, base):
        final_masters(base)

        phase = data_module.Phase("final")

        assert phase.party_by_iban["ES00NUEVOPROV"] == "S1"
        assert phase.party_by_iban["ES00VIEJOPROV"] == "S1"

    def test_si_el_iban_ya_es_de_otro_manda_el_maestro_nuevo(self, base):
        final_masters(base)

        assert data_module.Phase("final").party_by_iban["ES00COMPARTIDA"] == "C3"

    def test_sin_masters_final_no_cambia_nada(self, base):
        phase = data_module.Phase("live")

        assert phase.party_by_iban == {"ES00VIEJA": "C1", "ES00COMPARTIDA": "C2", "ES00VIEJOPROV": "S1"}


class TestMaestrosIncompletos:
    def test_un_maestro_que_falta_en_masters_final_se_toma_del_anterior(self, base):
        final_masters(base, {"bank_accounts.csv": "account_id\nA1\nA2\n"})

        phase = data_module.Phase("final")

        assert set(phase.accounts) == {"A1", "A2"}
        assert phase.categories == ["BANK_FEES"]
        assert list(phase.terms) == ["T30"]
        assert phase.fx.rate("USD", phase.fx.dates["USD"][0]) == 1.10

    def test_lo_que_trae_masters_final_tiene_prioridad(self, base):
        final_masters(base, {**OTHER_MASTERS, "categories.csv": "category\nNUEVA\n"})

        assert data_module.Phase("final").categories == ["NUEVA"]

    def test_masters_final_no_afecta_a_otras_fases(self, base):
        final_masters(base, {**OTHER_MASTERS, "categories.csv": "category\nNUEVA\n"})

        assert data_module.Phase("live").categories == ["BANK_FEES"]

    def test_sin_entities_en_masters_final_el_motor_arranca_con_el_anterior(self, base):
        final_masters(base, {"bank_accounts.csv": "account_id\nA1\n"})

        assert data_module.Phase("final").entity_country == {"ES01": "ES"}

    def test_entities_de_masters_final_trae_la_filial_nueva(self, base):
        final_masters(base, {**OTHER_MASTERS, "entities.csv": "entity,country\nES01,ES\nZZ09,ZA\n"})

        assert data_module.Phase("final").entity_country["ZZ09"] == "ZA"


class TestAvisoDePreflight:
    @staticmethod
    def fase_completa(base: Path) -> None:
        (base / "final" / "open_items_at_start.csv.gz").touch()
        for prior in ("train", "live"):
            (base / prior).mkdir(exist_ok=True)
            (base / prior / "ar_invoices.csv.gz").touch()

    def test_avisa_de_cada_maestro_que_falta_sin_parar(self, base, capsys):
        self.fase_completa(base)
        final_masters(base, {"bank_accounts.csv": "account_id\nA1\n"})

        preflight.check_phase_inputs("final", base)

        salida = capsys.readouterr().out
        assert "AVISO" in salida
        assert "entities.csv" in salida and "fx_rates.csv" in salida
        assert "customers.csv" not in salida and "bank_accounts.csv" not in salida

    def test_con_todos_los_maestros_no_avisa(self, base, capsys):
        self.fase_completa(base)
        final_masters(base)

        preflight.check_phase_inputs("final", base)

        assert "AVISO" not in capsys.readouterr().out

    def test_sin_la_carpeta_masters_final_sigue_parando(self, base):
        self.fase_completa(base)

        with pytest.raises(SystemExit, match="masters_final"):
            preflight.check_phase_inputs("final", base)
