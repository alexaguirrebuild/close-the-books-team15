"""End to end over a tiny copy of the official layout: isolation, golden, idempotence and visible errors."""
import gzip
import json
import shutil
from pathlib import Path

import duckdb
import pytest

from bankrec.csvload import CsvSpec, load_csv
from bankrec.db import IngestContext, bulk_insert, open_build
from bankrec.ingest import run_ingest
from bankrec.issues import IssueLog, Manifest
from bankrec.pdf_index import pdf_text
from bankrec.roots import GoldenAccessError, resolve_layout

DOCS = Path(__file__).resolve().parents[2] / "bankrec-docs-and-masters"
MASTERS = DOCS / "masters"
SAMPLE_PDF = next((DOCS / "sample" / "remittance_advices").glob("*/*.pdf"), None) if (DOCS / "sample").is_dir() else None
BANK_HEADER = (
    "bank_txn_id,account_id,entity,bank,statement_format,statement_file,booking_date,value_date,amount,currency,bank_code,"
    "bank_subcode,ref1,ref2,text,counterparty_name,counterparty_account,end_to_end_id,remittance_info,orig_amount,orig_currency"
)
AR_HEADER = "item_id,entity,document_number,issue_date,due_date,customer_id,customer_name,currency,net_amount,tax_amount,total_amount,payment_terms,customer_po,payment_method"
AP_HEADER = "item_id,entity,supplier_id,supplier_name,supplier_invoice_number,issue_date,due_date,currency,net_amount,tax_amount,total_amount,payment_terms,our_po,payment_method"
CN_HEADER = "item_id,side,entity,document_number,issue_date,party_id,party_name,currency,net_amount,tax_amount,total_amount,related_document,reason"
OPEN_HEADER = "item_id,item_type,entity,party_id,document_number,issue_date,due_date,currency,total_amount,open_amount"
PR_HEADER = "run_id,run_type,execution_date,debtor_account_id,end_to_end_id,beneficiary,beneficiary_iban,currency,transfer_amount,document_number,document_type,document_amount,remittance_info"
DD_HEADER = "batch_id,scheme,collection_date,account_id,currency,batch_total,end_to_end_id,mandate_id,debtor_name,debtor_iban,amount,remittance_info,re_presentation"
CARD_HEADER = "settlement_id,acquirer,merchant_id,settlement_date,account_id,currency,order_number,card,auth_code,operation_date,gross,fee,net"
CHEQUE_HEADER = "deposit_id,deposit_date,account_id,currency,deposit_total,cheque_number,drawer,drawer_bank_routing,amount,stub_reference"

ACCOUNT = "UK01-NORT-GBP"
STATEMENT = "\r\n".join(
    [
        "{1:F01NORTGB2LAXXX0000000000}{2:O940}{4:",
        ":20:STMT",
        ":25:GB32",
        ":60F:C250701GBP0,00",
        ":61:2507010701C100,00NTRFNONREF//NO1",
        ":86:PAGO FV25-1",
        ":61:2507010701C100,00NTRFNONREF//NO2",
        ":86:PAGO FV25-2",
        ":62F:C250731GBP200,00",
        "-}",
    ]
)


