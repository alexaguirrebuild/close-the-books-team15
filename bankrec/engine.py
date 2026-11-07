"""Motor de conciliación. Cada movimiento se procesa en orden de fecha; cada regla propone filas con su porqué.

Una fila = (bank_txn_id, item_id, amount_cents, category, exception, rule, evidence).
POLICIES.md manda: los nombres de reglas citan su apartado.
"""
from __future__ import annotations

import importlib
import datetime as dt
import re
from collections import defaultdict
from itertools import combinations

from . import duplicados
from .data import SEPA, Phase, Txn, ascii_upper, name_key, norm_text, num_variants

PAYMENT_FACTORY = {"ES00": {"ES01", "ES02", "PT01"}}
WITHHOLDING = {"MX": (0.0125, 0.015, 0.10), "BR": (0.0125, 0.015, 0.10), "MA": (0.0125, 0.015, 0.10)}


class Row:
    __slots__ = ("txn", "item", "amount", "category", "exception", "rule", "evidence")

    def __init__(self, txn, item="", amount=None, category="", exception="", rule="", evidence=""):
        self.txn, self.item, self.amount = txn, item, amount
        self.category, self.exception, self.rule, self.evidence = category, exception, rule, evidence


# ---------------------------------------------------------------- categorías sin partida (POLICIES §4)
NONITEM_RULES = [
    (r"ZBA SWEEP|CASH ?POOL|CASH CONCENTRATION|BARRIDO|NIVELACION|TRASPASO ENTRE CUENTAS ES00|TRASPASO PROPIO|SWEEP", "CASH_POOL"),
    (r"NOMINA|PAYROLL|SALARIES|SALAIRES|GEHALT|LOHN", "PAYROLL"),
    (r"SEGURIDAD SOCIAL|SEG\. SOCIAL|SOCIAL CHARGES|URSSAF|TGSS|SOZIALVERS|COTISATIONS", "SOCIAL_SECURITY"),
    (r"DEVOLUCION IMPUESTO|TAX REFUND|DEVOLUCION IVA|REMBOURSEMENT TVA|STEUERERSTATTUNG", "TAX_REFUND"),
    (r"IMPUESTOS? MOD|TAX PAYMENT|VAT RETURN|AEAT|HACIENDA|FINANZAMT|DGFIP|TVA |IMPUESTO", "TAX_PAYMENT"),
    (r"ARRENDAMIENTO|LEASING|RENTING|CREDIT-BAIL", "LEASING"),
    (r"DISPOSICION PRESTAMO|LOAN DRAWDOWN|DEBLOCAGE|DISPOSICION POLIZA", "LOAN_DRAWDOWN"),
    (r"AMORTIZACION DE PRESTAMO|PRES\.[A-Z]{2}\d|LOAN REPAYMENT|ECHEANCE PRET|TILGUNG|CUOTA PRESTAMO", "LOAN_REPAYMENT"),
    (r"FINANCIACION IC|PRESTAMO INTRAGRUPO|PRESTAMO IC|INTERCOMPANY|INTRAGROUP", "INTERCOMPANY"),
    (r"TRASPASO (DE|A) CTA|OWN ACCOUNT|VIREMENT INTERNE|UMBUCHUNG", "INTERNAL_TRANSFER"),
    (r"COMPRA DIVISA|VENTA DIVISA|FX DEAL|SPOT |FOREX|CAMBIO DIVISA|DEVISENHANDEL", "FX_DEAL"),
    (r"ADEUDO MENSUAL DE TARJETA|CARD STATEMENT|RELEVE CARTE|KREDITKARTENABRECHNUNG", "CARD_EXPENSES"),
    (r"PRIMA SEGURO|INSURANCE|ASSURANCE|VERSICHERUNG|POLIZA SEGURO", "INSURANCE"),
    (r"PROFORMA|ANTICIPO PROFORMA|PF\d{2}-", "CUSTOMER_ADVANCE"),
    (r"DEVOLUCION PAGO DUPLICADO|REFUND DUPLICATE|REMBOURSEMENT DOUBLE", "CUSTOMER_REFUND"),
    (r"REEMBOLSO|ABONO PAGO DOBLE|ERSTATTUNG DOPPEL|REMBOURSEMENT", "SUPPLIER_REFUND"),
    (r"COMISION|COMISIONES|GASTOS|FEE|CHARGES|FRAIS|GEBUHR|ENTGELT|MANTENIMIENTO CUENTA|CUOTA TPV", "BANK_FEES"),
]
NONITEM_RE = [(re.compile(p), c) for p, c in NONITEM_RULES]


def classify_nonitem(t: Txn):
    up = t.up
    if "LIQUIDACION INTERESES" in up or "INTERESES" in up or "INTEREST" in up or "ZINSEN" in up or "INTERETS" in up:
        return ("INTEREST_INCOME" if t.amount > 0 else "INTEREST_EXPENSE"), "kw:interest"
    for rx, cat in NONITEM_RE:
        if rx.search(up):
            if cat in ("PAYROLL", "SOCIAL_SECURITY", "TAX_PAYMENT", "LEASING", "LOAN_REPAYMENT", "CARD_EXPENSES",
                       "INSURANCE", "CUSTOMER_REFUND", "BANK_FEES") and t.amount > 0:
                continue  # estas categorías son cargos
            if cat in ("CUSTOMER_ADVANCE", "SUPPLIER_REFUND", "TAX_REFUND", "LOAN_DRAWDOWN") and t.amount < 0:
                continue
            return cat, f"kw:{rx.pattern[:30]}"
    return None, ""


RUN_ID = re.compile(r"\b[A-Z]{2}\d{10,}\b")
# LATE: descuento pasado de plazo. Es fórmula exacta y POLICIES §3 lo trata como PARTIAL, que es lo que hace settle().
EXPLAINED_SHORTFALLS = ("SHORT_DISCOUNT", "SHORT_WITHHOLDING", "SHORT_BANK_CHARGES", "LATE")
OPTIONAL_PLUGINS = {"learned"}  # Solo estos pueden faltar: sin un plugin obligatorio se pierden puntos sin aviso.


