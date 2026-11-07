"""What the first version of the local gate let through or got wrong: each test names a real way to lose points in FINAL."""
import csv
import gzip
import json
import uuid
from pathlib import Path

import pytest
from submission_roots import HEADER, build_phase, over_60_mb, run_on_warehouse, write_golden, write_submission_text

from bankcheck.roots import Layout, resolve_layout
from bankcheck.submission.baseline import write_baseline
from bankcheck.submission.official import ScorerChanged, lift_limits, load_official_scorer
from bankcheck.submission.score import score_submission_file
from bankcheck.submission.validate import PhaseDataUnreadable, validate_submission
from bankcheck.submission.writer import write_submission

T1, T2, T3 = "BT-2025-0000001", "BT-2025-0000002", "BT-2025-0000003"
ITEM_A, ITEM_B = "ARI-2025-0000001", "API-2025-0000002"
CLOSED_IN_TRAIN = "ARI-2024-0000009"
CLOSED_IN_LIVE = "ARC-2025-0000077"
TXNS = [(T1, 121000, "PAGO FV25-0000001"), (T2, -60500, "PAGO"), (T3, 999, "COBRO")]
ITEMS = [(ITEM_A, 121000, "FV25-0000001"), (ITEM_B, 60500, "P1")]
VALID_ROWS = [f"{T1},{ITEM_A},1210.00,,", f"{T2},{ITEM_B},605.00,,SHORT_DISCOUNT", f"{T3},,,UNIDENTIFIED,UNIDENTIFIED"]


@pytest.fixture
def live(tmp_path: Path) -> Layout:
    build_phase(tmp_path, "train", TXNS, [*ITEMS, (CLOSED_IN_TRAIN, 9900, "OLD1")])
    return build_phase(tmp_path, "live", TXNS, ITEMS)


@pytest.fixture
def train(tmp_path: Path) -> Layout:
    return build_phase(tmp_path, "train", TXNS, ITEMS)


def codes(report: dict) -> set[str]:
    return {error["code"] for error in report["errors"]}


def warning(report: dict, code: str) -> dict:
    return next(entry for entry in report["warnings"] if entry["code"] == code)


