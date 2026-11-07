"""Stage files, the report, merging by priority and scoring a window of TRAIN with the official scorer."""
import csv
import gzip
import json
from pathlib import Path

import duckdb
import pytest

from bankcheck.cli import main as bankrec_main
from bankcheck.matching.__main__ import EXIT_OK, EXIT_PROBLEMS, EXIT_USAGE, main
from bankcheck.matching.contract import MATCH_COLUMNS, read_match_rows, stage_path, write_match_rows
from bankcheck.matching.evaluate import evaluate_stage
from bankcheck.matching.merge import merge_stages
from bankcheck.matching.remittance import run_remittance
from bankcheck.matching.remittance_rules import SOURCE
from bankcheck.roots import Layout
from bankcheck.submission import validate

STARTER = Path(__file__).resolve().parents[2] / "bankrec-docs-and-masters" / "starter"
MASTERS = Path(__file__).resolve().parents[2] / "bankrec-docs-and-masters" / "masters"
SUBMISSION_HEADER = ["bank_txn_id", "item_id", "amount", "category", "exception"]
WAREHOUSE_SQL = """
CREATE TABLE remittance_advice (advice_id VARCHAR, entity VARCHAR, payer_name VARCHAR, format VARCHAR, layout VARCHAR,
    payment_ref VARCHAR, payment_date DATE, currency VARCHAR, control_amount DECIMAL(18,2), lines_sum DECIMAL(18,2),
    n_lines INTEGER, control_status VARCHAR, on_disk BOOLEAN);
CREATE TABLE remittance_line (advice_id VARCHAR, line_no INTEGER, document VARCHAR, document_norm VARCHAR, doc_date DATE,
    gross DECIMAL(18,2), deduction DECIMAL(18,2), reason VARCHAR, paid DECIMAL(18,2), is_repeated BOOLEAN);
CREATE TABLE bank_txn (bank_txn_id VARCHAR, entity VARCHAR, amount DECIMAL(18,2), currency VARCHAR, booking_date DATE,
    orig_amount DECIMAL(18,2), ref2 VARCHAR, end_to_end_id VARCHAR, text VARCHAR);
CREATE TABLE item (item_id VARCHAR, item_type VARCHAR, side VARCHAR, entity VARCHAR, printed_norm VARCHAR, party_id VARCHAR,
    issue_date DATE, currency VARCHAR, total_amount DECIMAL(18,2), open_amount DECIMAL(18,2), is_opening BOOLEAN, payment_terms VARCHAR);
CREATE TABLE customer (customer_id VARCHAR, entity VARCHAR, payment_terms VARCHAR);
CREATE TABLE payment_term (code VARCHAR, early_payment_discount_pct DECIMAL(5,2), discount_days INTEGER);
INSERT INTO payment_term VALUES ('T30', NULL, NULL);
INSERT INTO remittance_advice VALUES
  ('A1', 'ES01', 'Pagador SA', 'csv', 'csv', '250715123456', DATE '2025-07-15', 'EUR', 150.00, 150.00, 3, 'ok', true),
  ('A2', 'ES01', 'Otro SA', 'csv', 'csv', '250601999999', DATE '2025-06-01', 'EUR', 70.00, 70.00, 1, 'ok', true),
  ('A3', 'ES01', 'Fuera SA', 'pdf', 'pdf', NULL, NULL, NULL, NULL, NULL, 0, 'not_on_disk', false);
INSERT INTO remittance_line VALUES
  ('A1', 1, 'FV25-0000001', 'FV250000001', DATE '2025-07-01', 100.00, NULL, NULL, 100.00, false),
  ('A1', 2, 'FV25-0000002', 'FV250000002', DATE '2025-07-01', 50.00, NULL, NULL, 50.00, false),
  ('A1', 3, 'NOEXISTE', 'NOEXISTE', DATE '2025-07-01', 9.00, NULL, NULL, 0.00, false),
  ('A1', 4, 'FV25-0000003', 'FV250000003', DATE '2025-07-01', 10.00, 10.00, 'PRICE DIFF', 0.00, false),
  ('A2', 1, 'FV25-0000009', 'FV250000009', DATE '2025-05-01', 70.00, NULL, NULL, 70.00, false);
INSERT INTO bank_txn VALUES ('BT-2025-0000001', 'ES01', 150.00, 'EUR', DATE '2025-07-15', NULL, NULL, NULL, 'SEPA 250715123456');
INSERT INTO item VALUES
  ('ARI-2025-0000001', 'ARI', 'AR', 'ES01', 'FV250000001', 'C1', DATE '2025-07-01', 'EUR', 100.00, 100.00, false, 'T30'),
  ('ARI-2025-0000002', 'ARI', 'AR', 'ES01', 'FV250000002', 'C1', DATE '2025-07-01', 'EUR', 50.00, 50.00, false, 'T30'),
  ('ARI-2025-0000003', 'ARI', 'AR', 'ES01', 'FV250000003', 'C1', DATE '2025-07-01', 'EUR', 10.00, 10.00, false, 'T30');
"""


