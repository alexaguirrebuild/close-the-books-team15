"""One command any session can run: TRAIN with a score, LIVE with validation and coverage, and nothing uploaded."""
import json
from pathlib import Path

import pytest
from submission_roots import build_phase, write_golden, write_submission_text

from bankcheck.cli import main as cli_main
from bankcheck.roots import DataRootNotFound, resolve_layout
from bankcheck.submission import verify as verify_module
from bankcheck.submission.score import score_submission_file
from bankcheck.submission.validate import validate_submission
from bankcheck.submission.verify import _reports_folder as verify_reports_folder
from bankcheck.submission.verify import EXIT_OK, EXIT_PROBLEMS, main

T1, T2, T3 = "BT-2025-0000001", "BT-2025-0000002", "BT-2025-0000003"
ITEM_A, ITEM_B = "ARI-2025-0000001", "API-2025-0000002"
TXNS = [(T1, 121000, "PAGO FV25-0000001"), (T2, -60500, "PAGO"), (T3, 999, "COBRO")]
ITEMS = [(ITEM_A, 121000, "FV25-0000001"), (ITEM_B, 60500, "P1")]
GOLDEN = [f"{T1},{ITEM_A},1210.00,,,ar_1to1,1", f"{T2},,,BANK_FEES,,nonitem,1", f"{T3},,,UNIDENTIFIED,UNIDENTIFIED,unidentified,1"]
REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def root(tmp_path: Path) -> Path:
    base = tmp_path / "repo"  # Una carpeta llamada data/ movería out/ y cache/ a su padre (roots.py) y estas rutas mirarían otro sitio.
    train = build_phase(base, "train", TXNS, ITEMS)
    write_golden(train, GOLDEN)
    build_phase(base, "live", TXNS, ITEMS)
    return base


def saved_report(root: Path) -> dict:
    return json.loads((root / "out" / "verification" / "report.json").read_text(encoding="utf-8"))


def test_default_run_scores_train_and_validates_live(root: Path) -> None:
    code = main(["--data-root", str(root)])

    report = saved_report(root)
    assert code == EXIT_OK
    assert report["phases"]["train"]["score"]["score"] == 100.0
    assert report["phases"]["train"]["validation"]["ok"] is True
    assert report["phases"]["live"]["validation"]["coverage"]["missing"] == 0
    assert "score" not in report["phases"]["live"]


def test_markdown_report_states_the_score_coverage_and_that_nothing_was_sent(root: Path) -> None:
    main(["--data-root", str(root)])

    text = (root / "out" / "verification" / "report.md").read_text(encoding="utf-8")

    assert "100.00" in text
    assert "3 de 3" in text
    assert "LIVE no tiene puntuación" in text
    assert "No se ha subido nada" in text


def test_uses_the_file_of_another_session_instead_of_the_baseline(root: Path, tmp_path: Path) -> None:
    rows = [f"{T1},{ITEM_A},1210.00,,", f"{T2},,,BANK_FEES,", f"{T3},,,UNIDENTIFIED,UNIDENTIFIED"]
    mine = write_submission_text(tmp_path, rows, "matcher.csv")

    code = main(["--data-root", str(root), "--phase", "live", "--file", f"live={mine}"])

    report = saved_report(root)
    assert code == EXIT_OK
    assert report["phases"]["live"]["file"] == str(mine)
    assert not (root / "out" / "live" / "baseline.csv.gz").exists()


def test_invalid_file_fails_the_run_and_the_report_still_says_why(root: Path, tmp_path: Path) -> None:
    missing_two_lines = write_submission_text(tmp_path, [f"{T1},{ITEM_A},1210.00,,"], "partial.csv")

    code = main(["--data-root", str(root), "--phase", "live", "--file", f"live={missing_two_lines}"])

    report = saved_report(root)
    assert code == EXIT_PROBLEMS
    assert report["ok"] is False
    assert report["phases"]["live"]["validation"]["coverage"]["missing"] == 2


def test_phase_that_cannot_run_is_reported_instead_of_crashing(root: Path) -> None:
    code = main(["--data-root", str(root), "--phase", "final"])

    report = saved_report(root)
    assert code == EXIT_PROBLEMS
    assert report["phases"]["final"]["ok"] is False
    assert "final" in report["phases"]["final"]["error"]


def test_missing_warehouse_is_reported_instead_of_crashing(root: Path) -> None:
    (root / "cache" / "warehouse" / "live.duckdb").unlink()

    code = main(["--data-root", str(root), "--phase", "live"])

    assert code == EXIT_PROBLEMS
    assert saved_report(root)["phases"]["live"]["ok"] is False


@pytest.mark.parametrize("damaged", ["golden_allocations.csv.gz", "bank_transactions.csv.gz"])
def test_damaged_files_of_the_phase_are_reported_instead_of_crashing(root: Path, damaged: str) -> None:
    path = root / "train" / damaged
    path.write_bytes(path.read_bytes()[:-9])

    code = main(["--data-root", str(root), "--phase", "train"])

    assert code == EXIT_PROBLEMS
    assert saved_report(root)["phases"]["train"]["ok"] is False


def test_the_pipeline_command_runs_it_too(root: Path) -> None:
    code = cli_main(["verify", "--data-root", str(root), "--phase", "live"])

    assert code == EXIT_OK
    assert saved_report(root)["phases"]["live"]["validation"]["ok"] is True


def test_bad_file_argument_is_a_usage_error(root: Path) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--data-root", str(root), "--file", "not-a-pair"])

    assert exit_info.value.code == 2


