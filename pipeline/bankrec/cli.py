"""Command line of the pipeline. Nothing here talks to the submission platform."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import duckdb

from .roots import PHASES, DataRootNotFound, Layout, resolve_layout

EXIT_OK, EXIT_PROBLEMS, EXIT_USAGE = 0, 1, 2
logger = logging.getLogger("bankrec")


def _configure_logging(layout: Layout, command: str) -> None:
    layout.report_dir.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(layout.report_dir / f"{command}.log", mode="w", encoding="utf-8"),
    ]
    logging.basicConfig(level=logging.INFO, format="%(message)s", handlers=handlers, force=True)


def _ingest(args: argparse.Namespace, layout: Layout) -> int:
    from .ingest import run_ingest

    logger.info("Raíz de datos: %s\nFase: %s (%s)\nMaestros: %s", layout.root, layout.phase, layout.phase_dir, layout.masters_override_dir or layout.masters_dir)
    result = run_ingest(layout, allow_errors=args.allow_errors)
    if not result.ok:
        logger.error("La ingesta terminó con errores: revisa %s", layout.report_dir / "ingest_report.json")
    return EXIT_OK if result.ok else EXIT_PROBLEMS


def _baseline(args: argparse.Namespace, layout: Layout) -> int:
    from .submission.baseline import write_baseline

    out = Path(args.out) if args.out else layout.report_dir / "baseline.csv.gz"
    rows, matched = write_baseline(layout, out)
    logger.info("%s filas (%s movimientos casados) -> %s", f"{rows:,}", f"{matched:,}", out)
    return EXIT_OK


def _validate(args: argparse.Namespace, layout: Layout) -> int:
    from .submission.validate import print_validation, validate_submission

    report = validate_submission(layout, Path(args.submission))
    print_validation(report, layout)
    return EXIT_OK if report["ok"] else EXIT_PROBLEMS


def _score(args: argparse.Namespace, layout: Layout) -> int:
    from .submission.score import print_score, score_submission_file

    result = score_submission_file(layout, Path(args.submission))
    print_score(result)
    return EXIT_OK if result["ok"] else EXIT_PROBLEMS


def _match(args: argparse.Namespace, layout: Layout) -> int:
    from .matching.remittance import run_remittance

    run_remittance(layout)
    return EXIT_OK


def _pdf(args: argparse.Namespace, layout: Layout) -> int:
    from .pdf_index import pdf_text

    if not layout.warehouse_path.is_file():
        logger.error("No hay almacén de la fase %s: lanza antes `bankrec ingest`", layout.phase)
        return EXIT_PROBLEMS
    con = duckdb.connect(str(layout.warehouse_path))
    try:
        found = pdf_text(con, layout, args.reference)
    finally:
        con.close()
    if not found:
        logger.error("Ningún PDF en disco para: %s", args.reference)
        return EXIT_PROBLEMS
    for file, text in found.items():
        logger.info("--- %s\n%s", file, text)
    return EXIT_OK


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bankrec", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    def command(name: str, handler, help_text: str, phases: tuple[str, ...] = PHASES) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, help=help_text)
        sub.add_argument("--phase", required=True, choices=phases)
        sub.add_argument("--data-root", type=Path, default=None, help="carpeta con <fase>/ y masters/; si no, se autodetecta")
        sub.set_defaults(handler=handler)
        return sub

    command("ingest", _ingest, "carga completa de una fase en su almacén").add_argument(
        "--allow-errors", action="store_true", help="publica el almacén aunque haya errores; el código de salida sigue siendo distinto de 0"
    )
    command("baseline", _baseline, "salida válida mínima, para ejercitar la validación").add_argument("--out", default=None)
    command("validate", _validate, "validación local de formato y cobertura").add_argument("submission")
    command("score", _score, "puntuación con el scorer oficial (solo con golden)", ("train", "sample")).add_argument("submission")
    command("match", _match, "etapas de matching disponibles (hoy, avisos de remesa); escribe en out/<fase>/matches/")
    command("pdf", _pdf, "texto de un PDF por fichero o número impreso").add_argument("reference")
    commands.add_parser("verify", help="comprobación local completa e informe: TRAIN con nota, LIVE con validación y cobertura", add_help=False)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments[:1] == ["verify"]:
        from .submission.verify import main as verify_main

        return verify_main(arguments[1:])
    args = _parser().parse_args(arguments)
    try:
        layout = resolve_layout(args.phase, data_root=args.data_root)
    except DataRootNotFound as error:
        sys.stderr.write(f"{error}\n")
        return EXIT_USAGE
    _configure_logging(layout, args.command)
    return args.handler(args, layout)


if __name__ == "__main__":
    raise SystemExit(main())
