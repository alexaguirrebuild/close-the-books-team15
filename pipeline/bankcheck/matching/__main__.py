"""python -m bankcheck.matching: its own entry point until cli.py is free to register it. Nothing here submits anything."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import duckdb

from ..roots import PHASES, DataRootNotFound, Layout, resolve_layout
from .merge import merge_stages
from .remittance import run_remittance
from .remittance_rules import SOURCE

EXIT_OK, EXIT_PROBLEMS, EXIT_USAGE = 0, 1, 2
DEFAULT_SINCE = "2024-07-01"
DEFAULT_MERGED = "submission_m2.csv.gz"
logger = logging.getLogger("bankcheck")


def _m2(args: argparse.Namespace, layout: Layout) -> int:
    run_remittance(layout)
    return EXIT_OK


def _merge(args: argparse.Namespace, layout: Layout) -> int:
    from ..submission.validate import print_validation, validate_submission

    report = validate_submission(layout, merge_stages(layout, args.stages.split(","), args.out))
    print_validation(report, layout)
    return EXIT_OK if report["ok"] else EXIT_PROBLEMS


def _evaluate(args: argparse.Namespace, layout: Layout) -> int:
    from .evaluate import evaluate_stage, print_evaluation

    result = evaluate_stage(layout, args.stage, args.since)
    print_evaluation(result)
    return EXIT_OK if result["window"]["ok"] and result["linked"]["ok"] else EXIT_PROBLEMS


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m bankcheck.matching", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    def command(name: str, handler, help_text: str) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, help=help_text)
        sub.add_argument("--phase", required=True, choices=PHASES)
        sub.add_argument("--data-root", type=Path, default=None)
        sub.set_defaults(handler=handler)
        return sub

    command("m2", _m2, "filas de los avisos de remesa")
    merge = command("merge", _merge, "une las etapas con el baseline en un envío candidato; no envía nada")
    merge.add_argument("--stages", default=SOURCE, help="etapas separadas por comas; gana la primera")
    merge.add_argument("--out", default=DEFAULT_MERGED)
    evaluate = command("evaluate", _evaluate, "puntuación oficial de una etapa; solo en fases con etiquetas")
    evaluate.add_argument("--stage", default=SOURCE)
    evaluate.add_argument("--since", default=DEFAULT_SINCE)
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout, force=True)
    args = _parser().parse_args(argv)
    try:
        layout = resolve_layout(args.phase, data_root=args.data_root)
    except DataRootNotFound as error:
        sys.stderr.write(f"{error}\n")
        return EXIT_USAGE
    try:
        return args.handler(args, layout)
    except (FileNotFoundError, ValueError, duckdb.Error) as error:
        logger.error("%s", error)
        return EXIT_PROBLEMS


if __name__ == "__main__":
    raise SystemExit(main())
