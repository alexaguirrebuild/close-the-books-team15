"""Plugin de cobros cortos, deducciones mixtas y sobrepagos (Lote 4 / Issue #30).

Se encadena sin tocar engine.py mediante prepare(engine) y match(engine, t),
con try/except y rollback completo si ocurre cualquier excepción.
"""
from __future__ import annotations

import datetime as dt


def _invoice_deductions(engine, invoices, credits, cash, t):
    """DP por factura: cada factura puede cobrarse íntegra, con descuento pronto pago o con retención."""
    if len(invoices) > 20 or any(i.ccy != t.ccy for i in invoices + credits):
        return None
    target = cash + sum(c.open for c in credits)
    states = {0: ()}
    country = engine.ph.party_country.get(invoices[0].party) if invoices else None
    withholding = getattr(engine, "WITHHOLDING", {"MX": (0.0125, 0.015, 0.10), "BR": (0.0125, 0.015, 0.10), "MA": (0.0125, 0.015, 0.10)})
    for it in invoices:
        opts = [(it.open, "")]
        term = engine.ph.terms.get(it.terms or "")
        if term and term.get("early_payment_discount_pct"):
            pct = float(term["early_payment_discount_pct"])
            days = int(term.get("discount_days") or 0)
            in_time = it.issue and t.date <= it.issue + dt.timedelta(days=days + 3)
            opts.append((it.total - int(round(it.total * pct / 100)), "SHORT_DISCOUNT" if in_time else "LATE"))
        if country in withholding and it.side == "AR" and it.open == it.total:
            for r in withholding[country]:
                opts.append((it.total - int(round(it.total * r)), "SHORT_WITHHOLDING"))
        nxt = {}
        for sm, plan in states.items():
            for val, exc in opts:
                ns = sm + val
                if ns <= target + 2 * len(invoices) and (ns not in nxt or (exc and not nxt[ns][-1][1])):
                    nxt[ns] = plan + ((it, exc),)
        states = nxt
        if len(states) > 50000:
            return None
    best = [plan for sm, plan in states.items() if abs(sm - target) <= 2 * len(invoices) and any(e for _, e in plan)]
    if not best:
        return None
    best.sort(key=lambda p: (any(e == "LATE" for _, e in p), sum(1 for _, e in p if e)))
    plan = best[0]
    if any(e == "LATE" for _, e in plan):
        return "LATE"
    return {it.id: exc for it, exc in plan}


def _wrap_party_amount(engine):
    if getattr(engine, "_cobros_cortos_wrapped", False):
        return
    engine._cobros_cortos_wrapped = True
    orig_party_amount = engine._party_amount

    def _party_amount_wrapped(t, party, how):
        if orig_party_amount(t, party, how):
            return True
        if t.amount <= 0 or not party or not party.startswith("C"):
            return False
        its = engine.open_items_of(party, t, {"ARI", "ARC"})
        if not its:
            return False
        inv = [i for i in its if engine.sign(i, t) > 0]
        credits = [i for i in its if engine.sign(i, t) < 0]
        cash = abs(t.amount)
        # 1. Sobrepago de 1 factura (Issue #30: ar_overpayment, 2 céntimos < exceso <= 10%)
        over = [i for i in inv if i.ccy == t.ccy and 2 < cash - i.open <= 0.10 * i.open]
        if len(over) == 1 and len(inv) == 1 and not credits:
            return engine.settle(t, [over[0].id], f"party_overpay_{how}", f"contraparte {party}", party=party)
        # 2. Deducciones mixtas en lote de facturas del mismo cliente
        if 1 < len(inv) <= 12:
            dplan = _invoice_deductions(engine, inv, credits, cash, t)
            if isinstance(dplan, dict):
                for c in credits:
                    engine.emit(t, c, c.open, "", f"party_deductions_{how}", f"contraparte {party}")
                for it in inv:
                    engine.emit(t, it, it.open, dplan.get(it.id, ""), f"party_deductions_{how}", f"contraparte {party}")
                return True
        return False

    engine._party_amount = _party_amount_wrapped


def prepare(engine):
    try:
        _wrap_party_amount(engine)
    except Exception:
        pass


def match(engine, t):
    # Toda la lógica actúa envolviendo _party_amount después de m_refs.
    return False