class TestItemsOfEarlierPhases:
    """A return or a duplicate can hit an item closed before the phase: it is in no table of the phase."""

    def test_item_of_an_earlier_phase_warns_and_keeps_the_file_valid(self, live: Layout, tmp_path: Path) -> None:
        rows = [*VALID_ROWS[:2], f"{T3},{CLOSED_IN_TRAIN},99.00,,RETURNED"]

        report = validate_submission(live, write_submission_text(tmp_path, rows))

        assert report["ok"] is True
        assert warning(report, "ITEM_FROM_PRIOR_PHASE")["count"] == 1
        assert warning(report, "ITEM_FROM_PRIOR_PHASE")["samples"] == [CLOSED_IN_TRAIN]

    def test_item_in_no_phase_at_all_is_an_error(self, live: Layout, tmp_path: Path) -> None:
        rows = [*VALID_ROWS[:2], f"{T3},ARI-2024-7777777,99.00,,RETURNED"]

        report = validate_submission(live, write_submission_text(tmp_path, rows))

        assert report["ok"] is False
        assert codes(report) == {"ITEM_UNKNOWN"}
        assert report["errors"][0]["row"] == 4
        assert not report["warnings"]

    def test_final_also_looks_in_live(self, tmp_path: Path) -> None:
        build_phase(tmp_path, "train", TXNS, ITEMS)
        build_phase(tmp_path, "live", TXNS, [(CLOSED_IN_LIVE, 500, "AB1")])
        final = build_phase(tmp_path, "final", TXNS, ITEMS)
        rows = [*VALID_ROWS[:2], f"{T3},{CLOSED_IN_LIVE},5.00,,DUPLICATE_PAYMENT"]

        report = validate_submission(final, write_submission_text(tmp_path, rows))

        assert report["ok"] is True
        assert warning(report, "ITEM_FROM_PRIOR_PHASE")["samples"] == [CLOSED_IN_LIVE]

    def test_an_id_that_could_not_be_checked_is_not_valid_and_says_why(self, tmp_path: Path) -> None:
        live = build_phase(tmp_path, "live", TXNS, ITEMS)
        rows = [*VALID_ROWS[:2], f"{T3},{CLOSED_IN_TRAIN},99.00,,RETURNED"]

        report = validate_submission(live, write_submission_text(tmp_path, rows))

        assert report["ok"] is False
        assert codes(report) == {"ITEM_UNVERIFIED"}
        assert warning(report, "PRIOR_PHASE_MISSING")["samples"] == ["train"]

    def test_one_missing_file_of_an_earlier_phase_also_counts_as_not_checked(self, live: Layout, tmp_path: Path) -> None:
        (live.root / "train" / "credit_notes.csv.gz").unlink()
        rows = [*VALID_ROWS[:2], f"{T3},ARC-2024-7777777,99.00,,"]

        report = validate_submission(live, write_submission_text(tmp_path, rows))

        assert report["ok"] is False
        assert codes(report) == {"ITEM_UNVERIFIED"}

    def test_missing_earlier_phase_does_not_matter_when_every_id_is_in_the_phase(self, tmp_path: Path) -> None:
        live = build_phase(tmp_path, "live", TXNS, ITEMS)

        report = validate_submission(live, write_submission_text(tmp_path, VALID_ROWS))

        assert report["ok"] is True
        assert not report["warnings"]

    def test_train_does_not_look_anywhere_else(self, train: Layout, tmp_path: Path) -> None:
        rows = [*VALID_ROWS[:2], f"{T3},{CLOSED_IN_TRAIN},99.00,,RETURNED"]

        report = validate_submission(train, write_submission_text(tmp_path, rows))

        assert report["ok"] is False
        assert codes(report) == {"ITEM_UNKNOWN"}
        assert not report["warnings"]


