"""Análisis de fallos de un segmento con un modelo. Herramienta de desarrollo: el motor no la carga y no cambia ninguna respuesta.

    python -m bankrec.ai_analysis --phase sample --segment ar_1toN_credit_note -n 12
Reúne los fallos con la misma comparación que `bankrec.diff`, calcula la estadística en local (qué regla los resolvió, qué
tamaño de cobro) y, con BANKREC_LLM=1 y ANTHROPIC_API_KEY, pide al modelo hipótesis y reglas candidatas. El modelo solo
propone: toda regla pasa por plan, test y holdout (CLAUDE.md). Los datos son los del reto (sintéticos), sin golden de LIVE.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict

from . import llm
from .data import DATA, rows
from .diff import load, same
from .run import OUT

SIZES = (("1-10", 10), ("11-30", 30), ("31-100", 100), (">100", None))
SYSTEM = ("You are a reviewer of a bank-reconciliation rules engine. You propose hypotheses and candidate rules from failure "
          "cases. Be concrete and short. Never invent data that is not in the cases.")


def _size(n: int) -> str:
    return next(label for label, top in SIZES if top is None or n <= top)


def failures(phase: str, segment: str, limit: int) -> list:
    """Hasta `limit` movimientos del segmento donde nuestra respuesta no coincide con la buena."""
    src = "train" if phase == "holdout" else phase
    ours = load(OUT / f"explain_{phase}.csv.gz")
    gold = defaultdict(list)
    for r in rows(DATA / src / "golden_allocations.csv.gz"):
        if r["bank_txn_id"] in ours and r["segment"] == segment:
            gold[r["bank_txn_id"]].append(r)
    bank = {r["bank_txn_id"]: r for r in rows(DATA / src / "bank_transactions.csv.gz") if r["bank_txn_id"] in gold}
    out = []
    for tid, gr in gold.items():
        if same(gr, ours[tid]):
            continue
        b, sr = bank[tid], ours[tid]
        fmt = lambda rs: " ; ".join(f"{r['item_id'] or r['category']} {r['amount']} {r['exception']}".strip() for r in rs[:10])
        out.append({"txn": tid, "text": f"{b['text'][:140]} | {b['remittance_info'][:60]}", "amount": f"{b['amount']} {b['currency']}",
                    "rule": sr[0]["rule"], "good_n": len(gr), "ours_n": len([r for r in sr if r["item_id"]]),
                    "good": fmt(gr), "ours": fmt(sr)})
        if len(out) >= limit:
            break
    return out


def summarise(cases: list) -> dict:
    return {"by_rule": dict(Counter(c["rule"] for c in cases)),
            "by_size": dict(Counter(_size(c["good_n"]) for c in cases)),
            "missing_items": sum(max(c["good_n"] - c["ours_n"], 0) for c in cases)}


def build_prompt(segment: str, cases: list, stats: dict, max_cases: int = 12) -> str:
    shown = "\n\n".join(f"TEXT: {c['text']}\nAMOUNT: {c['amount']}\nOUR RULE: {c['rule']}\nOURS ({c['ours_n']} items): {c['ours']}\n"
                        f"CORRECT ({c['good_n']} items): {c['good']}" for c in cases[:max_cases])
    return (f"Segment: {segment}. Failures analysed: {len(cases)}.\nLocal statistics: {json.dumps(stats)}\n\n{shown}\n\n"
            "Constraints for any rule you propose: no ids, names, banks or amounts hard-coded; assign only with exact evidence "
            "(a reference or an exact amount) and a single candidate, otherwise do not answer; cite the POLICIES section it implements.\n"
            "Answer with: 1) the 2-3 most likely common causes, each with the case numbers that show it; "
            "2) one candidate rule per cause, with what evidence it requires; 3) what would make it unsafe.")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", default="sample")
    ap.add_argument("--segment", required=True)
    ap.add_argument("-n", type=int, default=12)
    a = ap.parse_args(argv)
    cases = failures(a.phase, a.segment, a.n)
    stats = summarise(cases)
    print(f"{a.segment}: {len(cases)} fallos analizados\n{json.dumps(stats, indent=2)}")
    if not cases:
        return
    if not llm.enabled():
        print("\nSin análisis del modelo: activa BANKREC_LLM=1 y pon ANTHROPIC_API_KEY en .env.")
        return
    answer = llm.ask(build_prompt(a.segment, cases, stats), SYSTEM, max_tokens=1500)
    print("\n" + (answer or f"El modelo no respondió: {llm.last_error or 'tope de llamadas'}."))


if __name__ == "__main__":
    main()
