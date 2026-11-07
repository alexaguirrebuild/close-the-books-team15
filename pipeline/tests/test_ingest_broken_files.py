"""A broken file is that file's problem: it is reported by name, and the rest of the phase still loads."""
import gzip
import io
import json
import random
import zipfile
from pathlib import Path

import openpyxl
import pytest

from bankrec.csvload import CsvSpec, load_csv
from bankrec.db import IngestContext, building_path, open_build
from bankrec.ingest import run_ingest
from bankrec.issues import IssueLog, Manifest
from bankrec.roots import IndexUnreadable, read_file_index
from test_ingest import AR_HEADER, gz, layout_of, plain, query, root  # noqa: F401  (root is a fixture)

REMITTANCE_INDEX_HEADER = "file,entity,payer_name,received_date,format"
REMITTANCE_ROW = "UK01/RA_1.csv,UK01,Cliente,2025-07-02,csv"
PDF_INDEX_HEADER = "file,document_type,document_number,entity,party_id,document_date"
PDF_ROW = "UK01/ARI_FV251.pdf,invoice,FV25-1,UK01,CUK01000001,2025-07-01"
ROWS_BEYOND_THE_GZIP_BUFFER = 20_000
TRUNCATED_SHARE = 0.6


def files_with(root_path: Path, code: str) -> list[str]:
    return [row[0] for row in query(root_path, f"SELECT file FROM ingest_issue WHERE code = '{code}' ORDER BY 1")]


def add_advice(root_path: Path, name: str, content: bytes) -> str:
    folder = root_path / "live" / "remittance_advices"
    (folder / "UK01" / name).write_bytes(content)
    plain(folder / "index.csv", REMITTANCE_INDEX_HEADER, REMITTANCE_ROW, f"UK01/{name},UK01,Otro,2025-07-03,{Path(name).suffix[1:]}")
    return f"live/remittance_advices/UK01/{name}"


def xlsx_with_part_replaced(part: str, content: bytes) -> bytes:
    """A workbook openpyxl really wrote, with one of its internal parts damaged."""
    valid = io.BytesIO()
    openpyxl.Workbook().save(valid)
    damaged = io.BytesIO()
    with zipfile.ZipFile(valid) as source, zipfile.ZipFile(damaged, "w") as target:
        for name in source.namelist():
            target.writestr(name, content if name == part else source.read(name))
    return damaged.getvalue()


def zip_without_workbook() -> bytes:
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr("hola.txt", "no soy un libro")
    return content.getvalue()


def write_many_invoices(path: Path) -> None:
    rng = random.Random(7)
    rows = (
        f"ARI-2025-{index:07d},UK01,FV{rng.randrange(10**9)},2025-07-01,2025-07-31,CUK01000001,Cliente {rng.randrange(10**9)},GBP,100.00,0.00,100.00,T30,,transfer"
        for index in range(ROWS_BEYOND_THE_GZIP_BUFFER)
    )
    gz(path, AR_HEADER, *rows)


def truncate(path: Path) -> None:
    data = path.read_bytes()
    path.write_bytes(data[: int(len(data) * TRUNCATED_SHARE)])


