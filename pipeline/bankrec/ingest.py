"""One command: build the warehouse of a single phase from its folder, or leave the previous one intact."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from .alerts import check_every_file_was_read, run_alerts
from .aux_files import load_aux_files
from .db import IngestContext, building_path, keep_failed_build, open_build, publish_build
from .issues import IssueLog, Manifest
from .pdf_index import index_pdfs
from .remittance.load import load_remittance
from .report import build_report, discard_report, print_summary, write_control_tables, write_fingerprints, write_report, write_run
from .roots import Layout
from .statements.load import load_statements
from .statements.reconcile import reconcile
from .tables import load_bank_transactions, load_items, load_masters

logger = logging.getLogger("bankrec")

STEPS = (
    ("Maestros", load_masters),
    ("Movimientos normalizados", load_bank_transactions),
    ("Partidas", load_items),
    ("Remesas, recibos, tarjetas y cheques", load_aux_files),
    ("Avisos de remesa", load_remittance),
    ("Índice de PDFs", index_pdfs),
    ("Extractos crudos", load_statements),
    ("Reconciliación con los extractos", reconcile),
    ("Alertas", run_alerts),
    ("Ficheros sin leer", check_every_file_was_read),
)


@dataclass(frozen=True)
class IngestResult:
    ok: bool
    published: bool
    report: dict[str, object]


def run_ingest(layout: Layout, allow_errors: bool = False) -> IngestResult:
    """Publish only a clean warehouse. With errors the previous one stays and the new one is kept aside to diagnose them."""
    started_at = datetime.now(timezone.utc)
    clock = time.monotonic()
    discard_report(layout.report_dir)
    con = open_build(layout.warehouse_path)
    ctx = IngestContext(layout, con, IssueLog(), Manifest())
    try:
        for label, step in STEPS:
            step_clock = time.monotonic()
            step(ctx)
            logger.info("%-40s %6.1f s", label, time.monotonic() - step_clock)
        write_control_tables(ctx)
        fingerprints = write_fingerprints(ctx)
        write_run(ctx, started_at)
        report = build_report(ctx, fingerprints, time.monotonic() - clock)
    except Exception as error:
        con.close()
        building_path(layout.warehouse_path).unlink(missing_ok=True)
        logger.exception("Ingesta abortada: %s. No hay informe nuevo y el almacén anterior sigue en su sitio.", error)
        raise
    con.close()
    published = allow_errors or not ctx.issues.has_errors
    location = publish_build(layout.warehouse_path) if published else keep_failed_build(layout.warehouse_path)
    final_report = {**report, "published": published, "warehouse": str(location)}
    write_report(final_report, layout.report_dir)
    print_summary(final_report)
    if not published:
        logger.error("Almacén NO publicado por errores; el anterior sigue en su sitio. Diagnóstico en %s", location)
    elif ctx.issues.has_errors:
        logger.warning("Almacén publicado CON errores porque se pidió --allow-errors: %s", location)
    return IngestResult(not ctx.issues.has_errors, published, final_report)
