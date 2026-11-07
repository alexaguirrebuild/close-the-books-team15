"""Validation is the last gate before a real submission, so it is tested with the official scorer and real phase files."""
import csv
import gzip
from pathlib import Path

import pytest
from submission_roots import HEADER, REAL_SCORER, build_phase

from bankrec.roots import Layout
from bankrec.submission.baseline import write_baseline
from bankrec.submission.score import score_submission_file
from bankrec.submission.validate import validate_submission

TXNS = ["BT-2025-0000001", "BT-2025-0000002", "BT-2025-0000003"]
ITEM_A = "ARI-2025-0000001"
ITEM_B = "API-2025-0000002"
VALID_ROWS = [
    f"{TXNS[0]},{ITEM_A},1210.00,,",
    f"{TXNS[1]},{ITEM_B},605.00,,SHORT_DISCOUNT",
    f"{TXNS[2]},,,UNIDENTIFIED,UNIDENTIFIED",
]


@pytest.fixture
def layout(tmp_path: Path) -> Layout:
    if not REAL_SCORER.is_file():
        pytest.skip("hace falta bankrec-docs-and-masters/starter")
    txns = [(TXNS[0], 121000, "PAGO FV25-0000001"), (TXNS[1], -60500, "PAGO"), (TXNS[2], 999, "COBRO")]
    items = [(ITEM_A, 121000, "FV25-0000001"), (ITEM_B, 60500, "P1")]
    build_phase(tmp_path, "train", txns, items)
    return build_phase(tmp_path, "live", txns, items)


def write(tmp_path: Path, rows: list[str], header: str = HEADER, name: str = "sub.csv") -> Path:
    path = tmp_path / name
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    return path


def messages(report: dict) -> str:
    return " | ".join(str(error["message"]) for error in report["errors"])