class TestBrokenAdvices:
    @pytest.mark.parametrize(
        ("name", "content"),
        [
            ("RA_2.pdf", b"esto no es un pdf"),
            ("RA_2.pdf", b""),
            ("RA_2.pdf", b"%PDF-1.7\ntruncado"),
            ("RA_2.xlsx", b"esto no es un zip"),
            ("RA_2.xlsx", b""),
            ("RA_2.xlsx", zip_without_workbook()),
            ("RA_2.xlsx", xlsx_with_part_replaced("xl/workbook.xml", b"<workbook")),
            ("RA_2.xlsx", xlsx_with_part_replaced("xl/worksheets/sheet1.xml", b"<worksheet")),
            ("RA_2.xlsx", xlsx_with_part_replaced("[Content_Types].xml", b"<Types")),
            ("RA_2.xls", b"\xd0\xcf\x11\xe0 formato antiguo de Excel"),
            ("RA_2.zip", zip_without_workbook()),
            ("RA_2.csv", "cañón".encode("latin-1")),
        ],
        ids=[
            "pdf_basura", "pdf_vacio", "pdf_truncado", "xlsx_no_zip", "xlsx_vacio", "xlsx_sin_libro", "xlsx_workbook_roto",
            "xlsx_hoja_rota", "xlsx_content_types_roto", "xls_antiguo", "zip_entre_avisos", "csv_latin1",
        ],
    )
    def test_is_reported_by_name_and_the_rest_still_loads(self, root: Path, name: str, content: bytes) -> None:
        expected = add_advice(root, name, content)

        result = run_ingest(layout_of(root), allow_errors=True)

        assert result.ok is False
        assert files_with(root, "REMITTANCE_PARSE_ERROR") == [expected]
        assert query(root, "SELECT count(*) FROM remittance_line") == [(1,)]
        assert query(root, f"SELECT control_status FROM remittance_advice WHERE advice_id = 'UK01/{name}'") == [("error",)]

    def test_is_not_also_reported_as_a_file_nobody_read(self, root: Path) -> None:
        add_advice(root, "RA_2.pdf", b"esto no es un pdf")

        run_ingest(layout_of(root), allow_errors=True)

        assert files_with(root, "FILE_NOT_READ") == []

    @pytest.mark.parametrize("amount", ["NaN", "Infinity", "-Infinity", "1E+30", "12345678901234567890"])
    def test_an_amount_that_cannot_be_stored_is_reported_by_name(self, root: Path, amount: str) -> None:
        content = "\n".join(
            [
                "payment_ref,2,payment_date,2025-07-01,currency,GBP",
                "document,doc_date,gross,discount_or_deduction,reason,paid,your_order",
                f"FV25-2,01/07/2025,{amount},,,{amount},",
                "",
            ]
        )
        expected = add_advice(root, "RA_2.csv", content.encode("utf-8"))

        result = run_ingest(layout_of(root), allow_errors=True)

        assert result.ok is False
        assert files_with(root, "REMITTANCE_PARSE_ERROR") == [expected]
        assert query(root, "SELECT count(*) FROM remittance_line") == [(1,)]

    def test_a_broken_link_is_reported_by_name(self, root: Path) -> None:
        expected = add_advice(root, "RA_2.pdf", b"")
        link = root / expected
        link.unlink()
        link.symlink_to(link.parent / "no_existe.pdf")

        result = run_ingest(layout_of(root), allow_errors=True)

        assert result.ok is False
        assert files_with(root, "REMITTANCE_PARSE_ERROR") == [expected]
        assert files_with(root, "FILE_NOT_READ") == []


class TestBrokenStatements:
    @staticmethod
    def n43_with_date(day: str) -> bytes:
        records = ("11" + "0" * 45 + "826", f"22{' ' * 8}{day}{day}01001200000000010000")
        return "".join(f"{record.ljust(80)}\r\n" for record in records).encode("latin-1")

    @staticmethod
    def camt_with_date(day: str) -> bytes:
        return (
            '<?xml version="1.0"?><Document xmlns="urn:x"><BkToCstmrStmt><Stmt><Ntry><Amt Ccy="GBP">1.00</Amt>'
            f"<CdtDbtInd>CRDT</CdtDbtInd><BookgDt><Dt>{day}</Dt></BookgDt></Ntry></Stmt></BkToCstmrStmt></Document>"
        ).encode("utf-8")

    @pytest.mark.parametrize(
        ("suffix", "content"),
        [("n43", n43_with_date("251345")), ("xml", camt_with_date("31/12/2025"))],
        ids=["n43", "camt"],
    )
    def test_an_impossible_date_is_reported_by_name_and_the_rest_loads(self, root: Path, suffix: str, content: bytes) -> None:
        name = f"live/bank_statements/UK01-NORT-GBP/UK01-NORT-GBP_202508.{suffix}"
        (root / name).write_bytes(content)

        result = run_ingest(layout_of(root), allow_errors=True)

        assert result.ok is False
        assert files_with(root, "STATEMENT_PARSE_ERROR") == [name]
        assert files_with(root, "FILE_NOT_READ") == []
        assert query(root, "SELECT count(*) FROM bank_statement_line") == [(2,)]


