"""Compara la distribución de categorías, excepciones y reglas entre dos salidas (alarma de drift antes del FINAL).

    python -m bankrec.stats final live
"""
from __future__ import annotations

import sys
from collections import Counter

from .data import rows
from .run import OUT


def dist(tag):
    c_cat, c_exc, c_rule, n_txn = Counter(), Counter(), Counter(), set()
    for r in rows(OUT / f"explain_{tag}.csv.gz"):
        n_txn.add(r["bank_txn_id"])
        if r["item_id"]:
            c_cat["(con partida)"] += 1
        else:
            c_cat[r["category"]] += 1
        if r["exception"]:
            c_exc[r["exception"]] += 1
        c_rule[r["rule"].split(":")[0]] += 1
    return len(n_txn), c_cat, c_exc, c_rule


def main(a="final", b="live"):
    na, ca, ea, ra = dist(a)
    nb, cb, eb, rb = dist(b)
    for title, x, y in (("CATEGORÍA", ca, cb), ("EXCEPCIÓN", ea, eb), ("REGLA", ra, rb)):
        print(f"\n{title:22s} {a:>9s} {b:>9s}   (% sobre movimientos)")
        for k in sorted(set(x) | set(y), key=lambda k: -(x[k] + y[k])):
            pa = 100 * x[k] / na if na else 0.0
            pb = 100 * y[k] / nb if nb else 0.0
            drift = abs(pa - pb) > max(2.0, 0.5 * max(pa, pb))
            if title == "EXCEPCIÓN" and ((pa == 0) != (pb == 0) or (min(pa, pb) > 0 and max(pa, pb) / min(pa, pb) >= 2.0)):
                drift = True
            flag = "  <-- revisar" if drift else ""
            print(f"  {k[:22]:22s} {pa:8.2f}% {pb:8.2f}%{flag}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
