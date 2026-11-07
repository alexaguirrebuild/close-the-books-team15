"""Carga de una fase (sample / train / live / final), maestros e índices. Solo librería estándar."""
from __future__ import annotations

import csv
import datetime as dt
import glob
import gzip
import os
import re
import unicodedata
from bisect import bisect_right
from collections import defaultdict
from pathlib import Path

DATA = Path(os.environ.get("BANKREC_DATA", Path(__file__).resolve().parents[1] / "data"))

SEPA = set("AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE IS LI NO CH GB MC SM VA AD".split())
LEGAL = set("""SL SLU SA SAU SCOOP COOP S COOP GMBH CO KG EK E K SARL EURL SAS SASU SPA SRL SRLS LTD LLC INC PLC BV NV AG SE
LDA UNIPESSOAL SAPI CV DE RL LTDA ME EIRELI SARLAU PTY OHG UG MBH CIA Y AND ET THE LA EL LOS LAS DU DES LE""".split())


def rows(path):
    p = str(path)
    if p.endswith(".gz"):
        fh = gzip.open(p, "rt", encoding="utf-8", newline="")
    else:
        fh = open(p, encoding="utf-8-sig", newline="")
    with fh:
        yield from csv.DictReader(fh)


def cents(s) -> int:
    if s is None or s == "":
        return 0
    return int(round(float(s) * 100))


def day(s):
    return dt.date.fromisoformat(s[:10]) if s else None