class TestFileIndexes:
    @pytest.mark.parametrize(
        ("folder", "lines"),
        [("remittance_advices", (REMITTANCE_INDEX_HEADER, REMITTANCE_ROW)), ("pdfs", (PDF_INDEX_HEADER, PDF_ROW))],
    )
    def test_a_bom_changes_nothing(self, root: Path, folder: str, lines: tuple[str, str]) -> None:
        (root / "live" / folder / "index.csv").write_bytes(b"\xef\xbb\xbf" + "\n".join(lines).encode("utf-8") + b"\n")

        result = run_ingest(layout_of(root))

        assert result.ok is True
        assert query(root, "SELECT count(*) FROM remittance_advice WHERE payer_name = 'Cliente'") == [(1,)]
        assert query(root, "SELECT count(*) FROM pdf_index WHERE document_norm = 'FV251'") == [(1,)]

    @pytest.mark.parametrize(
        ("folder", "content"),
        [
            ("remittance_advices", "archivo,entity\nUK01/RA_1.csv,UK01\n".encode()),
            ("pdfs", "archivo,entity\nUK01/ARI_FV251.pdf,UK01\n".encode()),
            ("remittance_advices", b""),
            ("pdfs", b""),
            ("remittance_advices", "file,entity\nUK01/cañón.csv,UK01\n".encode("latin-1")),
            ("pdfs", "file,entity\nUK01/cañón.pdf,UK01\n".encode("latin-1")),
        ],
        ids=["avisos_sin_columna", "pdfs_sin_columna", "avisos_vacio", "pdfs_vacio", "avisos_latin1", "pdfs_latin1"],
    )
    def test_an_unusable_index_is_reported_with_its_file(self, root: Path, folder: str, content: bytes) -> None:
        (root / "live" / folder / "index.csv").write_bytes(content)

        result = run_ingest(layout_of(root), allow_errors=True)

        assert result.ok is False
        assert files_with(root, "FILE_UNREADABLE") == [f"live/{folder}/index.csv"]
        assert files_with(root, "FILE_NOT_READ") == []

    def test_an_unusable_advice_index_is_not_reported_as_missing(self, root: Path) -> None:
        (root / "live" / "remittance_advices" / "index.csv").write_text("archivo,entity\n", encoding="utf-8")

        run_ingest(layout_of(root), allow_errors=True)

        assert files_with(root, "MISSING_FILE") == []

    def test_a_missing_advice_index_is_still_reported_as_missing(self, root: Path) -> None:
        (root / "live" / "remittance_advices" / "index.csv").unlink()

        run_ingest(layout_of(root), allow_errors=True)

        assert files_with(root, "MISSING_FILE") == ["live/remittance_advices/index.csv"]

    def test_read_file_index_explains_what_is_wrong(self, tmp_path: Path) -> None:
        path = tmp_path / "index.csv"
        path.write_text("archivo,entity\n", encoding="utf-8")

        with pytest.raises(IndexUnreadable, match="file"):
            read_file_index(path)

    def test_a_row_without_a_file_name_is_reported_and_the_rest_loads(self, root: Path) -> None:
        plain(
            root / "live" / "remittance_advices" / "index.csv",
            "entity,file,payer_name,received_date,format",
            "UK01,UK01/RA_1.csv,Cliente,2025-07-02,csv",
            "UK01",
            "UK01,,Otro,2025-07-03,csv",
        )

        result = run_ingest(layout_of(root), allow_errors=True)

        assert result.ok is False
        assert query(root, "SELECT file, count FROM ingest_issue WHERE code = 'BAD_VALUE'") == [("live/remittance_advices/index.csv", 2)]
        assert query(root, "SELECT advice_id, payer_name FROM remittance_advice") == [("UK01/RA_1.csv", "Cliente")]

    def test_a_bad_date_in_the_advice_index_is_reported_and_blanked(self, root: Path) -> None:
        plain(root / "live" / "remittance_advices" / "index.csv", REMITTANCE_INDEX_HEADER, "UK01/RA_1.csv,UK01,Cliente,julio de 2025,csv")

        result = run_ingest(layout_of(root), allow_errors=True)

        assert result.ok is False
        assert files_with(root, "BAD_VALUE") == ["live/remittance_advices/index.csv"]
        assert query(root, "SELECT received_date, month, payer_name FROM remittance_advice") == [(None, None, "Cliente")]

    def test_a_bad_date_in_the_pdf_index_is_reported_and_blanked(self, root: Path) -> None:
        plain(root / "live" / "pdfs" / "index.csv", PDF_INDEX_HEADER, "UK01/ARI_FV251.pdf,invoice,FV25-1,UK01,CUK01000001,2025-13-45")

        result = run_ingest(layout_of(root), allow_errors=True)

        assert result.ok is False
        assert files_with(root, "BAD_VALUE") == ["live/pdfs/index.csv"]
        assert query(root, "SELECT document_date, month, document_norm FROM pdf_index") == [(None, None, "FV251")]