def test_two_files_for_one_phase_are_a_usage_error_not_a_silent_drop(root: Path, tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--data-root", str(root), "--file", f"live={tmp_path / 'a.csv'}", "--file", f"live={tmp_path / 'b.csv'}"])

    assert exit_info.value.code == 2


def test_an_unexpected_failure_in_a_phase_is_reported_and_the_others_still_run(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_validate = verify_module.validate_submission

    def fail_on_train(layout, path):
        if layout.phase == "train":
            raise TypeError("a bug nobody expected")
        return real_validate(layout, path)

    monkeypatch.setattr(verify_module, "validate_submission", fail_on_train)

    code = main(["--data-root", str(root)])

    report = saved_report(root)
    assert code == EXIT_PROBLEMS
    assert "TypeError" in report["phases"]["train"]["error"]
    assert report["phases"]["live"]["ok"] is True


def test_an_interrupted_run_leaves_no_older_report_passing_as_good(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert main(["--data-root", str(root), "--phase", "live"]) == EXIT_OK
    folder = root / "out" / "verification"
    assert (folder / "report.json").is_file()

    def interrupted(*_args):
        raise KeyboardInterrupt

    monkeypatch.setattr(verify_module, "_run_phase", interrupted)

    with pytest.raises(KeyboardInterrupt):
        main(["--data-root", str(root), "--phase", "live"])

    assert not (folder / "report.json").exists()
    assert not (folder / "report.md").exists()


class TestSelfCheck:
    def test_labels_as_a_submission_score_100_and_pass_the_gate(self, root: Path) -> None:
        code = main(["--data-root", str(root), "--phase", "train", "--self-check"])

        check = saved_report(root)["phases"]["train"]["self_check"]
        assert code == EXIT_OK
        assert check == {"ok": True, "score": 100.0, "validation_ok": True, "errors": []}

    def test_nothing_derived_from_the_labels_stays_on_disk(self, root: Path) -> None:
        main(["--data-root", str(root), "--phase", "train", "--self-check"])

        files = sorted(path.name for path in (root / "out").rglob("*") if path.is_file())

        assert [name for name in files if "golden" in name] == []
        assert [name for name in files if name.endswith((".csv", ".gz"))] == ["baseline.csv.gz"]

    def test_labels_the_scorer_rejects_are_reported_as_a_failed_check(self, root: Path) -> None:
        """Exactly when the check must say it failed: there is no score to print, and the report must still be written."""
        rows = [f"{T1},{ITEM_A},1210.00,,,ar_1to1,1", f"{T2},,,NOT_A_CATEGORY,,nonitem,1", GOLDEN[2]]
        write_golden(resolve_layout("train", data_root=root), rows)

        code = main(["--data-root", str(root), "--phase", "train", "--self-check"])

        check = saved_report(root)["phases"]["train"]["self_check"]
        text = (root / "out" / "verification" / "report.md").read_text(encoding="utf-8")
        assert code == EXIT_PROBLEMS
        assert check["ok"] is False
        assert check["score"] is None
        assert "FALLA" in text

    def test_a_label_the_gate_would_reject_fails_the_run(self, root: Path) -> None:
        write_golden(resolve_layout("train", data_root=root), [f"{T1},ARI-2025-9999999,1210.00,,,ar_1to1,1", *GOLDEN[1:]])

        code = main(["--data-root", str(root), "--phase", "train", "--self-check"])

        check = saved_report(root)["phases"]["train"]["self_check"]
        assert code == EXIT_PROBLEMS
        assert check["ok"] is False
        assert check["validation_ok"] is False

    def test_phase_without_labels_has_no_self_check(self, root: Path) -> None:
        code = main(["--data-root", str(root), "--phase", "live", "--self-check"])

        assert code == EXIT_OK
        assert "self_check" not in saved_report(root)["phases"]["live"]


@pytest.mark.data
class TestAgainstTheRealSample:
    @pytest.fixture
    def sample(self):
        try:
            layout = resolve_layout("sample", data_root=REPO_ROOT)
        except DataRootNotFound:
            pytest.skip("hace falta sample/ en la raíz del repo o en bankrec-docs-and-masters/")
        if not layout.warehouse_path.is_file():
            pytest.skip("hace falta `bankcheck ingest --phase sample`")
        return layout

    def test_labels_of_the_sample_score_exactly_100(self, sample, tmp_path: Path) -> None:
        from bankcheck.submission.score import golden_as_submission

        with golden_as_submission(sample) as path:
            result = score_submission_file(sample, path)
            report = validate_submission(sample, path)

        assert result["score"] == 100.0
        assert report["ok"] is True
        assert not report["warnings"]


def test_a_relative_root_from_inside_data_also_sends_the_reports_outside_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--data-root .` has no name to recognise: the report ended up inside data/, where nothing looks for it."""
    project = tmp_path / "repo"
    data = project / "data"
    build_phase(data, "live", TXNS, ITEMS)
    monkeypatch.chdir(data)

    main(["--data-root", ".", "--phase", "live"])

    assert (project / "out" / "verification" / "report.json").is_file()
    assert not (data / "out" / "verification").exists()


def test_with_a_data_folder_the_reports_go_to_the_project_out_folder(tmp_path: Path) -> None:
    """roots.py keeps out/ and cache/ outside data/; the verification report must land next to them."""
    assert verify_reports_folder(tmp_path / "data") == tmp_path / "out" / "verification"
    assert verify_reports_folder(tmp_path / "repo") == tmp_path / "repo" / "out" / "verification"