def ascii_upper(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return s.upper()


def norm_text(s: str) -> str:
    return re.sub(r"[^A-Z0-9]+", " ", ascii_upper(s)).strip()


def name_key(s: str) -> str:
    toks = [t for t in norm_text(s).split() if t not in LEGAL and len(t) > 1]
    return " ".join(toks)


def num_variants(num: str) -> set[str]:
    a = re.sub(r"[^A-Z0-9]", "", ascii_upper(num))
    dg = re.sub(r"\D", "", num or "")
    out = set()
    if len(a) >= 4:
        out.add(a)
    if len(dg) >= 5:
        out.add(dg)
        out.add(dg.lstrip("0"))
    return {v for v in out if v}


TOKEN_RE = re.compile(r"[A-Z0-9][A-Z0-9\-/\.]{2,}")


def text_tokens(*parts: str) -> set[str]:
    """Candidatos a número de documento en los textos del banco."""
    out = set()
    for p in parts:
        up = ascii_upper(p)
        for tok in TOKEN_RE.findall(up):
            tok = tok.strip("-/.")
            a = re.sub(r"[^A-Z0-9]", "", tok)
            if len(a) >= 4:
                out.add(a)
            dg = re.sub(r"\D", "", tok)
            if len(dg) >= 5:
                out.add(dg)
                out.add(dg.lstrip("0"))
        # números partidos por el corte de 38 caracteres del N43 (" | ")
        joined = re.sub(r"\s*\|\s*", "", up)
        for tok in TOKEN_RE.findall(joined):
            a = re.sub(r"[^A-Z0-9]", "", tok)
            if len(a) >= 6:
                out.add(a)
    return out


class Item:
    __slots__ = ("id", "kind", "side", "entity", "number", "party", "party_name", "ccy", "total", "open",
                 "issue", "due", "terms", "po", "method", "related", "reason")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    @property
    def is_credit(self):
        return self.kind in ("ARC", "APC")


class Txn:
    __slots__ = ("id", "account", "entity", "bank", "fmt", "date", "vdate", "amount", "ccy", "code", "subcode", "ref1",
                 "ref2", "text", "cp_name", "cp_iban", "e2e", "rmt", "orig_amount", "orig_ccy", "up", "tokens")

    def __init__(self, r):
        self.id = r["bank_txn_id"]
        self.account = r["account_id"]
        self.entity = r["entity"]
        self.bank = r["bank"]
        self.fmt = r["statement_format"]
        self.date = day(r["booking_date"])
        self.vdate = day(r["value_date"])
        self.amount = cents(r["amount"])
        self.ccy = r["currency"]
        self.code = r["bank_code"]
        self.subcode = r["bank_subcode"]
        self.ref1, self.ref2 = r["ref1"], r["ref2"]
        self.text = r["text"]
        self.cp_name = r["counterparty_name"]
        self.cp_iban = r["counterparty_account"].replace(" ", "")
        self.e2e = r["end_to_end_id"]
        self.rmt = r["remittance_info"]
        self.orig_amount = cents(r["orig_amount"]) if r["orig_amount"] else None
        self.orig_ccy = r["orig_currency"] or None
        self.up = ascii_upper(" | ".join([self.text, self.ref1, self.ref2, self.rmt, self.cp_name]))
        self.tokens = text_tokens(self.text, self.ref1, self.ref2, self.rmt, self.e2e)


class FX:
    def __init__(self, path):
        self.s = defaultdict(list)
        for r in rows(path):
            self.s[r["currency"]].append((day(r["date"]), float(r["units_per_eur"])))
        for v in self.s.values():
            v.sort()
        self.dates = {k: [d for d, _ in v] for k, v in self.s.items()}

    def rate(self, ccy, d):
        if ccy == "EUR" or ccy not in self.s:
            return 1.0
        i = bisect_right(self.dates[ccy], d) - 1
        return self.s[ccy][max(i, 0)][1]

    def conv(self, amount, frm, to, d):
        if frm == to:
            return amount
        return amount / self.rate(frm, d) * self.rate(to, d)


PHASE_START = {"live": dt.date(2025, 7, 1), "final": dt.date(2026, 1, 1)}
RECENT_DAYS = 180


class Phase:
    def __init__(self, name: str, holdout: "dt.date | None" = None):
        self.name = name
        self.holdout = holdout
        self.dir = DATA / name
        mdir = DATA / "masters"
        final_m = DATA / "final" / "masters_final"
        if name == "final" and final_m.exists():
            mdir = final_m
        self.mdir = mdir
        self.entity_country = {r["entity"]: r.get("country") for r in rows(mdir / "entities.csv")}
        self.customers = {r["customer_id"]: r for r in rows(mdir / "customers.csv")}
        self.suppliers = {r["supplier_id"]: r for r in rows(mdir / "suppliers.csv")}
        self.accounts = {r["account_id"]: r for r in rows(mdir / "bank_accounts.csv")}
        self.categories = [r["category"] for r in rows(mdir / "categories.csv")]
        self.terms = {r["code"]: r for r in rows(mdir / "payment_terms.csv")}
        self.fx = FX(mdir / "fx_rates.csv")
        self.items: dict[str, Item] = {}
        self._load_items()
        self.txns = [Txn(r) for r in rows(self.dir / "bank_transactions.csv.gz")]
        self.txns.sort(key=lambda t: (t.date, t.id))
        if holdout:
            self._cut_holdout(holdout)
        self.dd = self._prev_csvs("direct_debit_batches") + self._csvs("direct_debit_batches")
        self.cards = self._csvs("card_settlements")
        self.cheques = self._csvs("cheque_deposits")
        self.runs = self._prev_csvs("payment_runs") + self._csvs("payment_runs")
        self._index()

    # ---------- carga ----------
    def _prev_csvs(self, sub, months=2):
        """Remesas de la fase anterior de los últimos `months` meses: el confirming de junio se carga en julio,
        y las devoluciones de principios de mes citan remesas del mes anterior."""
        order = ["train", "live", "final"]
        if self.name not in order or order.index(self.name) == 0 or not self.txns:
            return []
        prev = DATA / order[order.index(self.name) - 1] / sub
        y, m = self.txns[0].date.year, self.txns[0].date.month
        keep = set()
        for _ in range(months):
            m -= 1
            if m == 0:
                y, m = y - 1, 12
            keep.add(f"{y}{m:02d}")
        out = []
        for f in sorted(glob.glob(str(prev / "*.csv"))):
            if any(re.search(rf"_{k}[_.]", Path(f).name) for k in keep):
                out.extend(rows(f))
        return out

    def _csvs(self, sub):
        out = []
        for p in sorted(glob.glob(str(self.dir / sub / "*.csv"))):
            out.extend(rows(p))
        return out

    def _add_ar(self, r, open_=None):
        self.items[r["item_id"]] = Item(
            id=r["item_id"], kind="ARI", side="AR", entity=r["entity"], number=r["document_number"],
            party=r["customer_id"], party_name=r["customer_name"], ccy=r["currency"], total=cents(r["total_amount"]),
            open=cents(r["total_amount"]) if open_ is None else open_, issue=day(r["issue_date"]), due=day(r["due_date"]),
            terms=r.get("payment_terms"), po=r.get("customer_po"), method=r.get("payment_method"))

    def _add_ap(self, r, open_=None):
        self.items[r["item_id"]] = Item(
            id=r["item_id"], kind="API", side="AP", entity=r["entity"], number=r["supplier_invoice_number"],
            party=r["supplier_id"], party_name=r["supplier_name"], ccy=r["currency"], total=cents(r["total_amount"]),
            open=cents(r["total_amount"]) if open_ is None else open_, issue=day(r["issue_date"]), due=day(r["due_date"]),
            terms=r.get("payment_terms"), po=r.get("our_po"), method=r.get("payment_method"))

    def _add_cn(self, r, open_=None):
        side = r["side"]
        self.items[r["item_id"]] = Item(
            id=r["item_id"], kind="ARC" if side == "AR" else "APC", side=side, entity=r["entity"],
            number=r["document_number"], party=r["party_id"], party_name=r["party_name"], ccy=r["currency"],
            total=cents(r["total_amount"]), open=cents(r["total_amount"]) if open_ is None else open_,
            issue=day(r["issue_date"]), related=r.get("related_document"), reason=r.get("reason"))

    def _load_items(self):
        # fases anteriores: solo para enriquecer las partidas abiertas al inicio (condiciones, pedido, fecha)
        order = ["train", "live", "final"]
        prev = order[: order.index(self.name)] if self.name in order else []
        start = {}
        p_open = self.dir / "open_items_at_start.csv.gz"
        if p_open.exists():
            start = {r["item_id"]: r for r in rows(p_open)}
        # cerradas recientes con saldo 0: las necesitan las devoluciones (RETURNED) y los pagos duplicados
        recent = PHASE_START.get(self.name, dt.date.max) - dt.timedelta(days=RECENT_DAYS)
        for ph in prev:
            d = DATA / ph
            if not d.exists() or not start:
                continue
            for fn, add in (("ar_invoices", self._add_ar), ("ap_invoices", self._add_ap), ("credit_notes", self._add_cn)):
                p = d / f"{fn}.csv.gz"
                if p.exists():
                    for r in rows(p):
                        if r["item_id"] in start:
                            add(r, cents(start[r["item_id"]]["open_amount"]))
                        elif r["issue_date"] and day(r["issue_date"]) >= recent:
                            add(r, 0)
        for iid, r in start.items():  # las que no se pudieron enriquecer
            if iid not in self.items:
                kind = r["item_type"] if r["item_type"] in ("ARI", "ARC", "API", "APC") else iid[:3]
                self.items[iid] = Item(id=iid, kind=kind, side=kind[:2], entity=r["entity"], number=r["document_number"],
                                       party=r["party_id"], party_name="", ccy=r["currency"], total=cents(r["total_amount"]),
                                       open=cents(r["open_amount"]), issue=day(r["issue_date"]), due=day(r["due_date"]))
        for fn, add in (("ar_invoices", self._add_ar), ("ap_invoices", self._add_ap), ("credit_notes", self._add_cn)):
            p = self.dir / f"{fn}.csv.gz"
            if p.exists():
                for r in rows(p):
                    if r["item_id"] not in self.items:
                        add(r)

    def _cut_holdout(self, cut):
        """Simula LIVE dentro de TRAIN. Saldo a `cut` = saldo oficial al inicio de LIVE (1-jul-2025)
        + lo cobrado según el golden entre `cut` y el final de TRAIN (las devoluciones RETURNED restan).
        Es exacto por construcción; las facturas emitidas desde `cut` entran por su total, como en LIVE."""
        live_open = {r["item_id"]: cents(r["open_amount"]) for r in rows(DATA / "live" / "open_items_at_start.csv.gz")}
        date_of = {t.id: t.date for t in self.txns}
        paid = defaultdict(int)
        for r in rows(self.dir / "golden_allocations.csv.gz"):
            if not r["item_id"]:
                continue
            d = date_of.get(r["bank_txn_id"])
            if d is None or d < cut:
                continue
            if r["exception"] == "RETURNED":
                paid[r["item_id"]] -= cents(r["amount"])
            elif r["exception"] != "DUPLICATE_PAYMENT":
                paid[r["item_id"]] += cents(r["amount"])
        keep = {}
        for iid, it in self.items.items():
            if it.issue and it.issue >= cut:
                keep[iid] = it
                continue
            o = min(live_open.get(iid, 0) + paid.get(iid, 0), it.total)
            if o > 0:
                it.open = o
                keep[iid] = it
            elif it.issue and it.issue >= cut - dt.timedelta(days=RECENT_DAYS):
                it.open = 0
                keep[iid] = it
        self.items = keep
        self.txns = [t for t in self.txns if t.date >= cut]

    # ---------- índices ----------
    def _index(self):
        self.by_num = defaultdict(set)
        self.by_suffix = defaultdict(set)
        self.by_party = defaultdict(list)
        self.by_po = defaultdict(set)
        for it in self.items.values():
            for v in num_variants(it.number):
                self.by_num[v].add(it.id)
            dg = re.sub(r"\D", "", it.number or "")
            if len(dg) >= 6:
                self.by_suffix[dg[-6:]].add(it.id)
            if it.po and len(re.sub(r"[^A-Z0-9]", "", it.po.upper())) >= 5:
                for v in num_variants(it.po):
                    self.by_po[v].add(it.id)
            self.by_party[it.party].append(it.id)
        for v in self.by_party.values():
            v.sort(key=lambda i: (self.items[i].issue or dt.date.min, i))
        # contrapartes: IBAN y nombre
        self.party_by_iban = {}
        self.party_names = {}  # party_id -> set(name_key)
        self.party_country = {}
        self.children = defaultdict(set)
        for c in self.customers.values():
            pid = c["customer_id"]
            if c.get("iban"):
                self.party_by_iban[c["iban"].replace(" ", "")] = pid
            self.party_names[pid] = {k for k in (name_key(c["name"]), name_key(c.get("trade_name", ""))) if k}
            self.party_country[pid] = c.get("country")
            if c.get("parent_customer_id"):
                self.children[c["parent_customer_id"]].add(pid)
        for s in self.suppliers.values():
            pid = s["supplier_id"]
            if s.get("iban"):
                self.party_by_iban[s["iban"].replace(" ", "")] = pid
            self.party_names[pid] = {k for k in (name_key(s["name"]),) if k}
            self.party_country[pid] = s.get("country")
        for it in self.items.values():  # nombre impreso en la factura (antes de renombrados)
            if it.party_name:
                k = name_key(it.party_name)
                if k:
                    self.party_names.setdefault(it.party, set()).add(k)
        for r in self.dd:
            if r.get("debtor_iban"):
                self.party_by_iban.setdefault(r["debtor_iban"].replace(" ", ""), None)
        self.name_index = defaultdict(set)  # primer token significativo -> party ids
        for pid, keys in self.party_names.items():
            for k in keys:
                toks = k.split()
                if toks:
                    self.name_index[toks[0]].add(pid)
