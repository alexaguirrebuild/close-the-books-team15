from pathlib import Path

import pytest

from bankrec.roots import DataRootNotFound, is_excluded, iter_phase_files, resolve_layout


def make_root(base: Path, phase: str = "live", docs_folder: bool = True) -> Path:
    phase_dir = base / phase
    phase_dir.mkdir(parents=True)
    (phase_dir / "bank_transactions.csv.gz").write_bytes(b"")
    masters = base / "bankrec-docs-and-masters" / "masters" if docs_folder else base / "masters"
    masters.mkdir(parents=True)
    return base


def test_explicit_root_wins_over_environment(tmp_path: Path) -> None:
    explicit = make_root(tmp_path / "explicit")
    other = make_root(tmp_path / "other")

    layout = resolve_layout("live", data_root=explicit, env={"BANKREC_DATA_ROOT": str(other)}, cwd=tmp_path, home=tmp_path)

    assert layout.root == explicit
    assert layout.phase_dir == explicit / "live"


def test_detects_team_layout_with_docs_folder(tmp_path: Path) -> None:
    root = make_root(tmp_path / "repo")

    layout = resolve_layout("live", env={}, cwd=root, home=tmp_path)

    assert layout.root == root
    assert layout.masters_dir == root / "bankrec-docs-and-masters" / "masters"


def test_detects_official_layout_with_masters_beside_phases(tmp_path: Path) -> None:
    root = make_root(tmp_path / "official", docs_folder=False)

    layout = resolve_layout("live", env={}, cwd=root, home=tmp_path)

    assert layout.masters_dir == root / "masters"


def test_ignores_a_candidate_without_masters(tmp_path: Path) -> None:
    orphan = tmp_path / "downloads"
    (orphan / "train").mkdir(parents=True)
    (orphan / "train" / "bank_transactions.csv.gz").write_bytes(b"")

    with pytest.raises(DataRootNotFound) as error:
        resolve_layout("train", env={}, cwd=orphan, home=tmp_path)

    assert str(orphan) in str(error.value)


def test_missing_phase_lists_where_it_searched(tmp_path: Path) -> None:
    root = make_root(tmp_path / "repo", phase="train")

    with pytest.raises(DataRootNotFound) as error:
        resolve_layout("final", env={}, cwd=root, home=tmp_path)

    assert "final" in str(error.value)
    assert str(root) in str(error.value)


def test_rejects_an_unknown_phase(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        resolve_layout("trian", env={}, cwd=tmp_path, home=tmp_path)


def test_sample_lives_inside_the_docs_folder(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    sample = root / "bankrec-docs-and-masters" / "sample"
    sample.mkdir(parents=True)
    (sample / "bank_transactions.csv.gz").write_bytes(b"")
    (root / "bankrec-docs-and-masters" / "masters").mkdir()

    layout = resolve_layout("sample", env={}, cwd=root, home=tmp_path)

    assert layout.phase_dir == sample


def test_final_uses_its_own_masters_when_present(tmp_path: Path) -> None:
    root = make_root(tmp_path / "repo", phase="final")
    (root / "final" / "masters_final").mkdir()

    layout = resolve_layout("final", env={}, cwd=root, home=tmp_path)

    assert layout.masters_override_dir == root / "final" / "masters_final"


def test_warehouse_is_one_file_per_phase(tmp_path: Path) -> None:
    root = make_root(tmp_path / "repo")

    layout = resolve_layout("live", env={}, cwd=root, home=tmp_path)

    assert layout.warehouse_path == root / "cache" / "warehouse" / "live.duckdb"


def test_golden_is_always_excluded() -> None:
    assert is_excluded(Path("train/golden_allocations.csv.gz"))
    assert is_excluded(Path("sample/GOLDEN.csv"))
    assert not is_excluded(Path("train/ar_invoices.csv.gz"))


def test_iter_phase_files_skips_golden_and_hidden_folders(tmp_path: Path) -> None:
    phase = tmp_path / "train"
    (phase / ".claude-flow").mkdir(parents=True)
    (phase / ".claude-flow" / "state.json").write_text("{}")
    (phase / "golden_allocations.csv.gz").write_bytes(b"")
    (phase / "ar_invoices.csv.gz").write_bytes(b"")
    (phase / ".DS_Store").write_bytes(b"")

    names = [path.name for path in iter_phase_files(phase)]

    assert names == ["ar_invoices.csv.gz"]