class TestDamagedPhaseFiles:
    """DuckDB reads a truncated .csv.gz without error and returns only part of the rows: a valid id would look unknown."""

    def truncate(self, path: Path, last_id: str) -> None:
        """Many incompressible rows with the id at the very end, then half of the gzip stream is cut off."""
        header = gzip.decompress(path.read_bytes()).decode().splitlines()[0]
        filler = [f"ARI-2000-{n:07d}{',' * header.count(',')}{uuid.uuid4().hex}" for n in range(20_000)]
        whole = gzip.compress("\n".join([header, *filler, f"{last_id}{',' * header.count(',')}"]).encode() + b"\n")
        path.write_bytes(whole[: len(whole) // 2])

    def test_id_lost_in_a_damaged_earlier_phase_could_not_be_checked(self, live: Layout, tmp_path: Path) -> None:
        self.truncate(live.root / "train" / "ar_invoices.csv.gz", CLOSED_IN_TRAIN)
        rows = [*VALID_ROWS[:2], f"{T3},{CLOSED_IN_TRAIN},99.00,,RETURNED"]

        report = validate_submission(live, write_submission_text(tmp_path, rows))

        assert report["ok"] is False
        assert codes(report) == {"ITEM_UNVERIFIED"}
        assert warning(report, "PRIOR_PHASE_MISSING")["samples"] == ["train"]

    def test_damaged_item_file_of_the_phase_is_named_not_blamed_on_the_submission(self, live: Layout, tmp_path: Path) -> None:
        self.truncate(live.phase_dir / "ar_invoices.csv.gz", ITEM_A)

        with pytest.raises(PhaseDataUnreadable, match="ar_invoices"):
            validate_submission(live, write_submission_text(tmp_path, VALID_ROWS))

    def test_damaged_bank_file_of_the_phase_is_named(self, live: Layout, tmp_path: Path) -> None:
        path = live.phase_dir / "bank_transactions.csv.gz"
        self.truncate(path, T3)

        with pytest.raises(PhaseDataUnreadable, match="bank_transactions"):
            validate_submission(live, write_submission_text(tmp_path, VALID_ROWS))

    def test_missing_opening_items_are_named(self, live: Layout, tmp_path: Path) -> None:
        (live.phase_dir / "open_items_at_start.csv.gz").unlink()

        with pytest.raises(PhaseDataUnreadable, match="open_items_at_start"):
            validate_submission(live, write_submission_text(tmp_path, VALID_ROWS))

    def test_no_older_report_survives_damaged_phase_data(self, live: Layout, tmp_path: Path) -> None:
        path = write_submission_text(tmp_path, VALID_ROWS)
        old_report = Path(validate_submission(live, path)["report_path"])
        self.truncate(live.phase_dir / "ar_invoices.csv.gz", ITEM_A)

        with pytest.raises(PhaseDataUnreadable):
            validate_submission(live, path)

        assert not old_report.exists()


class TestRulesTheServerDoesNotEnforce:
    @pytest.mark.parametrize(
        ("row", "code"),
        [
            (f"{T3},{ITEM_B},0.00,,", "AMOUNT_NOT_POSITIVE"),
            (f"{T3},{ITEM_B},10.5,,", "AMOUNT_NOT_2_DECIMALS"),
            (f"{T3},,,,", "NO_ITEM_WITHOUT_CATEGORY"),
            (f"{T3},,,UNIDENTIFIED,", "UNIDENTIFIED_MISMATCH"),
            (f"{T3},,,BANK_FEES,UNIDENTIFIED", "UNIDENTIFIED_MISMATCH"),
            (f"{T3},{ITEM_B},10.00,,UNIDENTIFIED", "UNIDENTIFIED_MISMATCH"),
            (f"{T3},,,BANK_FEES,RETURNED", "NO_ITEM_EXCEPTION"),
        ],
    )
    def test_server_accepts_it_but_our_gate_stops_it(self, live: Layout, tmp_path: Path, row: str, code: str) -> None:
        report = validate_submission(live, write_submission_text(tmp_path, [*VALID_ROWS[:2], row]))

        assert report["official_format_ok"] is True
        assert report["ok"] is False
        assert codes(report) == {code}
        assert report["error_counts"] == {code: 1}

    def test_values_are_compared_as_the_server_reads_them(self, live: Layout, tmp_path: Path) -> None:
        rows = [*VALID_ROWS[:2], f"{T3},,,unidentified , Unidentified"]

        report = validate_submission(live, write_submission_text(tmp_path, rows))

        assert report["official_format_ok"] is True
        assert report["ok"] is True

    def test_counts_every_error_beyond_the_ones_it_lists(self, tmp_path: Path) -> None:
        many = [(f"BT-2025-{n:07d}", 100, "X") for n in range(1, 81)]
        layout = build_phase(tmp_path, "train", many, ITEMS)
        rows = [f"{txn},,,," for txn, _, _ in many]

        report = validate_submission(layout, write_submission_text(tmp_path, rows))

        assert report["error_counts"] == {"NO_ITEM_WITHOUT_CATEGORY": 80}
        assert len(report["errors"]) == 50


class TestCoverage:
    def test_missing_lines_are_an_error_with_their_own_code(self, live: Layout, tmp_path: Path) -> None:
        report = validate_submission(live, write_submission_text(tmp_path, VALID_ROWS[:2]))

        assert report["official_format_ok"] is True
        assert report["ok"] is False
        assert report["error_counts"] == {"MISSING_LINES": 1}
        assert report["coverage"]["missing_samples"] == [T3]

    def test_a_line_lost_by_the_warehouse_still_counts_as_missing(self, live: Layout, tmp_path: Path) -> None:
        """The server counts against the phase CSV: the warehouse may have rejected a row or be out of date."""
        run_on_warehouse(live, f"DELETE FROM bank_txn WHERE bank_txn_id = '{T3}'")

        report = validate_submission(live, write_submission_text(tmp_path, VALID_ROWS[:2]))

        assert report["ok"] is False
        assert report["coverage"]["bank_lines"] == 3
        assert report["coverage"]["missing_samples"] == [T3]

    def test_a_line_only_in_the_warehouse_is_not_required(self, live: Layout, tmp_path: Path) -> None:
        run_on_warehouse(live, "INSERT INTO bank_txn (bank_txn_id, amount_cents) VALUES ('BT-2025-0000099', 100)")

        report = validate_submission(live, write_submission_text(tmp_path, VALID_ROWS))

        assert report["ok"] is True
        assert report["coverage"]["bank_lines"] == 3


class TestItemsOfThePhase:
    def test_item_lost_by_the_warehouse_still_exists(self, live: Layout, tmp_path: Path) -> None:
        run_on_warehouse(live, f"DELETE FROM item WHERE item_id = '{ITEM_A}'")

        report = validate_submission(live, write_submission_text(tmp_path, VALID_ROWS))

        assert report["ok"] is True

    def test_opening_items_belong_to_the_phase(self, tmp_path: Path) -> None:
        build_phase(tmp_path, "train", TXNS, ITEMS)
        opening_item = "ARI-2024-0000005"
        live = build_phase(tmp_path, "live", TXNS, ITEMS, opening=[opening_item])
        rows = [*VALID_ROWS[:2], f"{T3},{opening_item},50.00,,"]

        report = validate_submission(live, write_submission_text(tmp_path, rows))

        assert report["ok"] is True
        assert not report["warnings"]


class TestIngestState:
    def test_ingest_that_recorded_errors_stops_the_gate(self, tmp_path: Path) -> None:
        build_phase(tmp_path, "train", TXNS, ITEMS)
        live = build_phase(tmp_path, "live", TXNS, ITEMS, ingest_errors=2)

        report = validate_submission(live, write_submission_text(tmp_path, VALID_ROWS))

        assert report["official_format_ok"] is True
        assert report["ok"] is False
        assert report["error_counts"] == {"INGEST_HAD_ERRORS": 2}

    def test_ingest_state_that_cannot_be_read_is_said_out_loud(self, live: Layout, tmp_path: Path) -> None:
        live.warehouse_path.unlink()

        report = validate_submission(live, write_submission_text(tmp_path, VALID_ROWS))

        assert report["ok"] is True
        assert warning(report, "INGEST_NOT_CHECKED")["count"] == 1


class TestOfficialVerdictWins:
    def test_our_rules_never_turn_a_server_rejection_into_valid(self, live: Layout, tmp_path: Path) -> None:
        rows = [*VALID_ROWS[:2], f"{T3},,9.99,,UNIDENTIFIED"]

        report = validate_submission(live, write_submission_text(tmp_path, rows))

        assert report["official_format_ok"] is False
        assert report["ok"] is False
        assert report["error_counts"]["OFFICIAL"] == 1


class TestUnreadableFiles:
    """In FINAL a broken file must give a verdict, not a traceback."""

    @pytest.mark.parametrize(
        ("name", "content"),
        [
            ("cut.csv.gz", gzip.compress((HEADER + "\n" + f"{T3},,,BANK_FEES,\n" * 5000).encode())[:200]),
            ("junk.csv.gz", b"\x1f\x8b" + b"not really gzip"),
            ("fake.csv.gz", f"{HEADER}\n".encode()),
            ("latin.csv", f"{HEADER}\n{T3},,,BANK_FEES,ñ\n".encode("latin-1")),
            ("huge_field.csv", f"{HEADER}\n{T3},,,{'X' * 200_000},\n".encode()),
            ("empty.csv", b""),
        ],
    )
    def test_gives_a_verdict(self, live: Layout, tmp_path: Path, name: str, content: bytes) -> None:
        path = tmp_path / name
        path.write_bytes(content)

        report = validate_submission(live, path)

        assert report["ok"] is False
        assert report["errors"]

    def test_truncated_gzip_is_stopped_whatever_the_server_code_makes_of_it(self, live: Layout, tmp_path: Path) -> None:
        whole = gzip.compress(("\n".join([HEADER, *VALID_ROWS]) + "\n").encode())
        path = tmp_path / "cut.csv.gz"
        path.write_bytes(whole[:-9])

        report = validate_submission(live, path)

        assert report["ok"] is False
        assert "UNREADABLE_FILE" in report["error_counts"]

    def test_garbage_after_the_gzip_stream_is_stopped(self, live: Layout, tmp_path: Path) -> None:
        path = tmp_path / "tail.csv.gz"
        path.write_bytes(gzip.compress(("\n".join([HEADER, *VALID_ROWS]) + "\n").encode()) + b"garbage")

        assert validate_submission(live, path)["ok"] is False

    @pytest.mark.parametrize("position", ["middle", "end"])
    def test_an_empty_gzip_member_is_not_damage(self, live: Layout, tmp_path: Path, position: str) -> None:
        first = gzip.compress(f"{HEADER}\n{VALID_ROWS[0]}\n".encode())
        rest = gzip.compress(("\n".join(VALID_ROWS[1:]) + "\n").encode())
        empty = gzip.compress(b"")
        path = tmp_path / "members.csv.gz"
        path.write_bytes(first + empty + rest if position == "middle" else first + rest + empty)

        report = validate_submission(live, path)

        assert report["official_format_ok"] is True
        assert report["ok"] is True

    def test_several_gzip_members_read_as_one_file(self, live: Layout, tmp_path: Path) -> None:
        path = tmp_path / "members.csv.gz"
        path.write_bytes(gzip.compress(f"{HEADER}\n{VALID_ROWS[0]}\n".encode()) + gzip.compress(("\n".join(VALID_ROWS[1:]) + "\n").encode()))

        report = validate_submission(live, path)

        assert report["official_format_ok"] is True
        assert report["ok"] is True

    def test_gzip_above_the_limit_is_not_inflated_whole(self, live: Layout, tmp_path: Path) -> None:
        """Blank-looking lines, not one huge field: only the size guard can stop this file from being read in full."""
        path = tmp_path / "bomb.csv.gz"
        blank_lines = (" " * 100_000 + "\n") * 900
        path.write_bytes(gzip.compress(("\n".join([HEADER, *VALID_ROWS]) + "\n" + blank_lines).encode(), compresslevel=1))

        report = validate_submission(live, path)

        assert report["official_format_ok"] is False
        assert report["ok"] is False
        assert report["uncompressed_mb"] is None
        assert "UNREADABLE_FILE" in report["error_counts"]


class TestMastersOfFinal:
    def test_final_takes_its_categories_from_its_own_masters(self, tmp_path: Path) -> None:
        build_phase(tmp_path, "train", TXNS, ITEMS)
        build_phase(tmp_path, "live", TXNS, ITEMS)
        build_phase(tmp_path, "final", TXNS, ITEMS)
        masters_final = tmp_path / "final" / "masters_final"
        masters_final.mkdir()
        (masters_final / "categories.csv").write_text("category,description,gl_account\nBRAND_NEW,,\nUNIDENTIFIED,,\n", encoding="utf-8")
        final = resolve_layout("final", data_root=tmp_path)
        rows = [*VALID_ROWS[:2], f"{T3},,,BRAND_NEW,"]

        report = validate_submission(final, write_submission_text(tmp_path, rows))

        assert report["official_format_ok"] is True
        assert report["ok"] is True


class TestDocumentedFormat:
    def test_semicolon_file_is_stopped_and_the_message_says_the_server_would_take_it(self, live: Layout, tmp_path: Path) -> None:
        path = tmp_path / "semi.csv"
        rows = [f"{T1};{ITEM_A};1210,00;;", f"{T2};{ITEM_B};605,00;;SHORT_DISCOUNT", f"{T3};;;UNIDENTIFIED;UNIDENTIFIED"]
        path.write_text("\n".join([HEADER.replace(",", ";"), *rows]) + "\n", encoding="utf-8")

        report = validate_submission(live, path)

        assert report["official_format_ok"] is True
        assert report["ok"] is False
        assert codes(report) == {"NOT_CANONICAL"}
        assert "servidor" in report["errors"][0]["message"]


class TestLimits:
    def test_upload_limit_binds_live_even_after_lifting_it_for_train(self, tmp_path: Path) -> None:
        train = build_phase(tmp_path, "train", TXNS, ITEMS)
        live = build_phase(tmp_path, "live", TXNS, ITEMS)
        big = tmp_path / "big.csv"
        big.write_text(over_60_mb(VALID_ROWS), encoding="utf-8")

        train_report = validate_submission(train, big)
        live_report = validate_submission(live, big)

        assert train_report["ok"] is True
        assert train_report["limits_applied"] is False
        assert live_report["ok"] is False
        assert live_report["official_format_ok"] is False

    def test_scoring_train_accepts_a_file_over_the_upload_limit(self, train: Layout, tmp_path: Path) -> None:
        write_golden(train, [f"{T1},{ITEM_A},1210.00,,,seg,1"])
        big = tmp_path / "big.csv"
        big.write_text(over_60_mb([f"{T1},{ITEM_A},1210.00,,"]), encoding="utf-8")

        assert score_submission_file(train, big)["ok"] is True

    def test_lifting_limits_changes_only_the_three_size_constants(self, train: Layout) -> None:
        official = load_official_scorer(train)
        lifted = lift_limits(load_official_scorer(train))

        changed = {name for name in vars(official) if name.isupper() and vars(official)[name] != vars(lifted)[name]}

        assert changed == {"MAX_ROWS", "MAX_UNCOMPRESSED", "MAX_DECOMPRESS"}


class TestScorerIntegrity:
    def test_loading_the_scorer_leaves_the_organisers_folder_untouched(self, live: Layout) -> None:
        before = sorted(path.name for path in live.starter_dir.iterdir())

        load_official_scorer(live)

        assert sorted(path.name for path in live.starter_dir.iterdir()) == before

    def test_the_scorer_that_runs_is_the_one_whose_bytes_were_checked(self, live: Layout, monkeypatch: pytest.MonkeyPatch) -> None:
        """If the file changes between the check and the load, the checked bytes are still the ones that run."""
        scorer_file = live.starter_dir / "bankrec_score.py"
        checked = scorer_file.read_bytes()
        reads = []
        real_read_bytes = Path.read_bytes

        def read_then_tamper(path: Path) -> bytes:
            content = real_read_bytes(path)
            if path == scorer_file:
                reads.append(path)
                path.write_bytes(checked.replace(b"W_ALLOC, W_TXN, W_EXC = 0.5", b"W_ALLOC, W_TXN, W_EXC = 0.9"))
            return content

        monkeypatch.setattr(Path, "read_bytes", read_then_tamper)

        scorer = load_official_scorer(live)

        assert len(reads) == 1
        assert scorer.W_ALLOC == 0.5

    def test_a_modified_scorer_stops_the_check(self, live: Layout, tmp_path: Path) -> None:
        scorer = live.starter_dir / "bankrec_score.py"
        scorer.write_text(scorer.read_text(encoding="utf-8").replace("W_ALLOC, W_TXN, W_EXC = 0.5", "W_ALLOC, W_TXN, W_EXC = 0.9"), encoding="utf-8")

        with pytest.raises(ScorerChanged):
            validate_submission(live, write_submission_text(tmp_path, VALID_ROWS))


class TestScore:
    def test_score_matches_the_hand_computed_value(self, train: Layout, tmp_path: Path) -> None:
        write_golden(
            train,
            [f"{T1},{ITEM_A},1210.00,,,ar_1to1,1", f"{T2},{ITEM_B},605.00,,PARTIAL,ap_partial,2", f"{T3},,,BANK_FEES,,nonitem,1"],
        )
        rows = [f"{T1},{ITEM_A},1210.00,,", f"{T2},{ITEM_B},605.00,,", f"{T3},,,BANK_FEES,"]

        result = score_submission_file(train, write_submission_text(tmp_path, rows))

        # F1 = 1 (2 of 2 pairs). Accuracy = 2/3 (T2 lost its exception). Macro-F1 = 0 (PARTIAL never predicted).
        assert result["score"] == 70.0
        assert result["segments"]["ap_partial"] == {"n": 1, "accuracy": 0.0}
        assert result["segments"]["ar_1to1"] == {"n": 1, "accuracy": 1.0}

    def test_phase_without_labels_is_refused_even_if_a_golden_file_appears(self, live: Layout, tmp_path: Path) -> None:
        write_golden(live, [f"{T1},{ITEM_A},1210.00,,,seg,1"])

        with pytest.raises(ValueError):
            score_submission_file(live, write_submission_text(tmp_path, VALID_ROWS))

    def test_rejected_file_is_reported_as_not_ok(self, train: Layout, tmp_path: Path) -> None:
        write_golden(train, [f"{T1},{ITEM_A},1210.00,,,seg,1"])

        result = score_submission_file(train, write_submission_text(tmp_path, [f"{T3},,9.99,,UNIDENTIFIED"]))

        assert result["ok"] is False
        assert result["score"] is None


class TestReports:
    def test_a_crash_never_leaves_an_older_valid_report_behind(self, live: Layout, tmp_path: Path) -> None:
        path = write_submission_text(tmp_path, VALID_ROWS)
        old_report = Path(validate_submission(live, path)["report_path"])
        assert old_report.is_file()
        (live.starter_dir / "bankrec_score.py").write_text("changed", encoding="utf-8")

        with pytest.raises(ScorerChanged):
            validate_submission(live, path)

        assert not old_report.exists()

    def test_each_submission_keeps_its_own_validation_report(self, live: Layout, tmp_path: Path) -> None:
        first = validate_submission(live, write_submission_text(tmp_path, VALID_ROWS, "matcher.csv"))
        second = validate_submission(live, write_submission_text(tmp_path, VALID_ROWS[:2], "baseline.csv"))

        assert json.loads(Path(first["report_path"]).read_text(encoding="utf-8"))["ok"] is True
        assert json.loads(Path(second["report_path"]).read_text(encoding="utf-8"))["ok"] is False

    def test_validation_never_shows_the_meaningless_score_of_an_empty_golden(self, live: Layout, tmp_path: Path) -> None:
        report = validate_submission(live, write_submission_text(tmp_path, VALID_ROWS))

        saved = json.loads(Path(report["report_path"]).read_text(encoding="utf-8"))

        assert "score" not in report
        assert "metrics" not in report
        assert "score" not in saved

    def test_validation_summarises_what_the_file_says(self, live: Layout, tmp_path: Path) -> None:
        report = validate_submission(live, write_submission_text(tmp_path, VALID_ROWS))

        assert report["breakdown"] == {
            "rows_with_item": 2,
            "rows_without_item": 1,
            "by_category": {"UNIDENTIFIED": 1},
            "by_exception": {"SHORT_DISCOUNT": 1, "UNIDENTIFIED": 1},
        }

    def test_score_is_saved_with_the_scorer_fingerprint(self, train: Layout, tmp_path: Path) -> None:
        write_golden(train, [f"{T1},{ITEM_A},1210.00,,,seg,1"])

        result = score_submission_file(train, write_submission_text(tmp_path, [f"{T1},{ITEM_A},1210.00,,"], "matcher.csv"))

        saved = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))
        assert saved["score"] == result["score"]
        assert saved["limits_lifted"] is True
        assert saved["scorer_sha256"] == "1cc74ec1cda3aac6351bf8353cbc38adc81a7b126f9e8c1d75cc5e08bc45fd81"