def load_plugins(names: str) -> list:
    loaded = []
    for name in [n for n in names.split(",") if n]:
        module_name = f"bankrec.{name}"
        try:
            loaded.append(importlib.import_module(module_name))
        except ModuleNotFoundError as error:
            if name not in OPTIONAL_PLUGINS or error.name != module_name:
                raise
    return loaded


MAX_PARTIES_TRIED = 4
MAX_SUBSET_ITEMS = 14


class Engine:
    def __init__(self, ph: Phase):
        self.ph = ph
        self.items = ph.items
        self.rows: list[Row] = []
        self.settled_by = defaultdict(list)  # item -> [(txn_id, amount)]
        self.iban_learned = {}
        self.cheque_items = {}
        self.card_items = {}
        self._prep_batches()
        self.reversal = self._find_reversals()
        self.plugins = []
        import os
        for mod in load_plugins(os.environ.get("BANKREC_PLUGINS", "devoluciones,m2_avisos,remittance,pdfclauses,learned")):
            if hasattr(mod, "prepare"):
                mod.prepare(self)
            if hasattr(mod, "match"):
                self.plugins.append(mod)

    # ------------------------------------------------------------ utilidades
    def entity_ok(self, it, t):
        return it.entity == t.entity or it.entity in PAYMENT_FACTORY.get(t.entity, ())

    def side_ok(self, it, t):
        if t.amount > 0:   # cobro: facturas y abonos de cliente; abono de proveedor reembolsado
            return it.kind in ("ARI", "ARC", "APC")
        return it.kind in ("API", "APC", "ARC")

    def sign(self, it, t):
        """Contribución al cobro/pago: + factura del lado, - abono compensado."""
        if t.amount > 0:
            return {"ARI": 1, "ARC": -1, "APC": 1}[it.kind]
        return {"API": 1, "APC": -1, "ARC": 1}[it.kind]

    def to_bank(self, cents_item, it, t):
        if it.ccy == t.ccy:
            return cents_item
        if t.orig_ccy == it.ccy and t.orig_amount:
            # el banco da el importe original: la conversión real es la del banco
            return cents_item * abs(t.amount) / t.orig_amount
        return self.ph.fx.conv(cents_item, it.ccy, t.ccy, t.date)

    def emit(self, t, it, amount, exc, rule, ev=""):
        amount = int(round(amount))
        self.rows.append(Row(t.id, it.id, amount, "", exc, rule, ev))
        if exc == "RETURNED":
            it.open = min(it.open + amount, it.total)  # una devolución reabre, pero nunca por encima del total
        else:
            it.open -= amount
        self.settled_by[it.id].append((t.id, amount))
        if t.cp_iban and it.party:
            self.iban_learned[t.cp_iban] = it.party

    def emit_cat(self, t, cat, exc="", rule="", ev=""):
        self.rows.append(Row(t.id, "", None, cat, exc, rule, ev))

    # ------------------------------------------------------------ excepciones (POLICIES §2-§3)
    def discount_ok(self, it, t, cash_item):
        term = self.ph.terms.get(it.terms or "")
        if not term or not term.get("early_payment_discount_pct"):
            return None
        pct = float(term["early_payment_discount_pct"])
        days = int(term.get("discount_days") or 0)
        disc = int(round(it.total * pct / 100))
        if abs(cash_item - (it.total - disc)) <= 2:
            in_time = it.issue and t.date <= it.issue + dt.timedelta(days=days + 3)
            return "SHORT_DISCOUNT" if in_time else "LATE"
        return None

    def explain_short(self, it, t, short_item, cash_item):
        """Por qué falta dinero en una factura. Devuelve la excepción o None."""
        d = self.discount_ok(it, t, cash_item)
        if d:
            return d
        country = self.ph.party_country.get(it.party)
        if country in WITHHOLDING and it.side == "AR":
            for r in WITHHOLDING[country]:
                if abs(short_item - it.total * r) <= max(2, it.total * 0.0005):
                    return "SHORT_WITHHOLDING"
        cross = (t.orig_ccy is not None) or (country and country not in SEPA)
        if cross and it.side == "AR":
            short_eur = self.ph.fx.conv(short_item, it.ccy, "EUR", t.date)
            if 0 < short_eur <= 6000 and short_item <= 0.02 * it.total:
                return "SHORT_BANK_CHARGES"
        return None

    def settle(self, t, cands, rule, ev="", party=None):
        """Reparte el importe del movimiento entre partidas candidatas y pone las excepciones."""
        cash = abs(t.amount)
        cands = [self.items[c] for c in dict.fromkeys(cands)]
        if party:  # abonos no citados que cuadran la diferencia (POLICIES §2, abono compensado)
            gap = sum(self.sign(it, t) * self.to_bank(max(it.open, 0), it, t) for it in cands) - cash
            if gap > 2:
                kinds = {"ARC"} if t.amount > 0 else {"APC"}
                cns = [c for c in self.open_items_of(party, t, kinds) if c.id not in {x.id for x in cands}]
                vals = [int(round(self.to_bank(c.open, c, t))) for c in cns]
                pick = None
                for j, v in enumerate(vals):
                    if abs(v - gap) <= 2:
                        pick = [cns[j]]
                        break
                if not pick:
                    for a, b in combinations(range(len(vals)), 2):
                        if abs(vals[a] + vals[b] - gap) <= 2:
                            pick = [cns[a], cns[b]]
                            break
                if pick:
                    cands = cands + pick
        fx = any(it.ccy != t.ccy for it in cands)
        exp = sum(self.sign(it, t) * self.to_bank(max(it.open, 0), it, t) for it in cands)
        diff = exp - cash
        tol = max(2, cash * 0.00001)
        invoices = [it for it in cands if self.sign(it, t) > 0]
        credits = [it for it in cands if self.sign(it, t) < 0]

        def full(it, exc=""):
            if fx and self.sign(it, t) > 0 and it.ccy != t.ccy and exc in ("", "SHORT_DISCOUNT", "SHORT_WITHHOLDING",
                                                                            "SHORT_BANK_CHARGES", "OVERPAYMENT"):
                exc = "FX_DIFFERENCE"
            self.emit(t, it, it.open, exc, rule, ev)

        if abs(diff) <= tol or (fx and exp and abs(diff) <= 0.03 * exp):
            for it in cands:
                full(it)
            return True
        if diff < 0:  # sobra dinero
            # sobrepago real: como mucho un 10 % de más; más que eso es un cobro de varias facturas que no vemos todas
            if len(invoices) == 1 and not credits and cash <= 1.10 * exp:
                full(invoices[0], "OVERPAYMENT")
            else:
                for it in cands:
                    full(it)
            return True
        # falta dinero
        if len(invoices) == 1:
            it = invoices[0]
            for c in credits:
                full(c)
            cash_item = cash + sum(self.to_bank(c.open, c, t) for c in credits) if False else None
            # importe cobrado para la factura, en su divisa
            net_credit = sum(c.total for c in credits)
            cash_item = (cash if it.ccy == t.ccy else self.ph.fx.conv(cash, t.ccy, it.ccy, t.date)) + net_credit
            short = it.open - cash_item
            exc = self.explain_short(it, t, short, cash_item)
            if exc in ("SHORT_DISCOUNT", "SHORT_WITHHOLDING", "SHORT_BANK_CHARGES"):
                full(it, exc)
            else:
                self.emit(t, it, cash_item, "PARTIAL", rule, ev)
            return True
        # varias facturas: ¿descuento en todas?
        disc_sum, all_disc = 0, True
        for it in invoices:
            term = self.ph.terms.get(it.terms or "")
            if term and term.get("early_payment_discount_pct"):
                pct = float(term["early_payment_discount_pct"])
                disc_sum += it.total - int(round(it.total * pct / 100))
            else:
                all_disc = False
                break
        if all_disc and abs(disc_sum - sum(c.total for c in credits) - cash) <= 2 * len(invoices):
            for it in cands:
                full(it, "SHORT_DISCOUNT" if self.sign(it, t) > 0 else "")
            return True
        # pago de menos repartido: el más antiguo primero, la última queda parcial
        remaining = cash + sum(self.to_bank(c.open, c, t) for c in credits)
        for c in credits:
            full(c)
        for it in sorted(invoices, key=lambda i: (i.issue or dt.date.min, i.id)):
            need = self.to_bank(it.open, it, t)
            if remaining <= 0:
                break
            if remaining + tol >= need:
                full(it)
                remaining -= need
            else:
                amt = remaining if it.ccy == t.ccy else self.ph.fx.conv(remaining, t.ccy, it.ccy, t.date)
                self.emit(t, it, amt, "PARTIAL", rule, ev)
                remaining = 0
        return True

    # ------------------------------------------------------------ lotes (POLICIES §5.3)
    def _prep_batches(self):
        ph = self.ph
        self.dd_batches = defaultdict(list)
        for r in ph.dd:
            self.dd_batches[r["batch_id"]].append(r)
        self.dd_by_mandate = defaultdict(list)
        for r in ph.dd:
            self.dd_by_mandate[ascii_upper(r["mandate_id"])].append(r)
        self.mandate_party = {ascii_upper(c["sepa_mandate_id"]): cid for cid, c in ph.customers.items()
                              if c.get("sepa_mandate_id")}
        self.card_sets = defaultdict(list)
        for r in ph.cards:
            self.card_sets[r["settlement_id"]].append(r)
        self.card_by_amount = defaultdict(list)
        for sid, rs in self.card_sets.items():
            self.card_by_amount[(rs[0]["account_id"], sum(int(round(float(x["net"]) * 100)) for x in rs))].append(sid)
        self.cheque_deps = defaultdict(list)
        for r in ph.cheques:
            self.cheque_deps[r["deposit_id"]].append(r)
        self.run_rows = defaultdict(list)
        self.e2e_rows = defaultdict(list)
        for r in ph.runs:
            self.run_rows[r["run_id"]].append(r)
            self.e2e_rows[r["end_to_end_id"]].append(r)
        self.transfers_by_amount = defaultdict(list)
        for e2e, rs in self.e2e_rows.items():
            r0 = rs[0]
            self.transfers_by_amount[(r0["debtor_account_id"], int(round(float(r0["transfer_amount"]) * 100)))].append(e2e)
        self.used_batches = set()

    def item_by_number(self, number, entity=None, party=None, kinds=None, amount=None):
        cands = set()
        for v in num_variants(number):
            cands |= self.ph.by_num.get(v, set())
        out = [self.items[c] for c in cands]
        if kinds:
            out = [i for i in out if i.kind in kinds]
        if entity:
            ents = {entity} | PAYMENT_FACTORY.get(entity, set())
            out = [i for i in out if i.entity in ents] or out
        if party and len(out) > 1:
            out = [i for i in out if i.party == party] or out
        if amount is not None and len(out) > 1:
            out = [i for i in out if abs(i.open - amount) <= 2 or abs(i.total - amount) <= 2] or out
        out.sort(key=lambda i: (-(i.open > 0), i.issue or dt.date.min))
        return out[0] if out else None

    def m_dd_batch(self, t):
        if t.amount <= 0:
            return False
        for bid in re.findall(r"\b\d{9,12}\b", t.up):
            rs = self.dd_batches.get(bid)
            if rs and abs(int(round(float(rs[0]["batch_total"]) * 100)) - t.amount) <= 2:
                for r in rs:
                    amt = int(round(float(r["amount"]) * 100))
                    it = self.item_by_number(r["remittance_info"], t.entity, kinds={"ARI"}, amount=amt)
                    if not it or it.open <= 0:
                        party = self.mandate_party.get(ascii_upper(r["mandate_id"])) or                             self.ph.party_by_iban.get((r.get("debtor_iban") or "").replace(" ", ""))
                        if party:
                            alt = [i for i in self.open_items_of(party, t, {"ARI"}) if abs(i.open - amt) <= 2]
                            if alt:
                                it = alt[0]
                    if it:
                        exc = "PARTIAL" if it.open - amt > 2 and it.total - amt > 2 else ""
                        self.emit(t, it, amt, exc, "dd_batch", f"remesa {bid}")
                self.used_batches.add(bid)
                return True
        return False

    def m_dd_return(self, t):
        if t.amount >= 0:
            return False
        if not re.search(r"DEVOLUCION RECIBO|DEVOL\.? ?RECIBO|IMPAYE|RUCKLASTSCHRIFT|RUECKLASTSCHRIFT|RETURN|RETOUR|R-TRANS|UNPAID|IMPAGADO", t.up):
            return False
        amt = -t.amount
        cand = []
        m = re.search(r"(VTM-[A-Z]{2}-\d+)", t.up)
        if m:
            cand = [r for r in self.dd_by_mandate.get(m.group(1), []) if abs(int(round(float(r["amount"]) * 100)) - amt) <= 2]
        if not cand:
            for bid in re.findall(r"\d{9,12}", t.up):
                cand = [r for r in self.dd_batches.get(bid, []) if abs(int(round(float(r["amount"]) * 100)) - amt) <= 2]
                if cand:
                    break
        if not cand:
            if not m:
                return False
            party = self.mandate_party.get(m.group(1))
            if not party:
                return False
            its = [self.items[i] for i in self.ph.by_party.get(party, ()) if self.items[i].kind == "ARI"
                   and abs(self.items[i].total - amt) <= 2 and (self.items[i].issue or t.date) <= t.date]
            if not its:
                return False
            it = max(its, key=lambda i: i.issue or dt.date.min)
            self.emit(t, it, amt, "RETURNED", "dd_return_mandate", f"mandato {m.group(1)}")
            return True
        # cuotas iguales cada mes: la devuelta es la última cobrada ANTES de la devolución, nunca una futura
        past = [x for x in cand if x.get("collection_date") and
                0 <= (t.date - dt.date.fromisoformat(x["collection_date"][:10])).days <= 90]
        r = max(past or cand, key=lambda x: x["collection_date"])
        it = self.item_by_number(r["remittance_info"], t.entity, kinds={"ARI"})
        if not it:
            return False
        self.emit(t, it, amt, "RETURNED", "dd_return", f"mandato {r['mandate_id']}")
        return True

    def m_card(self, t):
        if t.amount <= 0:
            return False
        sids = self.card_by_amount.get((t.account, t.amount), [])
        sids = [s for s in sids if s not in self.used_batches]
        if not sids:
            return False
        sid = min(sids, key=lambda s: abs((dt.date.fromisoformat(self.card_sets[s][0]["settlement_date"]) - t.date).days))
        self.used_batches.add(sid)
        for r in self.card_sets[sid]:
            gross = int(round(float(r["gross"]) * 100))
            it = self.item_by_number(r["order_number"], t.entity, kinds={"ARI"}, amount=gross)
            if it:
                self.emit(t, it, gross, "", "card_settlement", f"liquidación {sid}")
                self.card_items[(r["card"], gross)] = it  # Por si llega un contracargo (plugin devoluciones).
        return True

    def m_cheque(self, t):
        if t.amount <= 0:
            return False
        dep = None
        for did in re.findall(r"RCH\d+|\b[A-Z]{2,4}\d{8,}\b", t.up):
            if did in self.cheque_deps:
                dep = did
                break
        if not dep:
            for did, rs in self.cheque_deps.items():
                if did not in self.used_batches and rs[0]["account_id"] == t.account and \
                        abs(int(round(float(rs[0]["deposit_total"]) * 100)) - t.amount) <= 2:
                    dep = did
                    break
        if not dep:
            return False
        self.used_batches.add(dep)
        for r in self.cheque_deps[dep]:
            amt = int(round(float(r["amount"]) * 100))
            it = None
            stub = re.sub(r"\D", "", r["stub_reference"] or "")
            if len(stub) >= 5:
                for iid in self.ph.by_suffix.get(stub[-6:].rjust(6, "0"), set()) | self.ph.by_num.get(stub, set()):
                    c = self.items[iid]
                    if c.side == "AR" and self.entity_ok(c, t) and (abs(c.open - amt) <= 2 or abs(c.total - amt) <= 2):
                        it = c
                        break
            if not it:
                it = self.find_by_party_amount(r["drawer"], amt, t, kinds={"ARI"})
            if it:
                exc = "PARTIAL" if it.open - amt > 2 else ""
                self.emit(t, it, min(amt, it.open) if exc == "" else amt, exc, "cheque_deposit", f"remesa {dep}")
                self.cheque_items[r["cheque_number"]] = it  # Por si llega devuelto (plugin devoluciones).
        return True

    def run_doc_item(self, r, t):
        party = self.ph.party_by_iban.get((r.get("beneficiary_iban") or "").replace(" ", ""))
        if not party:
            party = self.resolve_name(r["beneficiary"])
        kinds = {"API", "APC", "ARC"} if r["document_type"] != "INVOICE" else {"API", "ARC"}
        amt = abs(int(round(float(r["document_amount"]) * 100)))
        cands = set()
        for v in num_variants(r["document_number"]):
            cands |= self.ph.by_num.get(v, set())
        cands = [self.items[c] for c in cands if self.items[c].kind in kinds]
        ent = (r.get("debtor_account_id") or t.account or "").split("-")[0]
        if party:
            cands = [c for c in cands if c.party == party] or cands
        if len(cands) > 1:
            cands = [c for c in cands if abs(c.open - amt) <= 2 or abs(c.total - amt) <= 2 or c.open > 0] or cands

        def exact(i):  # factoring: el beneficiario es la financiera, manda el importe del documento
            a = amt if i.ccy == r["currency"] else self.ph.fx.conv(amt, r["currency"], i.ccy, t.date)
            return abs(i.open - a) <= max(2, a * 0.005) or abs(i.total - a) <= max(2, a * 0.005)
        # la sociedad que paga suele ser la de la factura, pero la holding paga por las filiales (ap_paid_by_holding)
        cands.sort(key=lambda i: (-exact(i), -(i.entity == ent), -(i.open > 0), i.issue or dt.date.min))
        return cands[0] if cands else None

    def emit_run_rows(self, t, rs, rule, ev):
        for r in rs:
            it = self.run_doc_item(r, t)
            if not it:
                continue
            amt = abs(int(round(float(r["document_amount"]) * 100)))
            if it.ccy != r["currency"]:
                amt = self.ph.fx.conv(amt, r["currency"], it.ccy, t.date)
            exc = ""
            if not it.is_credit:
                if it.open - amt > max(2, it.total * 0.005):
                    exc = "PARTIAL"
                elif it.ccy != t.ccy:
                    exc = "FX_DIFFERENCE"
            self.emit(t, it, amt, exc, rule, ev)

    def _confirming_due(self, rid, rs, t):
        """Confirming: cada proveedor (e2e) se paga en el vencimiento más tardío de sus facturas, y el banco carga
        de una vez los proveedores de ese día («N PROVEEDORES»). Devuelve {e2e: (fecha_pago, filas, importe)}."""
        cache = self.__dict__.setdefault("_conf_due", {})
        if rid not in cache:
            by_e2e = defaultdict(list)
            for r in rs:
                by_e2e[r["end_to_end_id"]].append(r)
            out = {}
            for e, g in by_e2e.items():
                dues = []
                for r in g:
                    it = self.run_doc_item(r, t)
                    dues.append(it.due if it and it.due else dt.date.fromisoformat(r["execution_date"]))
                amt = sum(int(round(float(r["document_amount"]) * 100)) for r in g)
                out[e] = (max(dues), g, amt)
            cache[rid] = out
        return cache[rid]

    @staticmethod
    def _cited_runs(t):
        """Ids de remesa del texto. El banco puede partirlos entre dos campos: «PR0101250 | 71007»."""
        return list(dict.fromkeys(RUN_ID.findall(t.up) + RUN_ID.findall(t.up.replace(" | ", ""))))

    def m_payment_run(self, t):
        if t.amount >= 0:
            return False
        amt = -t.amount
        if t.e2e and t.e2e in self.e2e_rows:
            self.emit_run_rows(t, self.e2e_rows[t.e2e], "payment_run_e2e", f"e2e {t.e2e}")
            return True
        for rid in self._cited_runs(t):
            rs = self.run_rows.get(rid)
            if not rs:
                continue
            if rs[0]["run_type"] == "aggregated":
                # Un solo cargo por remesa y cuenta (POLICIES §5.3). La suma no sirve: mezcla divisas. El run_id se repite entre sociedades.
                own = [r for r in rs if r["debtor_account_id"] == t.account]
                if not own or (rid, t.account) in self.used_batches:
                    continue
                self.used_batches.add((rid, t.account))
                self.emit_run_rows(t, own, "payment_run_aggregated", f"remesa {rid}")
                return True
            if rs[0]["run_type"] == "transfers":
                # un cargo por beneficiario: solo la transferencia de este importe
                sub = defaultdict(list)
                for r in rs:
                    sub[r["end_to_end_id"]].append(r)
                hits = [e for e, x in sub.items() if abs(int(round(float(x[0]["transfer_amount"]) * 100)) - amt) <= 2
                        and e not in self.used_batches]
                if not hits:
                    continue
                self.used_batches.add(hits[0])
                self.emit_run_rows(t, sub[hits[0]], "payment_run_transfer", f"remesa {rid}")
                return True
            # aggregated / confirming: un cargo por fichero, por fecha de ejecución o (confirming) por vencimiento
            groups = defaultdict(list)
            for r in rs:
                groups[r["execution_date"]].append(r)
            cand = [rs] + list(groups.values())
            if rs[0]["run_type"] == "confirming":
                conf = self._confirming_due(rid, rs, t)
                m = re.search(r"\b(\d+) PROVEEDOR", t.up)
                n = int(m.group(1)) if m else None
                pool = [(e, v) for e, v in conf.items() if (rid, e) not in self.used_batches
                        and -1 <= (t.date - v[0]).days <= 5]
                pick = None
                by_day = defaultdict(list)
                for e, v in pool:
                    by_day[v[0]].append((e, v))
                for d in sorted(by_day, key=lambda d: abs((t.date - d).days)):
                    grp = by_day[d]
                    if abs(sum(v[2] for _, v in grp) - amt) <= 2 and (n is None or n == len(grp)):
                        pick = grp
                        break
                if pick is None and n and n <= 4 and len(pool) <= 40:
                    for comb in combinations(pool, n):
                        if abs(sum(v[2] for _, v in comb) - amt) <= 2:
                            pick = list(comb)
                            break
                if pick:
                    for e, v in pick:
                        self.used_batches.add((rid, e))
                    self.emit_run_rows(t, [r for _, v in pick for r in v[1]], "payment_run_confirming",
                                       f"remesa {rid} · {len(pick)} proveedores al vencimiento")
                    return True
            for g in cand:
                key = (rid, g[0]["execution_date"], len(g))
                if key in self.used_batches:
                    continue
                st = sum({r["end_to_end_id"]: float(r["transfer_amount"]) for r in g}.values())
                if abs(int(round(st * 100)) - amt) <= 2:
                    self.used_batches.add(key)
                    self.emit_run_rows(t, g, "payment_run", f"remesa {rid}")
                    return True
        e2es = [e for e in self.transfers_by_amount.get((t.account, amt), []) if e not in self.used_batches]
        if e2es:
            def score(e):
                r0 = self.e2e_rows[e][0]
                d = abs((dt.date.fromisoformat(r0["execution_date"]) - t.date).days)
                nm = name_key(r0["beneficiary"]).split()
                hit = sum(1 for w in nm[:2] if w in t.up)
                return (-hit, d)
            e = min(e2es, key=score)
            r0 = self.e2e_rows[e][0]
            if abs((dt.date.fromisoformat(r0["execution_date"]) - t.date).days) <= 5:
                self.used_batches.add(e)
                self.emit_run_rows(t, self.e2e_rows[e], "payment_run_amount", f"transfer {e}")
                return True
        return False

    # ------------------------------------------------------------ contraparte (POLICIES §5.1, §5.5)
    def resolve_names(self, raw):
        """Contrapartes candidatas por nombre (puede haber homónimos). Las mejores primero."""
        k = name_key(raw)
        if not k:
            return []
        toks = k.split()
        scored = {}
        for pid in self.ph.name_index.get(toks[0], ()):
            for nk in self.ph.party_names.get(pid, ()):
                if nk == k:
                    sc = 1000
                elif len(k) >= 8 and (nk.startswith(k) or k.startswith(nk)):
                    sc = 100 + min(len(k), len(nk))
                else:
                    common = len(set(nk.split()) & set(toks))
                    sc = common * 10 if common >= 2 else 0
                if sc > scored.get(pid, 0):
                    scored[pid] = sc
        best = max(scored.values(), default=0)
        if not best:
            return []
        return [p for p, v in sorted(scored.items(), key=lambda x: -x[1]) if v >= best - 5]

    def resolve_name(self, raw):
        c = self.resolve_names(raw)
        return c[0] if c else None

    def payer_text(self, t):
        up = ascii_upper(t.text)
        for pat in (r"ORD\.?\s+(.+?)(?:\s+FRA\b|\s+FV|\s*\||$)", r"TRANSFERENCIAS?\s+DE\s+(.+?)(?:\s*CONCEPTO|$)",
                    r"TRANSFERENCIAS\s+(.+?)\s*\|", r"/ORDP/(.+?)/", r"SEPA-UEBERWEISUNG AN (.+)$",
                    r"TRANSFERENCIA A (.+?)(?:\s*\||$)", r"ABWA\+(.+)$", r"\|\s*\d{6,}-(.+?)(?:\s*\||$)",
                    r"ADEUDO A SU CARGO\s*\|\s*(.+?)\s+N\s+\d", r"^([A-Z][A-Z0-9 .,&'-]{5,}?)\s*\|\s*N\s+\d"):
            m = re.search(pat, up)
            if m:
                return m.group(1).replace("|", "").strip()
        return ""

    def resolve_parties(self, t):
        """Todas las contrapartes posibles del movimiento: IBAN conocido, IBAN aprendido y nombre (homónimos incluidos)."""
        out, how = [], ""
        if t.cp_iban:
            p = self.ph.party_by_iban.get(t.cp_iban) or self.iban_learned.get(t.cp_iban)
            if p:
                out.append(p)
                how = "iban"
        for raw in (t.cp_name, self.payer_text(t)):
            if raw:
                for p in self.resolve_names(raw):
                    if p not in out:
                        out.append(p)
                        how = how or "nombre"
        return out, how

    def scan_names(self, t):
        """Último recurso: un nombre completo de contraparte dentro del texto del banco (PIX RECEBIDO X, TT IN X,
        DIRECT CREDIT X…). Vale también si el banco lo corta al final del campo."""
        segs = [name_key(x) for x in re.split(r"\|", " | ".join(filter(None, (t.text, t.rmt, t.ref1, t.ref2))))]
        best, hits = 0, []
        for seg in segs:
            toks = seg.split()
            n = len(toks)
            for i, tok in enumerate(toks):
                for pid in self.ph.name_index.get(tok, ()):
                    for nk in self.ph.party_names.get(pid, ()):
                        nt = nk.split()
                        L = len(nt)
                        if toks[i:i + L] == nt:
                            sc = sum(len(w) for w in nt)
                        elif i + L > n and n - i >= 2 and toks[i:n - 1] == nt[:n - 1 - i] and nt[n - 1 - i].startswith(toks[-1]):
                            sc = sum(len(w) for w in toks[i:])  # cortado por el banco
                        else:
                            continue
                        if sc < 8 or (L == 1 and len(nt[0]) < 7):
                            continue
                        if sc > best:
                            best, hits = sc, [pid]
                        elif sc == best and pid not in hits:
                            hits.append(pid)
        return hits

    def resolve_party(self, t):
        ps, how = self.resolve_parties(t)
        return (ps[0], how) if ps else (None, "")

    def open_items_of(self, party, t, kinds):
        pids = {party} | self.ph.children.get(party, set())
        out = []
        for pid in pids:
            for iid in self.ph.by_party.get(pid, ()):
                it = self.items[iid]
                if it.open > 0 and it.kind in kinds and self.entity_ok(it, t) and (it.issue is None or it.issue <= t.date):
                    out.append(it)
        out.sort(key=lambda i: (i.issue or dt.date.min, i.id))
        return out

    def find_by_party_amount(self, raw_name, amt, t, kinds):
        p = self.resolve_name(raw_name)
        if not p:
            return None
        for it in self.open_items_of(p, t, kinds):
            if abs(it.open - amt) <= 2:
                return it
        return None

    def m_refs(self, t):
        kinds = {"ARI", "ARC", "APC"} if t.amount > 0 else {"API", "APC", "ARC"}
        cands = set()
        for tok in t.tokens:
            cands |= self.ph.by_num.get(tok, set())
        parties, _ = self.resolve_parties(t)
        party = parties[0] if parties else None
        fam = set()
        for p in parties:
            fam |= {p} | self.ph.children.get(p, set())
        items = [self.items[c] for c in cands if self.items[c].kind in kinds and self.entity_ok(self.items[c], t)]
        if fam:
            items = [i for i in items if i.party in fam] or items
        if fam and not items:  # referencias cortadas a 16/35/38 caracteres: prefijo del número
            pref = [tok for tok in t.tokens if len(tok) >= 5 and not tok.isdigit()]
            if pref:
                for p in fam:
                    for iid in self.ph.by_party.get(p, ()):
                        it = self.items[iid]
                        if it.open > 0 and it.kind in kinds:
                            nn = re.sub(r"[^A-Z0-9]", "", ascii_upper(it.number))
                            if any(nn.startswith(x) and len(x) < len(nn) for x in pref):
                                items.append(it)
                if len(items) > 1:
                    items = [i for i in items if abs(self.to_bank(i.open, i, t) - abs(t.amount)) <= 2] or items
        # sufijos de 6 cifras ("RE RE250107423 108497 108019") cuando ya hay contraparte
        if fam:
            for tok in t.tokens:
                if tok.isdigit() and len(tok) == 6:
                    for iid in self.ph.by_suffix.get(tok, ()):
                        it = self.items[iid]
                        if it.party in fam and it.kind in kinds:
                            items.append(it)
        items = list({i.id: i for i in items}.values())
        if not items:
            return False
        open_items = [i for i in items if i.open > 0]
        if not open_items:
            # ya liquidadas: ¿pago duplicado? (POLICIES §3 DUPLICATE_PAYMENT)
            it = items[0]
            if abs(abs(t.amount) - self.to_bank(it.total, it, t)) <= max(2, it.total * 0.005):
                if it.side == "AR" and it.method == "dd" and not re.search(r"ADEUDO|RECIBO|DIRECT DEBIT|LASTSCHRIFT|PRELEV", t.up):
                    # recibo devuelto que el cliente vuelve a pagar por transferencia: no es duplicado
                    self.emit(t, it, it.total, "", "ref_represented", f"ref {it.number} · recibo devuelto, pagado por transferencia")
                    return True
                self.emit(t, it, it.total, "DUPLICATE_PAYMENT", "ref_duplicate", f"ref {it.number}")
                return True
            return False
        if len(open_items) > 1:
            open_items = self.best_subset(open_items, abs(t.amount), t) or open_items
        return self.settle(t, [i.id for i in open_items], "ref", "refs " + ",".join(i.number for i in open_items[:5]),
                           party=party or open_items[0].party)

    def best_subset(self, items, cash, t, max_n=40):
        """Qué partidas suman exactamente el importe. Orden de preferencia: todas, las más antiguas (FIFO), DP."""
        items = sorted(items, key=lambda i: (i.issue or dt.date.min, i.id))[:max_n]
        vals = [int(round(self.sign(i, t) * self.to_bank(i.open, i, t))) for i in items]
        if abs(sum(vals) - cash) <= 2:
            return items
        acc = 0
        for k, v in enumerate(vals):  # el cliente paga las más antiguas
            acc += v
            if abs(acc - cash) <= 2:
                return items[: k + 1]
        for i in range(1, len(vals)):  # o un tramo seguido de fechas
            acc = 0
            for j in range(i, len(vals)):
                acc += vals[j]
                if abs(acc - cash) <= 2:
                    return items[i: j + 1]
                if acc > cash + 2 and min(vals) > 0:
                    break
        reach = {0: ()}
        for j, v in enumerate(vals):
            new = {}
            for sm, comb in reach.items():
                ns = sm + v
                if ns not in reach and ns not in new and ns <= cash + 2 + sum(-x for x in vals if x < 0):
                    new[ns] = comb + (j,)
            reach.update(new)
            if len(reach) > 300000:
                break
            for d in (0, 1, -1, 2, -2):
                if cash + d in reach and reach[cash + d]:
                    return [items[x] for x in reach[cash + d]]
        return None

    def m_party_amount(self, t):
        parties, how = self.resolve_parties(t)
        if not parties:
            parties, how = self.scan_names(t), "nombre_texto"
        tried = parties[:MAX_PARTIES_TRIED]
        for party in tried:
            if self._party_amount(t, party, how):
                return True
        return self._party_rescue(t, tried)

    def _party_rescue(self, t, tried):
        """Terceros que el camino normal no llegó a probar: homónimos de más y nombres que solo ve scan_names.
        Solo con evidencia exacta y un único candidato: POLICIES §3 UNIDENTIFIED pide no forzar."""
        cash = abs(t.amount)
        exact, subsets = {}, {}
        for party in dict.fromkeys([*self.resolve_parties(t)[0], *self.scan_names(t)]):
            if party in tried:
                continue
            its = self.open_items_of(party, t, self._party_kinds(party, t))
            same = [i for i in its if abs(self.to_bank(i.open, i, t) - cash) <= 2]
            if same:
                exact[party] = same[:1]
            elif 1 < len(its) <= MAX_SUBSET_ITEMS:  # con más, muchas combinaciones suman lo mismo: no identifica
                sub = self.best_subset(its, cash, t, max_n=MAX_SUBSET_ITEMS)
                if sub:
                    subsets[party] = sub
        for found, rule in ((exact, "party_rescue_exact"), (subsets, "party_rescue_subset")):
            if found:
                if len(found) > 1:
                    return False
                party, picks = next(iter(found.items()))
                return self.settle(t, [i.id for i in picks], rule, f"contraparte {party}", party=party)
        return False

    @staticmethod
    def _party_kinds(party, t):
        if party.startswith("S") and t.amount > 0:
            return {"APC"}  # el proveedor nos devuelve un abono suyo (POLICIES §4, último punto)
        if party.startswith("C") and t.amount < 0:
            return {"ARC"}  # rappel o abono pagado al cliente por transferencia
        return {"ARI", "ARC"} if t.amount > 0 else {"API", "APC"}

    def _party_amount(self, t, party, how):
        its = self.open_items_of(party, t, self._party_kinds(party, t))
        if not its:
            return False
        cash = abs(t.amount)
        same = [i for i in its if abs(self.to_bank(i.open, i, t) - cash) <= 2]
        if same:
            # mes de servicio en el texto (POLICIES §5.6) o FIFO
            m = re.search(r"\b(ENE|FEB|MAR|ABR|MAY|JUN|JUL|AGO|SEP|OCT|NOV|DIC)[A-Z]*\.?\s*(\d{2})\b", t.up)
            if m and len(same) > 1:
                mon = ["ENE", "FEB", "MAR", "ABR", "MAY", "JUN", "JUL", "AGO", "SEP", "OCT", "NOV", "DIC"].index(m.group(1)) + 1
                pick = [i for i in same if i.issue and ((i.issue.month - 2) % 12 + 1) == mon]
                if pick:
                    same = pick
            return self.settle(t, [same[0].id], f"party_amount_{how}", f"contraparte {party}", party=party)
        sub = self.best_subset([i for i in its if self.sign(i, t) > 0] + [i for i in its if self.sign(i, t) < 0], cash, t,
                               max_n=MAX_SUBSET_ITEMS)
        if sub:
            return self.settle(t, [i.id for i in sub], f"party_subset_{how}", f"contraparte {party}")
        inv = [i for i in its if self.sign(i, t) > 0]
        # una factura con descuento / retención / comisiones / FX
        # descuento y retención son fórmulas exactas; las comisiones son una horquilla: primero lo exacto
        found = {}
        for it in inv:
            exp = self.to_bank(it.open, it, t)
            short = exp - cash
            if 0 < short <= 0.11 * exp:
                cash_item = cash if it.ccy == t.ccy else self.ph.fx.conv(cash, t.ccy, it.ccy, t.date)
                exc = self.explain_short(it, t, it.open - cash_item, cash_item)
                if exc in EXPLAINED_SHORTFALLS:
                    found.setdefault(exc, it)
        for exc in EXPLAINED_SHORTFALLS:
            if exc in found:
                return self.settle(t, [found[exc].id], f"party_short_{how}", f"contraparte {party}")
        for it in inv:
            exp = self.to_bank(it.open, it, t)
            short = exp - cash
            if it.ccy != t.ccy and exp and abs(short) <= 0.03 * exp:
                return self.settle(t, [it.id], f"party_fx_{how}", f"contraparte {party}")
        if re.search(r"A CUENTA|ON ACCOUNT|ACOMPTE|ABSCHLAG|PARCIAL", t.up) and inv:
            return self.settle(t, [inv[0].id], f"party_partial_{how}", f"contraparte {party}")
        return False

    # ------------------------------------------------------------ anulaciones (POLICIES §4)
    def _find_reversals(self):
        out = set()
        by_amt = defaultdict(list)
        for t in self.ph.txns:
            by_amt[(t.account, abs(t.amount))].append(t)
        for t in self.ph.txns:
            if "ANULACION" in t.up or "STORNO" in t.up or "REVERSAL" in t.up or "ANNULATION" in t.up:
                prev = [x for x in by_amt[(t.account, abs(t.amount))]
                        if x.id != t.id and x.amount == -t.amount and x.date <= t.date and x.id not in out]
                if prev:
                    out.add(t.id)
                    out.add(max(prev, key=lambda x: (x.date, x.id)).id)
        return out

    # ------------------------------------------------------------ bucle principal
    def run(self):
        for t in self.ph.txns:
            n0 = len(self.rows)
            if t.id in self.reversal:
                self.emit_cat(t, "BANK_REVERSAL", "", "reversal_pair")
                continue
            done = False
            for m in (self.m_dd_batch, self.m_dd_return, self.m_card, self.m_cheque, self.m_payment_run):
                if m(t):
                    done = len(self.rows) > n0
                    if done:
                        break
            if done:
                continue
            cat, why = classify_nonitem(t)
            strong = cat in ("CASH_POOL", "PAYROLL", "SOCIAL_SECURITY", "TAX_PAYMENT", "TAX_REFUND", "LEASING",
                             "LOAN_REPAYMENT", "LOAN_DRAWDOWN", "INTERCOMPANY", "INTERNAL_TRANSFER", "FX_DEAL",
                             "CARD_EXPENSES", "INSURANCE", "CUSTOMER_ADVANCE", "CUSTOMER_REFUND",
                             "INTEREST_INCOME", "INTEREST_EXPENSE")
            if cat and strong:
                self.emit_cat(t, cat, "", why)
                continue
            for p in self.plugins:
                if p.match(self, t) and len(self.rows) > n0:
                    done = True
                    break
            if done:
                continue
            for m in (self.m_refs, self.m_party_amount):
                if m(t) and len(self.rows) > n0:
                    done = True
                    break
            if done:
                continue
            if cat:
                self.emit_cat(t, cat, "", why)
            elif t.amount > 0:
                if not (duplicados.settle_duplicate(self, t) and len(self.rows) > n0):
                    self.emit_cat(t, "UNIDENTIFIED", "UNIDENTIFIED", "fallback_receipt")
            else:
                self.emit_cat(t, "BANK_FEES", "", "fallback_debit")
        return self.rows
