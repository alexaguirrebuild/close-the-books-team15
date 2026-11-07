"""Las claves de fichero tienen la forma de index.csv («AU01/RA_x.csv») también en Windows.

Con os.path.relpath, Windows devuelve «AU01\\RA_x.csv»: ningún aviso ni PDF cruzaba con su índice, cada aviso
salía dos veces (uno en el índice sin fichero y otro en disco sin índice) y bankcheck match enlazaba 0 avisos.
"""
from pathlib import Path

from bankcheck import pdf_index
from bankcheck.remittance import load
from bankcheck.roots import rel_key


def _arbol(tmp_path: Path) -> Path:
    (tmp_path / "AU01").mkdir()
    (tmp_path / "AU01" / "RA_20250101_X_1.csv").write_text("x", encoding="utf-8")
    (tmp_path / "AU01" / "INV_1.pdf").write_text("x", encoding="utf-8")
    return tmp_path


def test_rel_key_usa_barra_normal(tmp_path):
    assert rel_key(tmp_path / "AU01" / "a.csv", tmp_path) == "AU01/a.csv"


def test_avisos_en_disco_con_la_clave_del_indice(tmp_path):
    assert sorted(load._files_on_disk(_arbol(tmp_path))) == ["AU01/INV_1.pdf", "AU01/RA_20250101_X_1.csv"]


def test_pdfs_en_disco_con_la_clave_del_indice(tmp_path):
    assert list(pdf_index._pdfs_on_disk(_arbol(tmp_path))) == ["AU01/INV_1.pdf"]