class TestWriter:
    ROWS = [(T1, ITEM_A, "1210.00", "", ""), (T3, "", "", "UNIDENTIFIED", "UNIDENTIFIED")]

    def test_writes_the_documented_format(self, tmp_path: Path) -> None:
        path = tmp_path / "out.csv"

        count = write_submission(path, self.ROWS)

        assert count == 2
        assert path.read_bytes() == f"{HEADER}\n{T1},{ITEM_A},1210.00,,\n{T3},,,UNIDENTIFIED,UNIDENTIFIED\n".encode()

    def test_gzip_output_does_not_depend_on_the_file_name_or_the_clock(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        first, second = tmp_path / "a.csv.gz", tmp_path / "b.csv.gz"

        monkeypatch.setattr("gzip.time.time", lambda: 1_000_000.0)
        write_submission(first, self.ROWS)
        monkeypatch.setattr("gzip.time.time", lambda: 2_000_000.0)
        write_submission(second, self.ROWS)

        assert first.read_bytes() == second.read_bytes()
        assert gzip.decompress(first.read_bytes()).startswith(HEADER.encode())

    def test_a_failure_while_writing_leaves_the_previous_file_intact(self, tmp_path: Path) -> None:
        path = tmp_path / "out.csv"
        write_submission(path, self.ROWS)
        before = path.read_bytes()

        def failing_rows():
            yield self.ROWS[0]
            raise RuntimeError("matcher crashed")

        with pytest.raises(RuntimeError):
            write_submission(path, failing_rows())

        assert path.read_bytes() == before
        assert [entry.name for entry in tmp_path.iterdir()] == ["out.csv"]

    def test_baseline_is_reproducible_byte_for_byte(self, live: Layout, tmp_path: Path) -> None:
        first, second = tmp_path / "a.csv.gz", tmp_path / "b.csv.gz"

        write_baseline(live, first)
        write_baseline(live, second)

        assert first.read_bytes() == second.read_bytes()


class TestBaselineMatching:
    """A wrong match costs precision and hides real gaps in the gate, so only the exact case may match."""

    def matched(self, layout: Layout, tmp_path: Path) -> dict[str, str]:
        out = tmp_path / "base.csv.gz"
        write_baseline(layout, out)
        with gzip.open(out, "rt", encoding="utf-8", newline="") as handle:
            return {row["bank_txn_id"]: row["item_id"] for row in csv.DictReader(handle) if row["item_id"]}

    def test_printed_number_with_exact_amount_matches(self, tmp_path: Path) -> None:
        layout = build_phase(tmp_path, "live", [(T1, 121000, "PAGO FV25-0000001")], ITEMS)

        assert self.matched(layout, tmp_path) == {T1: ITEM_A}

    def test_a_different_amount_does_not_match(self, tmp_path: Path) -> None:
        layout = build_phase(tmp_path, "live", [(T1, 121001, "PAGO FV25-0000001")], ITEMS)

        assert self.matched(layout, tmp_path) == {}

    def test_a_different_currency_does_not_match(self, tmp_path: Path) -> None:
        layout = build_phase(tmp_path, "live", [(T1, 121000, "PAGO FV25-0000001")], ITEMS)
        run_on_warehouse(layout, "UPDATE item SET currency = 'USD'")

        assert self.matched(layout, tmp_path) == {}

    def test_a_debit_does_not_settle_a_customer_invoice(self, tmp_path: Path) -> None:
        layout = build_phase(tmp_path, "live", [(T1, -121000, "PAGO FV25-0000001")], ITEMS)

        assert self.matched(layout, tmp_path) == {}

    def test_a_short_token_is_not_a_reference(self, tmp_path: Path) -> None:
        layout = build_phase(tmp_path, "live", [(T1, -60500, "PAGO P1")], ITEMS)

        assert self.matched(layout, tmp_path) == {}

    def test_an_invoice_is_settled_only_once(self, tmp_path: Path) -> None:
        layout = build_phase(tmp_path, "live", [(T1, 121000, "PAGO FV25-0000001"), (T2, 121000, "OTRA VEZ FV25-0000001")], ITEMS)

        assert self.matched(layout, tmp_path) == {T1: ITEM_A}


class TestLabelsStayOut:
    def test_baseline_and_validation_never_read_the_labels(self, train: Layout, tmp_path: Path) -> None:
        (train.phase_dir / "golden_allocations.csv.gz").write_bytes(b"this is not gzip, reading it would crash")
        out = tmp_path / "base.csv.gz"

        write_baseline(train, out)

        assert validate_submission(train, out)["ok"] is True
