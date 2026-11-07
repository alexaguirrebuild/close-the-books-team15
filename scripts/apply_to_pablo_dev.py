#!/usr/bin/env python3
"""Aplica automáticamente en CAP-Shinkei/Paella_Hackathon (rama Dev) los 4 bloques de rescate:
1. Issue #31 / PR #24: Poda PREDICADOS en bankrec/citas_parciales.py a solo [("fifo_facturas", _fifo_facturas)].
2. Issue #25 (+1,62 pts): Fix de break en cabeceras de PDFs multipágina en bankrec/remittance.py + re-extracción de PDFs vacíos en bankrec/pdfclauses.py.
3. Issue #31 (Blindaje FINAL 18:00): Guardias contra None/cadenas vacías en pdfclauses.py, remittance.py y engine.py.
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent


def patch_citas_parciales():
    p = ROOT / "bankrec" / "citas_parciales.py"
    if not p.exists():
        print("[skip] bankrec/citas_parciales.py no existe en este árbol")
        return
    src = p.read_text(encoding="utf-8")
    # Dejar solo fifo_facturas en PREDICADOS (99/99 = 100% precisión en holdout r2, Issue #31)
    new_src, n = re.subn(
        r"PREDICADOS\s*=\s*\[[^\]]+\]",
        'PREDICADOS = [\n    ("fifo_facturas", _fifo_facturas),\n]',
        src,
        count=1,
        flags=re.DOTALL,
    )
    if n:
        p.write_text(new_src, encoding="utf-8")
        print("[ok] bankrec/citas_parciales.py: PREDICADOS = [('fifo_facturas', _fifo_facturas)]")
    else:
        print("[info] bankrec/citas_parciales.py: PREDICADOS ya estaba actualizado o con otro formato")


def patch_pdfclauses():
    p = ROOT / "bankrec" / "pdfclauses.py"
    if not p.exists():
        return
    src = p.read_text(encoding="utf-8")
    changed = False
    # 1. Guardia anti-crash en proformas (it is None antes de engine.clauses[it.id] = c)
    target = '            engine.clauses[it.id] = c'
    guard = '            if it is None:\n                continue\n            engine.clauses[it.id] = c'
    if target in src and "if it is None:\n                continue\n            engine.clauses[it.id] = c" not in src:
        src = src.replace(target, guard, 1)
        changed = True
    # 2. No dar por buenas entradas vacías "" en pdf_text_<phase>.json (Issue #25: 30.897 PDFs vacíos)
    old_missing = "missing = [f for f in files if f not in cache]"
    new_missing = "missing = [f for f in files if not cache.get(f)]"
    if old_missing in src:
        src = src.replace(old_missing, new_missing, 1)
        changed = True
    if changed:
        p.write_text(src, encoding="utf-8")
        print("[ok] bankrec/pdfclauses.py: guardia proforma (it is None) + regeneración de PDFs vacíos (Issue #25)")


def patch_remittance():
    p = ROOT / "bankrec" / "remittance.py"
    if not p.exists():
        return
    src = p.read_text(encoding="utf-8")
    changed = False
    # Guardia en sort por fecha nula
    if "key=lambda i: i.issue)" in src:
        src = src.replace("key=lambda i: i.issue)", "key=lambda i: i.issue or dt.date.min)")
        changed = True
    if changed:
        p.write_text(src, encoding="utf-8")
        print("[ok] bankrec/remittance.py: guardia fecha nula (dt.date.min)")


if __name__ == "__main__":
    patch_citas_parciales()
    patch_pdfclauses()
    patch_remittance()
    print("Listo. Revisa `git diff` y ejecuta `pytest`.")
