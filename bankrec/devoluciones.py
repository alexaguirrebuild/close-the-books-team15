"""Plugin: devoluciones que reabren partidas (POLICIES §3 RETURNED). Antes salían siempre mal, en los tres casos:
  - transferencia nuestra rechazada: cobro que citaba su end_to_end; quedaba como UNIDENTIFIED;
  - cheque devuelto: cargo con el número del cheque; quedaba como BANK_FEES;
  - contracargo de tarjeta: cargo con la tarjeta; quedaba como BANK_FEES o PARTIAL.
"""
from __future__ import annotations

import re

from .data import cents

TRANSFER_RETURN = re.compile(r"(?:DEVOLUCION TRANSFERENCIA|RETURN)\s+[A-Z]{2}\d{2}\b")
RUN_E2E = re.compile(r"\b[A-Z]{2}\d{10,}-\d{4}\b")
CHEQUE_RETURN = re.compile(r"(?:RETURNED ITEM CHK|CHEQUE IMPAYE)\s+(\d+)")
CARD_RETURN = re.compile(r"(?:CHARGEBACK|CONTRACARGO TARJ)\s+(\*{4}\d{4})")


def _cents(value: str) -> int:
    return abs(cents(value))


def _returned_transfer_rows(engine, t) -> list[dict]:
    """El e2e se repite entre divisas de la misma remesa, así que el importe decide cuál de ellas es."""
    cited = RUN_E2E.search(t.up.replace(" | ", ""))
    e2es = [cited.group(0)] if cited else ([t.e2e] if t.e2e else [])
    if not any(e in engine.e2e_rows for e in e2es):
        e2es = engine.transfers_by_amount.get((t.account, t.amount), [])
    used_ret = getattr(engine, "returned_e2es", set())
    matches = []
    for e2e in e2es:
        if e2e in used_ret:
            continue
        same_transfer = [r for r in engine.e2e_rows.get(e2e, ()) if abs(_cents(r["transfer_amount"]) - t.amount) <= 2]
        if same_transfer:
            matches.append((same_transfer[0].get("execution_date") or "", e2e, same_transfer))
    if not matches:
        return []
    t_iso = t.date.isoformat() if getattr(t, "date", None) else "9999-12-31"
    past = [m for m in matches if not m[0] or m[0][:10] <= t_iso]
    chosen = max(past, key=lambda m: m[0]) if past else matches[0]
    engine.__dict__.setdefault("returned_e2es", set()).add(chosen[1])
    return chosen[2]


def _match_transfer(engine, t) -> bool:
    if not TRANSFER_RETURN.search(t.up):
        return False
    emitted = False
    for row in _returned_transfer_rows(engine, t):
        item = engine.run_doc_item(row, t)
        if item is None:
            continue
        amount = _cents(row["document_amount"])
        if item.ccy != row["currency"]:
            amount = engine.ph.fx.conv(amount, row["currency"], item.ccy, t.date)
        engine.emit(t, item, amount, "RETURNED", "transfer_returned", f"devuelta {row['end_to_end_id']}")
        emitted = True
    return emitted


def _match_cheque(engine, t) -> bool:
    found = CHEQUE_RETURN.search(t.up)
    if not found:
        return False
    chk = found.group(1)
    item = engine.cheque_items.get(chk)
    amt = -t.amount
    if item is None and hasattr(engine, "ph"):
        for r in getattr(engine.ph, "cheques", ()):
            if r.get("cheque_number") == chk:
                party = engine.resolve_name(r.get("drawer") or "")
                if party:
                    closed = [
                         engine.items[i]
                        for i in engine.ph.by_party.get(party, ())
                        if engine.items[i].kind == "ARI"
                        and engine.items[i].open <= 0
                        and abs(engine.items[i].total - amt) <= 2
                    ]
                    if len(closed) == 1:
                        item = closed[0]
                        break
    if item is None:
        return False
    engine.emit(t, item, amt, "RETURNED", "cheque_returned", f"cheque {chk}")
    return True


def _match_card(engine, t) -> bool:
    found = CARD_RETURN.search(t.up)
    item = engine.card_items.get((found.group(1), -t.amount)) if found else None
    if item is None:
        return False
    engine.emit(t, item, -t.amount, "RETURNED", "card_chargeback", f"tarjeta {found.group(1)}")
    return True


def match(engine, t) -> bool:
    if t.amount > 0:
        return _match_transfer(engine, t)
    return _match_cheque(engine, t) or _match_card(engine, t)
