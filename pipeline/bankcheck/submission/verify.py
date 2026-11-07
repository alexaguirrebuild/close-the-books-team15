"""One command for the whole local check: phases with labels get a score, the others validation and coverage.

Any session can run it on its own files. It never talks to the platform, so it spends no upload slot.

    bankcheck verify                                 baseline of TRAIN and LIVE
    bankcheck verify --file live=out/live/x.csv.gz   a file of yours, only that phase
    bankcheck verify --phase train --self-check      also: the labels as a submission must score 100 and pass the gate
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from ..roots import DATA_DIRNAME, PHASES, DataRootNotFound, Layout, resolve_layout
from .baseline import write_baseline
from .official import SCORER_SHA256
from .score import PHASES_WITH_GOLDEN, golden_as_submission, print_score, score_submission_file
from .validate import print_validation, validate_submission
from .writer import write_json, write_text

EXIT_OK, EXIT_PROBLEMS = 0, 1
DEFAULT_PHASES = ("train", "live")
PERFECT_SCORE = 100.0
SEGMENTS_SHOWN = 15
ERRORS_SHOWN = 10
REPORT_NAMES = ("report.json", "report.md")
logger = logging.getLogger("bankcheck")


def _self_check(layout: Layout) -> dict:
    """Keeps only this summary: nothing derived from the labels, not even its aggregates, stays on disk."""
    with golden_as_submission(layout) as path:
        score = score_submission_file(layout, path)
        validation = validate_submission(layout, path)
    for report in (score, validation):
        Path(report["report_path"]).unlink(missing_ok=True)
    return {
        "ok": bool(score["ok"]) and score["score"] == PERFECT_SCORE and validation["ok"],
        "score": score["score"],
        "validation_ok": validation["ok"],
        "errors": validation["errors"][:ERRORS_SHOWN],
    }


def _baseline(layout: Layout) -> Path:
    path = layout.report_dir / "baseline.csv.gz"
    rows, matched = write_baseline(layout, path)
    logger.info("Baseline de %s: %s filas, %s movimientos casados", layout.phase, f"{rows:,}", f"{matched:,}")
    return path


def _verify_phase(layout: Layout, submission: Path | None, self_check: bool) -> dict:
    path = submission or _baseline(layout)
    validation = validate_submission(layout, path)
    print_validation(validation, layout)
    result: dict = {"file": str(path), "validation": validation}
    if layout.phase in PHASES_WITH_GOLDEN:
        result["score"] = score_submission_file(layout, path)
        print_score(result["score"])
        if self_check:
            result["self_check"] = _self_check(layout)
    parts = [validation, result.get("score", {"ok": True}), result.get("self_check", {"ok": True})]
    return {"ok": all(part["ok"] for part in parts), **result}


def _run_phase(phase: str, data_root: Path | None, submission: Path | None, self_check: bool) -> dict:
    """The boundary of a run: whatever goes wrong in a phase is a finding in the report and never stops the other phases."""
    try:
        return _verify_phase(resolve_layout(phase, data_root=data_root), submission, self_check)
    except Exception as error:
        logger.exception("La fase %s no se pudo comprobar", phase)
        return {"ok": False, "error": f"{type(error).__name__}: {error}"}


def _report_root(data_root: Path | None, phases: list[str]) -> Path:
    if data_root is not None:
        return data_root
    for phase in (*phases, *PHASES):
        try:
            return resolve_layout(phase).root
        except DataRootNotFound:
            continue
    return Path.cwd()


def _score_section(score: dict) -> list[str]:
    if not score["ok"]:
        return ["", "**Puntuación:** fichero rechazado por el scorer oficial."]
    metrics = score["metrics"]
    lines = [
        "",
        f"**Puntuación: {score['score']:.2f}** (scorer oficial sin modificar; solo se levantan sus 3 límites de tamaño)",
        "",
        "| Métrica | Valor |",
        "|---|---|",
        f"| Allocation F1 (50 %) | {metrics['allocation_f1']:.4f} |",
        f"| Precisión | {metrics['precision']:.4f} |",
        f"| Recall | {metrics['recall']:.4f} |",
        f"| Transaction accuracy (30 %) | {metrics['txn_accuracy']:.4f} |",
        f"| Exception macro-F1 (20 %) | {metrics['exception_macro_f1']:.4f} |",
        f"| Exactitud por importe (orientativa) | {metrics['amount_weighted_accuracy']:.4f} |",
        "",
        f"| Segmento (los {SEGMENTS_SHOWN} con más movimientos) | Movimientos | Exactitud |",
        "|---|---:|---:|",
    ]
    segments = sorted(score["segments"].items(), key=lambda entry: -entry[1]["n"])[:SEGMENTS_SHOWN]
    lines.extend(f"| {name} | {data['n']:,} | {data['accuracy']:.3f} |" for name, data in segments)
    return lines


def _validation_section(validation: dict) -> list[str]:
    coverage = validation["coverage"]
    size = "desconocido" if validation["uncompressed_mb"] is None else f"{validation['uncompressed_mb']} MB"
    limits = "60 MB y 3 M filas aplicados" if validation["limits_applied"] else "sin límite: esta fase no se envía"
    lines = [
        f"- **Validación:** {'válido' if validation['ok'] else 'NO VÁLIDO'}. Formato oficial: {'OK' if validation['official_format_ok'] else 'RECHAZADO'}",
        f"- **Cobertura:** {coverage['covered']:,} de {coverage['bank_lines']:,} movimientos; faltan {coverage['missing']:,}",
        f"- **Tamaño:** {validation['rows']:,} filas, {size} sin comprimir ({limits})",
        f"- **Contenido:** {validation['breakdown']['rows_with_item']:,} filas con partida y {validation['breakdown']['rows_without_item']:,} sin partida",
    ]
    if validation["error_counts"]:
        lines.append(f"- **Errores por tipo:** {validation['error_counts']}")
    lines.extend(f"- **Aviso {entry['code']}** ({entry['count']:,}): {entry['message']}" for entry in validation["warnings"])
    return lines


def _phase_section(phase: str, result: dict) -> list[str]:
    lines = ["", f"## {phase.upper()}", ""]
    if "error" in result:
        return [*lines, f"**No se pudo comprobar:** {result['error']}"]
    lines.extend([f"- **Fichero:** `{result['file']}`", *_validation_section(result["validation"])])
    if "score" in result:
        lines.extend(_score_section(result["score"]))
    else:
        lines.extend(["", f"{phase.upper()} no tiene puntuación: no hay golden, y la nota solo la da la plataforma, gastando cupo."])
    if "self_check" in result:
        check = result["self_check"]
        verdict = "correcta" if check["ok"] else "FALLA"
        score = "ninguna: el scorer rechaza el fichero" if check["score"] is None else f"{check['score']:.2f}"
        lines.extend(["", f"**Autocomprobación con el golden como envío: {verdict}.** Nota: {score}. Pasa la validación: {'sí' if check['validation_ok'] else 'NO'}."])
    return lines


def _render_markdown(report: dict) -> str:
    lines = [
        "# Verificación local",
        "",
        f"**Resultado: {'OK' if report['ok'] else 'HAY PROBLEMAS'}** · {report['generated_at']}",
        "",
        f"No se ha subido nada ni se ha pedido ninguna URL de subida. Scorer SHA-256: `{report['scorer_sha256']}`",
    ]
    for phase, result in report["phases"].items():
        lines.extend(_phase_section(phase, result))
    return "\n".join(lines) + "\n"


def _reports_folder(root: Path) -> Path:
    project = root.parent if root.name == DATA_DIRNAME else root  # out/ vive fuera de data/, como en resolve_layout.
    return project / "out" / "verification"


def _clear_reports(root: Path) -> None:
    """Whatever happens in this run, the verdict of an earlier one must not pass for its result."""
    for name in REPORT_NAMES:
        (_reports_folder(root) / name).unlink(missing_ok=True)


def _write_reports(root: Path, report: dict) -> Path:
    """Rendered first, so a failure here cannot leave one of the two files without the other."""
    text = _render_markdown(report)
    folder = _reports_folder(root)
    write_json(folder / "report.json", report)
    write_text(folder / "report.md", text)
    return folder / "report.md"


def _parse_args(argv: list[str] | None) -> tuple[argparse.Namespace, dict[str, Path]]:
    parser = argparse.ArgumentParser(prog="bankcheck verify", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--phase", action="append", choices=PHASES, help=f"repetible; por defecto {' y '.join(DEFAULT_PHASES)}")
    parser.add_argument("--file", action="append", default=[], metavar="FASE=RUTA", help="fichero a comprobar en vez del baseline; repetible")
    parser.add_argument("--self-check", action="store_true", help="en fases con golden: el golden como envío debe dar 100,00 y pasar la validación")
    parser.add_argument("--data-root", type=Path, default=None, help="carpeta con <fase>/ y masters/; si no, se autodetecta")
    args = parser.parse_args(argv)
    files: dict[str, Path] = {}
    for pair in args.file:
        phase, separator, path = pair.partition("=")
        if not separator or phase not in PHASES or not path:
            parser.error(f"--file espera FASE=RUTA con FASE en {', '.join(PHASES)}; recibido: {pair}")
        if phase in files:
            parser.error(f"dos ficheros para la fase {phase}: compruébalos en dos ejecuciones")
        files[phase] = Path(path)
    return args, files


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout, force=True)
    args, files = _parse_args(argv)
    phases = list(dict.fromkeys([*(args.phase or []), *files])) or list(DEFAULT_PHASES)
    root = _report_root(args.data_root, phases)
    _clear_reports(root)
    results = {phase: _run_phase(phase, args.data_root, files.get(phase), args.self_check) for phase in phases}
    report = {
        "ok": all(result["ok"] for result in results.values()),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "scorer_sha256": SCORER_SHA256,
        "uploaded": False,
        "phases": results,
    }
    path = _write_reports(root, report)
    logger.info("\n%s  ·  informe: %s", "TODO OK" if report["ok"] else "HAY PROBLEMAS", path)
    return EXIT_OK if report["ok"] else EXIT_PROBLEMS


if __name__ == "__main__":
    raise SystemExit(main())
