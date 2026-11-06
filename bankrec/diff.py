"""QA por segmento: enseña movimientos donde fallamos, con nuestra respuesta, la buena y el porqué de nuestra regla.

    python -m bankrec.diff --phase sample --segment ar_dd_batch -n 3
    python -m bankrec.diff --phase holdout --segment ap_factored -n 5     # sobre el holdout rápido
Es la herramienta del rol de QA: mira 5 fallos, encuentra el patrón, propón la regla.
"""
from __future__ import annotations

import argparse
import csv
import gzip
from collections import defaultdict

from .data import DATA, rows
from .run import OUT


def load(path, key="bank_txn_id"):
    d = defaultdict(list)
    for r in rows(path):
        d[r[key]].append(r)
    return d


def tol(g):
    return max(0.01, 0.005 * abs(g)) + 1e-9


def same(gr, sr):
    if gr[0]["item_id"] == "" or sr[0]["item_id"] == "":
        return (gr[0]["item_id"] == sr[0]["item_id"] == "" and gr[0]["category"] == sr[0]["category"]
                and gr[0]["exception"] == sr[0]["exception"])
    g = {r["item_id"]: (float(r["amount"]), r["exception"]) for r in gr}
    s = {r["item_id"]: (float(r["amount"] or 0), r["exception"]) for r in sr}
    return g.keys() == s.keys() and all(abs(g[k][0] - s[k][0]) <= tol(g[k][0]) and g[k][1] == s[k][1] for k in g)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", default="sample")
    ap.add_argument("--segment", required=True)
    ap.add_argument("-n", type=int, default=3)
    a = ap.parse_args(argv)
    src = "train" if a.phase == "holdout" else a.phase
    ours = load(OUT / f"explain_{a.phase}.csv.gz")
    gold = defaultdict(list)
    for r in rows(DATA / src / "golden_allocations.csv.gz"):
        if r["bank_txn_id"] in ours and r["segment"] == a.segment:
            gold[r["bank_txn_id"]].append(r)
    bt = {r["bank_txn_id"]: r for r in rows(DATA / src / "bank_transactions.csv.gz") if r["bank_txn_id"] in gold}
    shown = 0
    for tid, gr in gold.items():
        if gr[0]["segment"] != a.segment or tid not in ours or same(gr, ours[tid]):
            continue
        b = bt[tid]
        print(f"== {tid} {b['account_id']} {b['booking_date']} {b['amount']} {b['currency']} [{b['statement_format']}]")
        print(f"   texto: {b['text'][:160]} | r1={b['ref1']} r2={b['ref2']} rmt={b['remittance_info'][:60]}")
        if b["counterparty_name"]:
            print(f"   contraparte: {b['counterparty_name']} {b['counterparty_account']} e2e={b['end_to_end_id']}")
        print("   BUENA :", "; ".join(f"{r['item_id'] or r['category']} {r['amount']} {r['exception']}" for r in gr[:8]),
              f"(+{len(gr) - 8})" if len(gr) > 8 else "")
        sr = ours[tid]
        print("   NUESTRA:", "; ".join(f"{r['item_id'] or r['category']} {r['amount']} {r['exception']}" for r in sr[:8]),
              f"(+{len(sr) - 8})" if len(sr) > 8 else "", f"· regla={sr[0]['rule']} {sr[0]['evidence'][:50]}")
        shown += 1
        if shown >= a.n:
            break
    if not shown:
        print("sin fallos en ese segmento")


if __name__ == "__main__":
    main()