class TestUnreadableTables:
    def test_a_truncated_gzip_with_a_readable_header_is_reported_not_half_loaded(self, root: Path) -> None:
        path = root / "live" / "ar_invoices.csv.gz"
        write_many_invoices(path)
        truncate(path)

        result = run_ingest(layout_of(root), allow_errors=True)

        assert result.ok is False
        assert files_with(root, "FILE_UNREADABLE") == ["live/ar_invoices.csv.gz"]
        assert query(root, "SELECT count(*) FROM ar_invoice") == [(0,)]
        assert query(root, "SELECT count(*) FROM ap_invoice") == [(1,)]

    @pytest.mark.parametrize(
        ("content", "code"),
        [
            (b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03" + bytes(range(256)) * 8, "FILE_UNREADABLE"),
            (b"esto no es gzip", "FILE_UNREADABLE"),
            (b"", "BAD_HEADER"),
        ],
        ids=["deflate_roto", "no_es_gzip", "vacio"],
    )
    def test_a_damaged_gzip_is_reported_with_its_file(self, root: Path, content: bytes, code: str) -> None:
        (root / "live" / "ar_invoices.csv.gz").write_bytes(content)

        result = run_ingest(layout_of(root), allow_errors=True)

        assert result.ok is False
        assert files_with(root, code) == ["live/ar_invoices.csv.gz"]

    def test_a_gzip_whose_second_member_is_truncated_is_reported(self, root: Path) -> None:
        path = root / "live" / "ar_invoices.csv.gz"
        second = gzip.compress("".join(f"ARI-2025-{index:07d},{random.Random(index).random()}\n" for index in range(ROWS_BEYOND_THE_GZIP_BUFFER)).encode("utf-8"))
        path.write_bytes(gzip.compress(f"{AR_HEADER}\n".encode("utf-8")) + second[: len(second) // 2])

        result = run_ingest(layout_of(root), allow_errors=True)

        assert result.ok is False
        assert files_with(root, "FILE_UNREADABLE") == ["live/ar_invoices.csv.gz"]

    def test_one_bad_file_among_many_is_named_and_the_others_load(self, root: Path, tmp_path: Path) -> None:
        ctx = IngestContext(layout_of(root), open_build(tmp_path / "t.duckdb"), IssueLog(), Manifest())
        spec = CsvSpec("t", "test", ("a", "b"))
        first, broken, last = tmp_path / "1.csv.gz", tmp_path / "2.csv.gz", tmp_path / "3.csv.gz"
        gz(first, "a,b", "1,x")
        rng = random.Random(1)
        gz(broken, "a,b", *(f"{rng.randrange(10**12)},{rng.randrange(10**12)}" for _ in range(ROWS_BEYOND_THE_GZIP_BUFFER)))
        truncate(broken)
        gz(last, "a,b", "3,z")

        rows = load_csv(ctx, spec, [first, broken, last])

        assert rows == 2
        assert ctx.con.execute("SELECT a FROM t ORDER BY a").fetchall() == [("1",), ("3",)]
        assert [(issue.code, Path(issue.file).name) for issue in ctx.issues.items] == [("FILE_UNREADABLE", "2.csv.gz")]
        assert {record.status for record in ctx.manifest.files if Path(record.path).name == "2.csv.gz"} == {"error"}


class TestFxCoverage:
    @staticmethod
    def rates_until(root_path: Path, last_date: str) -> None:
        plain(root_path / "masters" / "fx_rates.csv", "date,currency,units_per_eur,source", f"{last_date},GBP,0.85,ECB")

    def test_rates_ending_well_before_the_phase_end_are_a_warning(self, root: Path) -> None:
        self.rates_until(root, "2025-06-30")

        result = run_ingest(layout_of(root))

        assert result.ok is True
        rows = query(root, "SELECT severity, detail, count FROM ingest_issue WHERE code = 'FX_RATES_STALE'")
        assert len(rows) == 1
        assert rows[0][0] == "WARN"
        assert "GBP" in rows[0][1] and "2025-06-30" in rows[0][1]

    def test_rates_ending_on_the_last_business_days_are_fine(self, root: Path) -> None:
        self.rates_until(root, "2025-12-29")

        run_ingest(layout_of(root))

        assert query(root, "SELECT count(*) FROM ingest_issue WHERE code = 'FX_RATES_STALE'") == [(0,)]

    def test_a_currency_nobody_uses_is_not_flagged(self, root: Path) -> None:
        plain(root / "masters" / "fx_rates.csv", "date,currency,units_per_eur,source", "2025-12-31,GBP,0.85,ECB", "2021-01-04,BRL,6.30,ECB")

        run_ingest(layout_of(root))

        assert query(root, "SELECT count(*) FROM ingest_issue WHERE code = 'FX_RATES_STALE'") == [(0,)]

    def test_a_blank_currency_does_not_hide_one_without_rates(self, root: Path) -> None:
        plain(root / "masters" / "fx_rates.csv", "date,currency,units_per_eur,source", "2025-12-31,USD,1.10,ECB", "2025-12-31,,1.00,ECB")

        run_ingest(layout_of(root))

        assert query(root, "SELECT count(*) FROM ingest_issue WHERE code = 'CURRENCY_WITHOUT_RATE'") == [(1,)]


class TestAbortedRun:
    def test_leaves_no_old_report_passing_as_good(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        layout = layout_of(root)
        run_ingest(layout)
        report = layout.report_dir / "ingest_report.json"
        assert json.loads(report.read_text(encoding="utf-8"))["published"] is True

        def explode(_ctx: IngestContext) -> None:
            raise RuntimeError("fallo inesperado")

        monkeypatch.setattr("bankrec.ingest.STEPS", (("Roto", explode),))
        with pytest.raises(RuntimeError):
            run_ingest(layout)

        assert not report.exists()
        assert not building_path(layout.warehouse_path).exists()

    def test_logs_what_stopped_it(self, root: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
        def explode(_ctx: IngestContext) -> None:
            raise RuntimeError("fallo inesperado")

        monkeypatch.setattr("bankrec.ingest.STEPS", (("Roto", explode),))
        with caplog.at_level("ERROR", logger="bankrec"), pytest.raises(RuntimeError):
            run_ingest(layout_of(root))

        assert "fallo inesperado" in caplog.text

    def test_a_clean_run_writes_the_report_again(self, root: Path) -> None:
        layout = layout_of(root)

        run_ingest(layout)

        assert (layout.report_dir / "ingest_report.json").exists()


def test_gzip_helper_really_truncates(tmp_path: Path) -> None:
    path = tmp_path / "a.csv.gz"
    write_many_invoices(path)
    truncate(path)

    with gzip.open(path, "rt", encoding="utf-8") as handle:
        assert handle.readline().startswith("item_id,")
        with pytest.raises(EOFError):
            handle.read()
