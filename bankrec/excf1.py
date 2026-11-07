"""F1 por clase de excepción, igual que el evaluador oficial, con la confusión de cada clase.

    python -m bankrec.excf1 --phase holdout            # usa out/explain_holdout.csv.gz
    python -m bankrec.excf1 --phase sample

El macro-F1 de excepciones pesa el 20 % de la nota y cada una de las 10 clases cuenta igual:
subir una clase rara de 0,2 a 0,6 vale lo mismo que subir FX_DIFFERENCE.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict

from .data import DATA, rows
from .run import EXC, OUT


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", default="holdout")
    ap.add_argument("--file", help="explain_*.csv.gz alternativo")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    src = "train" if a.phase == "holdout" else a.phase
    ours = defaultdict(dict)  # txn -> {item or '': exc}
    for r in rows(a.file or OUT / f"explain_{a.phase}.csv.gz"):
        ours[r["bank_txn_id"]][r["item_id"]] = r["exception"]
    gold = defaultdict(dict)
    seg = {}
    for r in rows(DATA / src / "golden_allocations.csv.gz"):
        if r["bank_txn_id"] in ours:
            gold[r["bank_txn_id"]][r["item_id"]] = r["exception"]
            seg[r["bank_txn_id"]] = r["segment"]
    tp, fp, gc = Counter(), Counter(), Counter()
    miss = defaultdict(Counter)   # clase buena -> lo que dijimos
    wrong = defaultdict(Counter)  # clase que dijimos de más -> lo que era
    miss_seg = defaultdict(Counter)
    for t, g in gold.items():
        o = ours.get(t, {})
        for it, ge in g.items():
            if ge:
                gc[ge] += 1
                oe = o.get(it, "<sin fila>") if it else (o.get("", "<con partidas>") if "" in o else "<con partidas>")
                if oe != ge:
                    miss[ge][oe or "<vacía>"] += 1
                    miss_seg[ge][seg[t]] += 1
        for it, oe in o.items():
            if not oe:
                continue
            ge = g.get(it) if it else (g.get("") if "" in g and len(g) == 1 else None)
            if oe == ge:
                tp[oe] += 1
            else:
                fp[oe] += 1
                wrong[oe][("<sin partida>" if ge is None else (ge or "<vacía>"))] += 1
    f1s = []
    print(f"{'clase':20s} {'oro':>6s} {'tp':>6s} {'fp':>6s} {'prec':>5s} {'rec':>5s} {'f1':>5s}")
    for c in EXC:
        if not gc[c]:
            continue
        p = tp[c] / (tp[c] + fp[c]) if tp[c] + fp[c] else 0
        rc = tp[c] / gc[c]
        f = 2 * p * rc / (p + rc) if p + rc else 0
        f1s.append(f)
        print(f"{c:20s} {gc[c]:6d} {tp[c]:6d} {fp[c]:6d} {p:5.2f} {rc:5.2f} {f:5.2f}")
        print(f"     se nos escapa como: {dict(miss[c].most_common(4))}  · segmentos: {dict(miss_seg[c].most_common(3))}")
        if fp[c]:
            print(f"     la ponemos de más cuando era: {dict(wrong[c].most_common(4))}")
    print(f"macro-F1 aprox: {sum(f1s) / len(f1s):.3f}" if f1s else "macro-F1 aprox: 0.000")


if __name__ == "__main__":
    main()