class TestValidate:
    def test_accepts_a_valid_file_with_full_coverage(self, layout: Layout, tmp_path: Path) -> None:
        report = validate_submission(layout, write(tmp_path, VALID_ROWS))

        assert report["ok"] is True
        assert report["official_format_ok"] is True
        assert report["coverage"]["missing"] == 0

    def test_accepts_gzip(self, layout: Layout, tmp_path: Path) -> None:
        path = tmp_path / "sub.csv.gz"
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            handle.write("\n".join([HEADER, *VALID_ROWS]) + "\n")

        assert validate_submission(layout, path)["ok"] is True

    def test_rejects_an_amount_on_a_row_without_item_as_cuadra_exports(self, layout: Layout, tmp_path: Path) -> None:
        rows = [*VALID_ROWS[:2], f"{TXNS[2]},,9.99,,UNIDENTIFIED"]

        report = validate_submission(layout, write(tmp_path, rows))

        assert report["ok"] is False
        assert report["official_format_ok"] is False
        assert "amount must be empty" in messages(report)

    def test_missing_bank_lines_fail_coverage_even_if_the_format_passes(self, layout: Layout, tmp_path: Path) -> None:
        report = validate_submission(layout, write(tmp_path, VALID_ROWS[:2]))

        assert report["official_format_ok"] is True
        assert report["ok"] is False
        assert report["coverage"]["missing"] == 1
        assert TXNS[2] in report["coverage"]["missing_samples"]

    def test_rejects_a_bank_line_of_another_phase(self, layout: Layout, tmp_path: Path) -> None:
        rows = [*VALID_ROWS, "BT-2021-0000009,,,BANK_FEES,"]

        report = validate_submission(layout, write(tmp_path, rows))

        assert report["ok"] is False
        assert "not a bank transaction of this phase" in messages(report)

    def test_rejects_an_unknown_item_the_official_regex_would_let_through(self, layout: Layout, tmp_path: Path) -> None:
        rows = [f"{TXNS[0]},ARI-2025-9999999,1210.00,,", *VALID_ROWS[1:]]

        report = validate_submission(layout, write(tmp_path, rows))

        assert report["official_format_ok"] is True
        assert report["ok"] is False
        assert report["error_counts"] == {"ITEM_UNKNOWN": 1}
        assert "ARI-2025-9999999" in messages(report)

    def test_rejects_duplicate_pairs(self, layout: Layout, tmp_path: Path) -> None:
        report = validate_submission(layout, write(tmp_path, [*VALID_ROWS, VALID_ROWS[0]]))

        assert report["ok"] is False
        assert "duplicate" in messages(report)

    @pytest.mark.parametrize(
        ("row", "expected"),
        [
            (f"{TXNS[0]},{ITEM_A},-1210.00,,", "invalid amount"),
            (f"{TXNS[0]},{ITEM_A},1210,5,,", "expected 5 fields"),
            (f"{TXNS[0]},{ITEM_A},1210.00,BANK_FEES,", "category must be empty"),
            (f"{TXNS[0]},{ITEM_A},1210.00,,MADE_UP", "unknown exception"),
            (f"{TXNS[0]},,,MADE_UP,", "unknown category"),
        ],
    )
    def test_rejects_what_format_md_forbids(self, layout: Layout, tmp_path: Path, row: str, expected: str) -> None:
        report = validate_submission(layout, write(tmp_path, [row, *VALID_ROWS[1:]]))

        assert report["ok"] is False
        assert expected in messages(report)

    def test_rejects_item_rows_mixed_with_a_category_row(self, layout: Layout, tmp_path: Path) -> None:
        report = validate_submission(layout, write(tmp_path, [*VALID_ROWS, f"{TXNS[0]},,,BANK_FEES,"]))

        assert report["ok"] is False
        assert "both item rows" in messages(report)

    def test_amounts_must_have_two_decimals_as_our_format_rule(self, layout: Layout, tmp_path: Path) -> None:
        rows = [f"{TXNS[0]},{ITEM_A},1210.5,,", *VALID_ROWS[1:]]

        report = validate_submission(layout, write(tmp_path, rows))

        assert report["official_format_ok"] is True
        assert report["ok"] is False
        assert "2 decimales" in messages(report)

    def test_header_must_follow_the_documented_order(self, layout: Layout, tmp_path: Path) -> None:
        path = write(tmp_path, ["UNIDENTIFIED,UNIDENTIFIED,,," + TXNS[2]], header="exception,category,item_id,amount,bank_txn_id")

        report = validate_submission(layout, path)

        assert report["ok"] is False
        assert "cabecera" in messages(report)

    def test_rejects_a_file_over_the_row_limit(self, layout: Layout, tmp_path: Path) -> None:
        """Short rows, so it stays under 60 MB and only the 3 M row limit can stop it."""
        path = tmp_path / "many.csv"
        path.write_text(f"{HEADER}\n" + "X,,,BANK_FEES,\n" * 3_000_001, encoding="utf-8")

        report = validate_submission(layout, path)

        assert report["official_format_ok"] is False
        assert report["ok"] is False
        assert "too many rows" in messages(report)


class TestBaseline:
    def test_every_bank_line_gets_a_row_and_the_file_validates(self, layout: Layout, tmp_path: Path) -> None:
        out = tmp_path / "base.csv.gz"

        rows, matched = write_baseline(layout, out)

        assert rows == 3
        assert matched == 1
        assert validate_submission(layout, out)["ok"] is True

    def test_defaults_follow_format_md(self, layout: Layout, tmp_path: Path) -> None:
        out = tmp_path / "base.csv.gz"
        write_baseline(layout, out)

        with gzip.open(out, "rt", encoding="utf-8", newline="") as handle:
            rows = {row["bank_txn_id"]: row for row in csv.DictReader(handle)}

        assert rows[TXNS[0]] == {"bank_txn_id": TXNS[0], "item_id": ITEM_A, "amount": "1210.00", "category": "", "exception": ""}
        assert rows[TXNS[1]]["category"] == "BANK_FEES"
        assert rows[TXNS[1]]["amount"] == ""
        assert rows[TXNS[2]]["category"] == "UNIDENTIFIED"
        assert rows[TXNS[2]]["exception"] == "UNIDENTIFIED"


class TestScore:
    def test_refuses_a_phase_without_golden(self, layout: Layout, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            score_submission_file(layout, write(tmp_path, VALID_ROWS))