def gz(path: Path, *lines: str) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def plain(path: Path, *lines: str) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def bank_row(txn: str, ref2: str, text: str) -> str:
    return f"{txn},{ACCOUNT},UK01,NORT,mt940,live/bank_statements/{ACCOUNT}/{ACCOUNT}_202507.sta,2025-07-01,2025-07-01,100.00,GBP,NTRF,,NONREF,{ref2},{text},,,,,,"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    if not MASTERS.is_dir() or SAMPLE_PDF is None:
        pytest.skip("hacen falta los maestros y la muestra")
    base = tmp_path / "repo"
    live = base / "live"
    shutil.copytree(MASTERS, base / "masters")
    for folder in ("payment_runs", "direct_debit_batches", "card_settlements", "cheque_deposits", "pdfs", "remittance_advices/UK01", f"bank_statements/{ACCOUNT}"):
        (live / folder).mkdir(parents=True)
    gz(live / "bank_transactions.csv.gz", BANK_HEADER, bank_row("BT-2025-0000001", "NO1", "PAGO FV25-1"), bank_row("BT-2025-0000002", "NO2", "PAGO FV25-2"))
    gz(live / "ar_invoices.csv.gz", AR_HEADER, "ARI-2025-0000001,UK01,FV25-1,2025-07-01,2025-07-31,CUK01000001,Cliente,GBP,100.00,0.00,100.00,T30,,transfer")
    gz(live / "ap_invoices.csv.gz", AP_HEADER, "API-2025-0000001,UK01,SUK01000001,Proveedor,P-1,2025-07-01,2025-07-31,GBP,50.00,0.00,50.00,T30,,transfer")
    gz(live / "credit_notes.csv.gz", CN_HEADER, "ARC-2025-0000001,AR,UK01,AB-1,2025-07-01,CUK01000001,Cliente,GBP,5.00,0.00,5.00,FV25-1,Devolución")
    gz(live / "open_items_at_start.csv.gz", OPEN_HEADER, "ARI-2024-0000009,ARI,UK01,CUK01000001,FV24-9,2024-12-01,2025-01-01,GBP,70.00,30.00")
    plain(live / "payment_runs" / "UK01_202507_0.csv", PR_HEADER, f"R1,transfers,2025-07-02,{ACCOUNT},R1-0001,Proveedor,,GBP,50.00,P-1,INVOICE,50.00,FRA P-1")
    plain(live / "direct_debit_batches" / "UK01_LON_202507.csv", DD_HEADER, f"B1,BACS,2025-07-02,{ACCOUNT},GBP,10.00,B1-1,M1,Cliente,,10.00,FV25-1,N")
    plain(live / "card_settlements" / "UK01_202507.csv", CARD_HEADER, f"S1,PayNexo,1,2025-07-02,{ACCOUNT},GBP,WEB1,****1,1,2025-07-01,10.00,0.10,9.90")
    plain(live / "cheque_deposits" / "UK01_LON_202507.csv", CHEQUE_HEADER, f"D1,2025-07-02,{ACCOUNT},GBP,10.00,1,Cliente,1,10.00,FV25-1")
    plain(live / "pdfs" / "index.csv", "file,document_type,document_number,entity,party_id,document_date", "UK01/ARI_FV251.pdf,invoice,FV25-1,UK01,CUK01000001,2025-07-01")
    (live / "pdfs" / "UK01").mkdir()
    shutil.copy(SAMPLE_PDF, live / "pdfs" / "UK01" / "ARI_FV251.pdf")
    plain(live / "remittance_advices" / "index.csv", "file,entity,payer_name,received_date,format", "UK01/RA_1.csv,UK01,Cliente,2025-07-02,csv")
    plain(
        live / "remittance_advices" / "UK01" / "RA_1.csv",
        "payment_ref,1,payment_date,2025-07-01,currency,GBP",
        "document,doc_date,gross,discount_or_deduction,reason,paid,your_order",
        "FV25-1,01/07/2025,100.00,,,100.00,",
        "TOTAL,,,,,100.00,",
    )
    (live / "bank_statements" / ACCOUNT / f"{ACCOUNT}_202507.sta").write_text(STATEMENT, encoding="utf-8")
    return base


def layout_of(root: Path, phase: str = "live"):
    return resolve_layout(phase, data_root=root)


