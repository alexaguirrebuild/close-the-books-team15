"""Exit codes are what stops the FINAL flow when something is wrong, so they are part of the contract."""
from pathlib import Path

import pytest

from bankrec.cli import EXIT_OK, EXIT_PROBLEMS, EXIT_USAGE, main
from test_ingest import BANK_HEADER, bank_row, gz, root  # noqa: F401  (root is a fixture)


def test_clean_ingest_exits_zero_and_writes_warehouse_and_report(root: Path) -> None:  # noqa: F811
    code = main(["ingest", "--phase", "live", "--data-root", str(root)])

    assert code == EXIT_OK
    assert (root / "cache" / "warehouse" / "live.duckdb").is_file()
    assert (root / "out" / "live" / "ingest_report.json").is_file()
    assert (root / "out" / "live" / "ingest.log").is_file()


def test_ingest_with_errors_exits_non_zero(root: Path) -> None:  # noqa: F811
    gz(root / "live" / "bank_transactions.csv.gz", BANK_HEADER, bank_row("BT-2025-0000001", "NO1", "PAGO FV25-1"))

    assert main(["ingest", "--phase", "live", "--data-root", str(root)]) == EXIT_PROBLEMS


def test_errors_keep_the_previous_warehouse_unless_told_otherwise(root: Path) -> None:  # noqa: F811
    warehouse = root / "cache" / "warehouse" / "live.duckdb"
    main(["ingest", "--phase", "live", "--data-root", str(root)])
    good = warehouse.read_bytes()
    gz(root / "live" / "bank_transactions.csv.gz", BANK_HEADER, bank_row("BT-2025-0000001", "NO1", "PAGO FV25-1"))

    assert main(["ingest", "--phase", "live", "--data-root", str(root)]) == EXIT_PROBLEMS
    assert warehouse.read_bytes() == good
    assert warehouse.with_name("live.failed.duckdb").is_file()

    assert main(["ingest", "--phase", "live", "--data-root", str(root), "--allow-errors"]) == EXIT_PROBLEMS
    assert warehouse.read_bytes() != good
    assert not warehouse.with_name("live.failed.duckdb").exists()


def test_pdf_without_a_warehouse_says_so_and_does_not_create_one(root: Path) -> None:  # noqa: F811
    assert main(["pdf", "--phase", "live", "--data-root", str(root), "FV25-1"]) == EXIT_PROBLEMS
    assert not (root / "cache" / "warehouse" / "live.duckdb").exists()


def test_missing_data_says_where_it_looked(root: Path, capsys: pytest.CaptureFixture[str]) -> None:  # noqa: F811
    code = main(["ingest", "--phase", "final", "--data-root", str(root)])

    assert code == EXIT_USAGE
    assert str(root) in capsys.readouterr().err


def test_unknown_phase_is_refused() -> None:
    with pytest.raises(SystemExit) as stopped:
        main(["ingest", "--phase", "trian"])

    assert stopped.value.code == EXIT_USAGE


def test_score_refuses_a_phase_without_golden_at_the_command_line(root: Path) -> None:  # noqa: F811
    with pytest.raises(SystemExit) as stopped:
        main(["score", "--phase", "live", "--data-root", str(root), "x.csv"])

    assert stopped.value.code == EXIT_USAGE


def test_pdf_command_reports_found_and_not_found(root: Path) -> None:  # noqa: F811
    main(["ingest", "--phase", "live", "--data-root", str(root)])

    assert main(["pdf", "--phase", "live", "--data-root", str(root), "FV25-1"]) == EXIT_OK
    assert main(["pdf", "--phase", "live", "--data-root", str(root), "NO-EXISTE"]) == EXIT_PROBLEMS