def build_layout(root: Path, phase: str = "live") -> Layout:
    phase_dir = root / phase
    phase_dir.mkdir(parents=True)
    warehouse = root / "cache" / "warehouse" / f"{phase}.duckdb"
    warehouse.parent.mkdir(parents=True)
    return Layout(
        root=root, phase=phase, phase_dir=phase_dir, masters_dir=MASTERS, masters_override_dir=None,
        starter_dir=STARTER, warehouse_path=warehouse, report_dir=root / "out" / phase,
    )


@pytest.fixture
def layout(tmp_path: Path) -> Layout:
    built = build_layout(tmp_path)
    con = duckdb.connect(str(built.warehouse_path))
    con.execute(WAREHOUSE_SQL)
    con.close()
    return built


def plain_rows(path: Path) -> list[list[str]]:
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        return list(csv.reader(handle))


def stage_row(txn: str, item: str, amount: str = "10.00", exception: str = "", source: str = "stage") -> dict[str, str]:
    return {"bank_txn_id": txn, "item_id": item, "amount": amount, "category": "", "exception": exception, "source": source, "confidence": "1.0", "advice_id": "", "line_no": ""}


def write_base(path: Path, rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(SUBMISSION_HEADER)
        writer.writerows(rows)


class TestRunRemittance:
    def test_writes_the_contract_columns(self, layout):
        run_remittance(layout)
        assert plain_rows(stage_path(layout, SOURCE))[0] == list(MATCH_COLUMNS)

    def test_rows_carry_item_amount_and_provenance(self, layout):
        run_remittance(layout)
        rows = {row["item_id"]: row for row in read_match_rows(stage_path(layout, SOURCE))}
        assert rows["ARI-2025-0000001"]["amount"] == "100.00"
        assert rows["ARI-2025-0000001"]["advice_id"] == "A1"
        assert rows["ARI-2025-0000001"]["source"] == SOURCE

    def test_a_line_that_paid_nothing_gives_no_row(self, layout):
        run_remittance(layout)
        items = {row["item_id"] for row in read_match_rows(stage_path(layout, SOURCE))}
        assert "ARI-2025-0000003" not in items

    def test_report_counts_links_and_unresolved(self, layout):
        report = run_remittance(layout)
        assert report["advices"] == {"total": 3, "on_disk": 2, "linked": {"ref": 1}, "unlinked": {"before_phase": 1, "in_phase": 0, "after_phase": 0}}
        assert report["lines"]["unresolved"] == 1
        assert report["rows"]["total"] == 2
        assert report["rows"]["zero_amount_dropped"] == 1

    def test_line_without_paid_is_counted_as_such(self, layout):
        con = duckdb.connect(str(layout.warehouse_path))
        con.execute("""INSERT INTO remittance_line VALUES ('A1', 5, 'FV25-0000002', 'FV250000002', DATE '2025-07-01', 50.00, NULL, NULL, NULL, false)""")
        con.close()
        rows = run_remittance(layout)["rows"]
        assert rows["no_paid_dropped"] == 1
        assert rows["repeated_item_dropped"] == 0

    def test_same_item_on_two_lines_is_counted_as_repeated(self, layout):
        con = duckdb.connect(str(layout.warehouse_path))
        con.execute("""INSERT INTO remittance_line VALUES ('A1', 5, 'FV25-0000002', 'FV250000002', DATE '2025-07-01', 50.00, NULL, NULL, 50.00, false)""")
        con.close()
        rows = run_remittance(layout)["rows"]
        assert rows["repeated_item_dropped"] == 1
        assert rows["no_paid_dropped"] == 0

    def test_report_is_written_next_to_the_rows(self, layout):
        run_remittance(layout)
        saved = json.loads(stage_path(layout, SOURCE).with_name(f"{SOURCE}_report.json").read_text(encoding="utf-8"))
        assert saved["phase"] == "live"

    def test_two_runs_give_identical_bytes(self, layout):
        run_remittance(layout)
        first = stage_path(layout, SOURCE).read_bytes()
        run_remittance(layout)
        assert stage_path(layout, SOURCE).read_bytes() == first

    def test_warehouse_is_not_modified(self, layout):
        before = layout.warehouse_path.read_bytes()
        run_remittance(layout)
        assert layout.warehouse_path.read_bytes() == before


class TestStageFiles:
    def test_missing_stage_file_names_the_path(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="no_existe"):
            list(read_match_rows(tmp_path / "no_existe.csv.gz"))

    def test_unknown_column_set_is_rejected(self, tmp_path):
        path = tmp_path / "raro.csv.gz"
        write_base(path, [])
        with pytest.raises(ValueError, match="columnas"):
            list(read_match_rows(path))

    def test_round_trip_keeps_values(self, tmp_path):
        path = tmp_path / "x.csv.gz"
        row = stage_row("BT-1", "ARI-2025-0000001", "12.50", "PARTIAL")
        write_match_rows(path, [tuple(row[column] for column in MATCH_COLUMNS)])
        assert list(read_match_rows(path)) == [row]


class TestMerge:
    def write_stage(self, layout: Layout, name: str, rows: list[dict[str, str]]) -> None:
        write_match_rows(stage_path(layout, name), [tuple(row[column] for column in MATCH_COLUMNS) for row in rows])

    def base_path(self, layout: Layout) -> Path:
        return layout.report_dir / "baseline.csv.gz"

    def test_stage_replaces_every_base_row_of_its_line(self, layout):
        write_base(self.base_path(layout), [["BT-1", "ARI-2025-0000001", "5.00", "", ""]])
        self.write_stage(layout, "s1", [stage_row("BT-1", "ARI-2025-0000002")])
        out = merge_stages(layout, ["s1"], "m.csv.gz")
        assert plain_rows(out)[1:] == [["BT-1", "ARI-2025-0000002", "10.00", "", ""]]

    def test_lines_without_a_stage_keep_the_base(self, layout):
        write_base(self.base_path(layout), [["BT-1", "", "", "BANK_FEES", ""], ["BT-2", "", "", "UNIDENTIFIED", "UNIDENTIFIED"]])
        self.write_stage(layout, "s1", [stage_row("BT-1", "ARI-2025-0000002")])
        out = merge_stages(layout, ["s1"], "m.csv.gz")
        assert plain_rows(out)[2] == ["BT-2", "", "", "UNIDENTIFIED", "UNIDENTIFIED"]

    def test_earlier_stage_wins_on_the_same_line(self, layout):
        write_base(self.base_path(layout), [["BT-1", "", "", "BANK_FEES", ""]])
        self.write_stage(layout, "first", [stage_row("BT-1", "ARI-2025-0000001")])
        self.write_stage(layout, "second", [stage_row("BT-1", "ARI-2025-0000002")])
        out = merge_stages(layout, ["first", "second"], "m.csv.gz")
        assert plain_rows(out)[1:] == [["BT-1", "ARI-2025-0000001", "10.00", "", ""]]

    def test_header_is_the_official_one(self, layout):
        write_base(self.base_path(layout), [["BT-1", "", "", "BANK_FEES", ""]])
        self.write_stage(layout, "s1", [])
        assert plain_rows(merge_stages(layout, ["s1"], "m.csv.gz"))[0] == SUBMISSION_HEADER

    def test_stage_line_missing_from_the_base_is_an_error(self, layout):
        write_base(self.base_path(layout), [["BT-1", "", "", "BANK_FEES", ""]])
        self.write_stage(layout, "s1", [stage_row("BT-OTRO", "ARI-2025-0000002")])
        with pytest.raises(ValueError, match="BT-OTRO"):
            merge_stages(layout, ["s1"], "m.csv.gz")

    def test_missing_base_says_how_to_build_it(self, layout):
        self.write_stage(layout, "s1", [])
        with pytest.raises(FileNotFoundError, match="bankcheck baseline"):
            merge_stages(layout, ["s1"], "m.csv.gz")


class TestEvaluate:
    @pytest.fixture
    def train(self, tmp_path: Path) -> Layout:
        if not (STARTER / "bankrec_score.py").is_file():
            pytest.skip("hace falta bankrec-docs-and-masters/starter")
        built = build_layout(tmp_path, "train")
        with gzip.open(built.phase_dir / "bank_transactions.csv.gz", "wt", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(["bank_txn_id", "booking_date", "amount"])
            writer.writerows([["BT-OLD", "2024-01-10", "5.00"], ["BT-NEW", "2024-08-10", "10.00"], ["BT-MISS", "2024-08-11", "7.00"]])
        with gzip.open(built.phase_dir / "golden_allocations.csv.gz", "wt", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(["bank_txn_id", "item_id", "amount", "category", "exception", "segment", "difficulty"])
            writer.writerows([
                ["BT-OLD", "ARI-2024-0000001", "5.00", "", "", "seg_old", "1"],
                ["BT-NEW", "ARI-2024-0000002", "10.00", "", "", "seg_new", "1"],
                ["BT-MISS", "ARI-2024-0000003", "7.00", "", "", "seg_miss", "1"],
            ])
        write_match_rows(stage_path(built, SOURCE), [("BT-NEW", "ARI-2024-0000002", "10.00", "", "", SOURCE, "1.0", "A1", "1")])
        return built

    def test_linked_lines_score_only_the_lines_the_stage_answers(self, train):
        result = evaluate_stage(train, SOURCE, "2024-07-01")
        assert result["linked"]["metrics"]["txn_accuracy"] == 1.0
        assert result["linked"]["metrics"]["precision"] == 1.0
        assert result["linked"]["metrics"]["recall"] == 1.0

    def test_window_excludes_lines_before_the_date(self, train):
        result = evaluate_stage(train, SOURCE, "2024-07-01")
        assert result["window"]["metrics"]["txns_total"] == 2
        assert result["window"]["metrics"]["recall"] == 0.5

    def test_report_has_scores_and_no_label_rows(self, train):
        evaluate_stage(train, SOURCE, "2024-07-01")
        saved = (train.report_dir / f"{SOURCE}_evaluate.json").read_text(encoding="utf-8")
        assert "ARI-2024-0000003" not in saved

    def test_no_golden_is_left_behind(self, train):
        evaluate_stage(train, SOURCE, "2024-07-01")
        leftovers = [path.name for path in train.report_dir.rglob("*") if "golden" in path.name.lower()]
        assert leftovers == []

    def test_phase_without_golden_is_refused(self, layout):
        with pytest.raises(ValueError, match="golden"):
            evaluate_stage(layout, SOURCE, "2024-07-01")


class TestCommandLine:
    @pytest.fixture
    def root(self, layout: Layout) -> Path:
        (layout.phase_dir / "bank_transactions.csv.gz").write_bytes(b"")
        (layout.root / "masters").mkdir()
        return layout.root

    def test_m2_writes_the_stage_file(self, root, layout):
        assert main(["m2", "--phase", "live", "--data-root", str(root)]) == EXIT_OK
        assert stage_path(layout, SOURCE).is_file()

    def test_merge_without_baseline_reports_a_problem(self, root):
        main(["m2", "--phase", "live", "--data-root", str(root)])
        assert main(["merge", "--phase", "live", "--data-root", str(root)]) == EXIT_PROBLEMS

    def build_base(self, layout: Layout) -> None:
        write_base(layout.report_dir / "baseline.csv.gz", [["BT-2025-0000001", "", "", "UNIDENTIFIED", "UNIDENTIFIED"]])

    def test_merge_validates_what_it_writes(self, root, layout, monkeypatch):
        self.build_base(layout)
        checked: list[Path] = []
        monkeypatch.setattr(validate, "validate_submission", lambda _layout, path: checked.append(path) or {"ok": True})
        monkeypatch.setattr(validate, "print_validation", lambda report, _layout: None)
        main(["m2", "--phase", "live", "--data-root", str(root)])
        assert main(["merge", "--phase", "live", "--data-root", str(root)]) == EXIT_OK
        assert checked == [layout.report_dir / "submission_m2.csv.gz"]

    def test_merge_fails_when_the_file_is_invalid(self, root, layout, monkeypatch):
        self.build_base(layout)
        monkeypatch.setattr(validate, "validate_submission", lambda _layout, _path: {"ok": False})
        monkeypatch.setattr(validate, "print_validation", lambda report, _layout: None)
        main(["m2", "--phase", "live", "--data-root", str(root)])
        assert main(["merge", "--phase", "live", "--data-root", str(root)]) == EXIT_PROBLEMS

    def test_missing_data_root_is_a_usage_error(self, tmp_path):
        assert main(["m2", "--phase", "live", "--data-root", str(tmp_path / "nada")]) == EXIT_USAGE

    def test_bankrec_match_runs_the_stage(self, root, layout):
        assert bankrec_main(["match", "--phase", "live", "--data-root", str(root)]) == EXIT_OK
        assert stage_path(layout, SOURCE).is_file()


class TestGoldenBoundary:
    PACKAGE = Path(__file__).resolve().parents[1] / "bankcheck" / "matching"

    def test_only_evaluate_mentions_the_golden(self):
        mentions = {path.name for path in self.PACKAGE.glob("*.py") if "golden" in path.read_text(encoding="utf-8").lower()}
        assert mentions == {"evaluate.py"}

    def test_stages_and_merge_do_not_import_the_scorer_or_evaluate(self):
        for name in ("remittance.py", "remittance_link.py", "remittance_items.py", "remittance_rules.py", "merge.py", "contract.py"):
            source = (self.PACKAGE / name).read_text(encoding="utf-8")
            assert "submission.score" not in source and "evaluate" not in source, name

    def test_stage_output_has_no_label_columns(self, layout):
        run_remittance(layout)
        assert not {"segment", "difficulty"} & set(plain_rows(stage_path(layout, SOURCE))[0])