def query(root: Path, sql: str, phase: str = "live"):
    con = duckdb.connect(str(root / "cache" / "warehouse" / f"{phase}.duckdb"), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


class TestIngest:
    def test_clean_phase_loads_everything(self, root: Path) -> None:
        result = run_ingest(layout_of(root))

        assert result.ok is True
        assert query(root, "SELECT count(*) FROM bank_txn") == [(2,)]
        assert query(root, "SELECT DISTINCT raw_status FROM bank_txn") == [("matched",)]
        assert query(root, "SELECT status, count(*) FROM xval_account_month GROUP BY 1") == [("OK", 1)]

    def test_item_unifies_invoices_credit_notes_and_opening_balances(self, root: Path) -> None:
        run_ingest(layout_of(root))

        rows = dict(query(root, "SELECT item_id, open_amount FROM item"))
        assert len(rows) == 4
        assert float(rows["ARI-2024-0000009"]) == 30.00
        assert query(root, "SELECT printed_norm, is_opening FROM item WHERE item_id = 'ARI-2024-0000009'") == [("FV249", True)]
        assert query(root, "SELECT printed_norm FROM item WHERE item_id = 'API-2025-0000001'") == [("P1",)]

    def test_second_run_gives_identical_fingerprints(self, root: Path) -> None:
        first = run_ingest(layout_of(root)).report["fingerprint"]
        second = run_ingest(layout_of(root)).report["fingerprint"]

        assert first == second
        assert query(root, "SELECT count(*) FROM bank_txn") == [(2,)]

    def test_records_carry_provenance(self, root: Path) -> None:
        run_ingest(layout_of(root))

        assert query(root, "SELECT src_file, bank, account_id, month FROM bank_txn LIMIT 1") == [("live/bank_transactions.csv.gz", "NORT", ACCOUNT, "2025-07")]
        assert query(root, "SELECT src_file, src_line, bank, account_id, month FROM bank_statement_line ORDER BY src_line LIMIT 1") == [
            (f"live/bank_statements/{ACCOUNT}/{ACCOUNT}_202507.sta", 5, "NORT", ACCOUNT, "2025-07")
        ]
        assert query(root, "SELECT bank, account_id, month, entity FROM dd_batch_line") == [("NORT", ACCOUNT, "2025-07", "UK01")]

    def test_only_reads_the_selected_phase(self, root: Path) -> None:
        (root / "train").mkdir()
        gz(root / "train" / "bank_transactions.csv.gz", BANK_HEADER, bank_row("BT-2021-0000099", "NO9", "OTRA FASE"))

        run_ingest(layout_of(root))

        assert query(root, "SELECT count(*) FROM bank_txn WHERE bank_txn_id = 'BT-2021-0000099'") == [(0,)]
        outside = query(root, "SELECT count(*) FROM file_manifest WHERE NOT (path LIKE 'live/%' OR path LIKE 'masters/%')")
        assert outside == [(0,)]

    def test_golden_never_enters_the_warehouse(self, root: Path) -> None:
        gz(root / "live" / "golden_allocations.csv.gz", "bank_txn_id,item_id,amount,category,exception,segment,difficulty", "BT-2025-0000001,ARI-2025-0000001,100.00,,,ar_1to1,1")

        run_ingest(layout_of(root))

        assert query(root, "SELECT count(*) FROM file_manifest WHERE path ILIKE '%golden%'") == [(0,)]
        assert query(root, "SELECT count(*) FROM duckdb_tables() WHERE table_name ILIKE '%golden%'") == [(0,)]
        control_tables = "('file_manifest', 'ingest_issue', 'source_summary')"
        assert query(root, f"SELECT count(*) FROM duckdb_columns() WHERE column_name IN ('segment', 'difficulty') AND table_name NOT IN {control_tables}") == [(0,)]

    def test_a_raw_line_missing_from_the_view_is_an_error_not_silence(self, root: Path) -> None:
        gz(root / "live" / "bank_transactions.csv.gz", BANK_HEADER, bank_row("BT-2025-0000001", "NO1", "PAGO FV25-1"))

        result = run_ingest(layout_of(root), allow_errors=True)

        assert result.ok is False
        assert query(root, "SELECT count(*) FROM ingest_issue WHERE code = 'RAW_ONLY_LINE'") == [(1,)]
        assert query(root, "SELECT count(*) FROM bank_statement_line") == [(2,)]
        assert query(root, "SELECT status FROM xval_account_month") == [("DIFF",)]

    def test_overlapping_exports_are_unified(self, root: Path) -> None:
        second = STATEMENT.replace("C250731GBP200,00", "C250731GBP200,00")
        (root / "live" / "bank_statements" / ACCOUNT / f"{ACCOUNT}_202508.sta").write_text(second, encoding="utf-8")

        result = run_ingest(layout_of(root))

        assert result.ok is True
        assert query(root, "SELECT count(*), count(*) FILTER (WHERE is_overlap_dup) FROM bank_statement_line") == [(4, 2)]
        assert query(root, "SELECT count(*) FROM bank_txn") == [(2,)]
        assert query(root, "SELECT raw_files FROM bank_txn LIMIT 1")[0][0] == [
            f"live/bank_statements/{ACCOUNT}/{ACCOUNT}_202507.sta",
            f"live/bank_statements/{ACCOUNT}/{ACCOUNT}_202508.sta",
        ]

    def test_a_broken_advice_is_reported_and_the_rest_still_loads(self, root: Path) -> None:
        folder = root / "live" / "remittance_advices"
        plain(folder / "UK01" / "RA_2.csv", "esto no es un aviso")
        plain(folder / "index.csv", "file,entity,payer_name,received_date,format", "UK01/RA_1.csv,UK01,Cliente,2025-07-02,csv", "UK01/RA_2.csv,UK01,Otro,2025-07-03,csv")

        result = run_ingest(layout_of(root), allow_errors=True)

        assert result.ok is False
        assert query(root, "SELECT file FROM ingest_issue WHERE code = 'REMITTANCE_PARSE_ERROR'") == [("live/remittance_advices/UK01/RA_2.csv",)]
        assert query(root, "SELECT count(*) FROM remittance_line") == [(1,)]

    def test_alerts_about_unknown_ids(self, root: Path) -> None:
        gz(root / "live" / "ar_invoices.csv.gz", AR_HEADER, "ARI-2025-0000001,UK01,FV25-1,2025-07-01,2025-07-31,CNOEXISTE,Cliente,GBP,100.00,0.00,100.00,T30,,transfer")

        run_ingest(layout_of(root))

        assert query(root, "SELECT count FROM ingest_issue WHERE code = 'UNKNOWN_CUSTOMER'") == [(1,)]

    def test_final_prefers_its_own_masters(self, root: Path) -> None:
        final = root / "final"
        shutil.copytree(root / "live", final)
        shutil.copytree(root / "masters", final / "masters_final")
        with (final / "masters_final" / "entities.csv").open("a", encoding="utf-8") as handle:
            handle.write("ZA01,Valtermia South Africa,ZA,ZAR,commercial,2026-01-01\n")

        run_ingest(layout_of(root, "final"), allow_errors=True)  # This copy of LIVE has dates outside FINAL.

        assert query(root, "SELECT count(*) FROM entity WHERE entity = 'ZA01'", "final") == [(1,)]
        assert query(root, "SELECT count(*) FROM ingest_issue WHERE code LIKE 'MASTER%'", "final") == [(0,)]

    def test_final_without_its_masters_is_an_error(self, root: Path) -> None:
        shutil.copytree(root / "live", root / "final")

        result = run_ingest(layout_of(root, "final"), allow_errors=True)

        assert result.ok is False
        assert query(root, "SELECT count(*) FROM ingest_issue WHERE code = 'MASTERS_FINAL_MISSING'", "final") == [(1,)]

    def test_a_master_missing_from_final_is_flagged_and_falls_back(self, root: Path) -> None:
        final = root / "final"
        shutil.copytree(root / "live", final)
        shutil.copytree(root / "masters", final / "masters_final")
        (final / "masters_final" / "suppliers.csv").unlink()

        run_ingest(layout_of(root, "final"), allow_errors=True)

        assert query(root, "SELECT severity, detail LIKE 'suppliers.csv%' FROM ingest_issue WHERE code = 'MASTER_NOT_UPDATED'", "final") == [("WARN", True)]
        assert query(root, "SELECT count(*) > 0 FROM supplier", "final") == [(True,)]

    def test_errors_do_not_replace_a_good_warehouse_and_the_new_one_is_kept_aside(self, root: Path) -> None:
        layout = layout_of(root)
        run_ingest(layout)
        gz(root / "live" / "bank_transactions.csv.gz", BANK_HEADER, bank_row("BT-2025-0000001", "NO1", "PAGO FV25-1"))

        result = run_ingest(layout)

        assert result.ok is False
        assert result.published is False
        assert query(root, "SELECT count(*) FROM bank_txn") == [(2,)]
        failed = layout.warehouse_path.with_name("live.failed.duckdb")
        assert result.report["warehouse"] == str(failed)
        con = duckdb.connect(str(failed), read_only=True)
        assert con.execute("SELECT count(*) FROM bank_txn").fetchone() == (1,)
        con.close()

    def test_a_clean_run_removes_a_stale_failed_warehouse(self, root: Path) -> None:
        layout = layout_of(root)
        bank = root / "live" / "bank_transactions.csv.gz"
        good = bank.read_bytes()
        gz(bank, BANK_HEADER, bank_row("BT-2025-0000001", "NO1", "PAGO FV25-1"))
        run_ingest(layout)
        bank.write_bytes(good)

        result = run_ingest(layout)

        assert result.published is True
        assert not layout.warehouse_path.with_name("live.failed.duckdb").exists()

    def test_a_file_no_loader_reads_is_an_error(self, root: Path) -> None:
        gz(root / "live" / "payment_runs" / "UK01_202508_0.csv.gz", PR_HEADER)
        (root / "live" / "bank_statements" / "suelto.sta").write_text(STATEMENT, encoding="utf-8")

        result = run_ingest(layout_of(root), allow_errors=True)

        assert result.ok is False
        unread = {row[0] for row in query(root, "SELECT file FROM ingest_issue WHERE code = 'FILE_NOT_READ'")}
        assert unread == {"live/payment_runs/UK01_202508_0.csv.gz", "live/bank_statements/suelto.sta"}

    def test_pdfs_only_indexed_do_not_count_as_unread_and_golden_is_noted(self, root: Path) -> None:
        gz(root / "live" / "golden_allocations.csv.gz", "bank_txn_id", "x")

        result = run_ingest(layout_of(root))

        assert result.ok is True
        assert query(root, "SELECT file FROM ingest_issue WHERE code = 'GOLDEN_EXCLUDED'") == [("live/golden_allocations.csv.gz",)]

    def test_repeated_entries_in_an_index_are_flagged(self, root: Path) -> None:
        row = "UK01/RA_1.csv,UK01,Cliente,2025-07-02,csv"
        plain(root / "live" / "remittance_advices" / "index.csv", "file,entity,payer_name,received_date,format", row, row)

        run_ingest(layout_of(root))

        assert query(root, "SELECT count FROM ingest_issue WHERE code = 'INDEX_REPEATED_FILE'") == [(1,)]

    def test_writes_a_report_with_counts_per_source(self, root: Path) -> None:
        layout = layout_of(root)

        run_ingest(layout)

        report = json.loads((layout.report_dir / "ingest_report.json").read_text(encoding="utf-8"))
        sources = {(entry["source_type"], entry["segment"]) for entry in report["sources"]}
        assert ("bank_transactions", "mt940/NORT") in sources
        assert ("items", "ARI") in sources

    def test_failure_keeps_the_previous_warehouse(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        layout = layout_of(root)
        run_ingest(layout)
        before = query(root, "SELECT count(*) FROM bank_txn")

        def explode(_ctx: IngestContext) -> None:
            raise RuntimeError("fallo")

        monkeypatch.setattr("bankrec.ingest.STEPS", (("Roto", explode),))
        with pytest.raises(RuntimeError):
            run_ingest(layout)

        assert query(root, "SELECT count(*) FROM bank_txn") == before


class TestPdfOnDemand:
    def test_pdfs_are_indexed_but_not_parsed(self, root: Path) -> None:
        run_ingest(layout_of(root))

        assert query(root, "SELECT file, on_disk, document_norm FROM pdf_index") == [("UK01/ARI_FV251.pdf", True, "FV251")]
        assert query(root, "SELECT count(*) FROM pdf_text_cache") == [(0,)]

    def test_text_is_extracted_on_demand_by_number_and_cached(self, root: Path) -> None:
        layout = layout_of(root)
        run_ingest(layout)
        con = duckdb.connect(str(layout.warehouse_path))

        found = pdf_text(con, layout, "FV25-1")

        assert set(found) == {"UK01/ARI_FV251.pdf"}
        assert found["UK01/ARI_FV251.pdf"].strip()
        assert con.execute("SELECT count(*) FROM pdf_text_cache").fetchone() == (1,)
        assert pdf_text(con, layout, "UK01/ARI_FV251.pdf") == found
        con.close()

    def test_unknown_reference_finds_nothing(self, root: Path) -> None:
        layout = layout_of(root)
        run_ingest(layout)
        con = duckdb.connect(str(layout.warehouse_path))

        assert pdf_text(con, layout, "NO-EXISTE") == {}
        con.close()


class TestCsvLoad:
    @pytest.fixture
    def ctx(self, tmp_path: Path, root: Path) -> IngestContext:
        layout = layout_of(root)
        return IngestContext(layout, open_build(tmp_path / "t.duckdb"), IssueLog(), Manifest())

    def spec(self) -> CsvSpec:
        return CsvSpec("t", "test", ("a", "b"), {"b": "DECIMAL(18,2)"})

    def test_refuses_golden_outright(self, ctx: IngestContext, tmp_path: Path) -> None:
        path = tmp_path / "golden_allocations.csv"
        plain(path, "a,b", "1,2")

        with pytest.raises(GoldenAccessError):
            load_csv(ctx, self.spec(), [path])

    def test_unexpected_header_is_reported_per_file_and_the_rest_loads(self, ctx: IngestContext, tmp_path: Path) -> None:
        good, bad = tmp_path / "good.csv", tmp_path / "bad.csv"
        plain(good, "a,b", "1,2.50")
        plain(bad, "x,y", "1,2")

        rows = load_csv(ctx, self.spec(), [good, bad])

        assert rows == 1
        assert [issue.code for issue in ctx.issues.items] == ["BAD_HEADER"]
        assert ctx.issues.has_errors

    def test_a_malformed_row_is_rejected_visibly(self, ctx: IngestContext, tmp_path: Path) -> None:
        path = tmp_path / "bad_row.csv"
        plain(path, "a,b", "1,2.50", "1,2,3,4", "5,6.00")

        rows = load_csv(ctx, self.spec(), [path])

        assert rows == 2
        assert [issue.code for issue in ctx.issues.items] == ["CSV_ROW_REJECTED"]
        assert ctx.issues.items[0].line == 3

    def test_a_value_of_the_wrong_type_is_reported(self, ctx: IngestContext, tmp_path: Path) -> None:
        path = tmp_path / "bad_value.csv"
        plain(path, "a,b", "1,no-es-numero")

        load_csv(ctx, self.spec(), [path])

        assert [issue.code for issue in ctx.issues.items] == ["BAD_VALUE"]

    def test_no_files_still_creates_the_table_with_its_derived_columns(self, ctx: IngestContext) -> None:
        spec = CsvSpec("t", "test", ("a", "b"), {"b": "DECIMAL(18,2)"}, "b * 2 AS doubled")

        assert load_csv(ctx, spec, []) == 0
        assert ctx.con.execute("SELECT count(*) FROM t").fetchone() == (0,)
        assert [row[0] for row in ctx.con.execute("DESCRIBE t").fetchall()] == ["a", "b", "src_file", "doubled"]


def test_bulk_insert_keeps_commas_quotes_newlines_and_nulls(tmp_path: Path) -> None:
    con = duckdb.connect(str(tmp_path / "b.duckdb"))
    con.execute("CREATE TABLE t (a VARCHAR, b INTEGER)")

    written = bulk_insert(con, "t", [['x,"y"\nz', 1], [None, None]])

    assert written == 2
    assert con.execute("SELECT * FROM t ORDER BY b").fetchall() == [('x,"y"\nz', 1), (None, None)]
