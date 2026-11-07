"""CLI del equipo.

    python -m bankrec.run --phase sample            # genera out/sub_sample.csv.gz y lo puntúa contra el golden
    python -m bankrec.run --phase live              # genera out/sub_live.csv.gz (NO lo envía)
    python -m bankrec.run --phase holdout           # holdout rápido: TRAIN ene-jun 2025 con saldos abiertos a 1-ene (~LIVE)
    python -m bankrec.run --phase train --since 2025-01-01   # holdout lento: recorre los 4 años

Cada ejecución deja también out/explain_<fase>.csv.gz: el porqué de cada fila (regla y evidencia). Eso no se envía.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import io
import json
import sys
import time
from collections import Counter, OrderedDict
from pathlib import Path

from . import preflight
from .data import DATA, Phase
from .engine import DEFAULT_PLUGINS, Engine

ROOT = Path(__file__).resolve().parents[1]
import os
OUT = Path(os.environ.get("BANKREC_OUT", ROOT / "out"))
EXC = ["PARTIAL", "OVERPAYMENT", "SHORT_DISCOUNT", "SHORT_BANK_CHARGES", "SHORT_WITHHOLDING", "SHORT_DISPUTE",
       "DUPLICATE_PAYMENT", "UNIDENTIFIED", "RETURNED", "FX_DIFFERENCE"]


def assemble(ph, rows, since=None):
    """Una fila por (movimiento, partida); como mucho una fila sin partida; ningún movimiento sin respuesta."""
    by_txn = OrderedDict((t.id, []) for t in ph.txns if not since or t.date >= since)
    for r in rows:
        if r.txn in by_txn:
            by_txn[r.txn].append(r)
    out = []
    for tid, rs in by_txn.items():
        items = [r for r in rs if r.item]
        merged = OrderedDict()
        for r in items:
            if r.item in merged:
                merged[r.item].amount += r.amount
            else:
                merged[r.item] = r
        # FORMAT: amount es lo liquidado. Una fila a cero no liquida nada y en el golden no existe.
        settled = [r for r in merged.values() if r.amount > 0]
        no_item = [r for r in rs if not r.item]
        if settled:
            out.extend(settled)
        elif no_item:
            out.append(no_item[0])
        else:
            from .engine import Row
            out.append(Row(tid, "", None, "UNIDENTIFIED", "UNIDENTIFIED", "missing"))
    return out


def write(rows, path, explain_path=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["bank_txn_id", "item_id", "amount", "category", "exception"])
        for r in rows:
            amt = "" if not r.item else f"{max(r.amount, 0) / 100:.2f}"
            w.writerow([r.txn, r.item, amt, "" if r.item else r.category, r.exception])
    if explain_path:
        with gzip.open(explain_path, "wt", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["bank_txn_id", "item_id", "amount", "category", "exception", "rule", "evidence"])
            for r in rows:
                amt = "" if not r.item else f"{max(r.amount, 0) / 100:.2f}"
                w.writerow([r.txn, r.item, amt, r.category, r.exception, r.rule, r.evidence])


def report_plugin_errors(eng):
    errors = getattr(eng, "plugin_errors", [])
    if errors:
        ejemplos = "; ".join(f"{name} en {where}: {detail}" for name, where, detail in errors[:5])
        print(f"  AVISO: {len(errors)} fallos de plugin; se han deshecho y la fase ha seguido: {ejemplos}", flush=True)


def report_broken_advices(eng):
    broken = getattr(eng, "ra_errors", [])
    if broken:
        ejemplos = ", ".join(file for file, _ in broken[:5])
        print(f"  AVISO: {len(broken)} avisos de remesa no se han podido leer: {ejemplos}", flush=True)
    empty = getattr(eng, "ra_empty", [])
    if empty:
        print(f"  AVISO: {len(empty)} avisos sin líneas: {', '.join(empty[:5])}", flush=True)


def score(ph, sub_path, golden_path, since=None):
    sys.path.insert(0, str(ROOT / "vendor"))
    import bankrec_score as S  # evaluador oficial, sin tocar

    txns = io.BytesIO()
    with gzip.open(txns, "wt") as g:
        g.write("bank_txn_id\n")
        for t in ph.txns:
            if not since or t.date >= since:
                g.write(t.id + "\n")
    tmp = OUT / "_txns.csv.gz"
    tmp.write_bytes(txns.getvalue())
    gold = golden_path
    if since:  # golden recortado al holdout
        gold = OUT / "_golden_holdout.csv.gz"
        keep = {t.id for t in ph.txns if t.date >= since}
        with gzip.open(golden_path, "rt", encoding="utf-8") as fi, gzip.open(gold, "wt", encoding="utf-8") as fo:
            head = fi.readline()
            fo.write(head)
            for line in fi:
                if line.split(",", 1)[0] in keep:
                    fo.write(line)
    g = S.load_golden(str(gold), str(tmp), EXC, ph.categories)
    res = S.score_submission(g, Path(sub_path).read_bytes(), str(sub_path))
    return res


def main(argv=None):
    preflight.ensure_deterministic(sys.argv[1:] if argv is None else argv)
    preflight.check_dependencies()
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", default="sample", choices=["sample", "train", "holdout", "live", "final"])
    ap.add_argument("--since", help="holdout: solo movimientos desde esta fecha (YYYY-MM-DD)")
    ap.add_argument("--no-score", action="store_true")
    a = ap.parse_args(argv)
    preflight.check_phase_inputs(a.phase, DATA)
    t0 = time.time()
    if a.phase == "holdout":
        a.since = a.since or "2025-01-01"
        ph = Phase("train", holdout=dt.date.fromisoformat(a.since))
    else:
        ph = Phase(a.phase)
    print("  plugins:", os.environ.get("BANKREC_PLUGINS", DEFAULT_PLUGINS), flush=True)  # Un export olvidado los apagaría sin avisar.
    print(f"[{a.phase}] {len(ph.txns)} movimientos, {len(ph.items)} partidas · carga {time.time() - t0:.0f}s", flush=True)
    print(f"  maestros: {ph.mdir} · remesas de pago {len(ph.runs)} · recibos {len(ph.dd)}", flush=True)
    eng = Engine(ph)
    rows = eng.run()
    report_broken_advices(eng)
    report_plugin_errors(eng)
    since = dt.date.fromisoformat(a.since) if a.since else None
    rows = assemble(ph, rows, since)
    tag = "holdout" if a.phase == "holdout" else a.phase + (f"_since{a.since}" if a.since else "")
    sub = OUT / f"sub_{tag}.csv.gz"
    write(rows, sub, OUT / f"explain_{tag}.csv.gz")
    rules = Counter(r.rule.split("_")[0] if r.rule.startswith("kw") else r.rule for r in rows)
    print(f"  {len(rows)} filas -> {sub} · {time.time() - t0:.0f}s")
    print("  reglas:", ", ".join(f"{k}={v}" for k, v in rules.most_common(14)))
    golden = DATA / ph.name / "golden_allocations.csv.gz"
    if not a.no_score and golden.exists():
        res = score(ph, sub, golden, since)
        print(json.dumps({"ok": res["ok"], "score": res["score"], "metrics": res["metrics"], "errors": res["errors"][:5]},
                         indent=1))
        segs = sorted(res["segments"].items(), key=lambda x: -x[1]["n"] * (1 - x[1]["accuracy"]))
        print("  segmentos con más fallos (n · acierto · fallos):")
        for k, v in segs[:25]:
            print(f"    {k:30s} n={v['n']:6d} acc={v['accuracy']:.3f} fallos={int(v['n'] * (1 - v['accuracy']))}")
        (OUT / f"score_{tag}.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
