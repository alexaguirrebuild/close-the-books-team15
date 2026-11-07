"""Mesa de conciliación: servidor local + SPA para que tesorería revise lo que propone el motor.

    python -m bankrec.ui --phase sample --port 8765

Solo librería estándar. Lee lo que deja `python -m bankrec.run` en out/ (explain, score, pdf_text) y los datos de la fase
en data/<fase>/. No toca el motor: las correcciones del contable se guardan en out/overrides_<fase>.json y se re-exporta
la entrega revisada en out/sub_<fase>_revisado.csv.gz (la del motor, out/sub_<fase>.csv.gz, no se pisa).

API (JSON; todas aceptan ?phase=sample|holdout|live|final)
    GET  /api/summary                    KPIs del cierre, reglas, excepciones, categorías y score
    GET  /api/transactions               lista filtrada y paginada (state, entity, exc, rule, conf, q, sort, offset, limit)
    GET  /api/transaction/<bank_txn_id>  ficha de auditoría: banco, propuesta, cuadre, documentos, cláusulas, golden
    GET  /api/items?q=&party=&entity=    buscador de partidas para reasignar
    GET  /api/document/<fase>/<pdfs|remittance_advices>/<entidad>/<fichero>   documento original
    GET  /api/export                     descarga la entrega revisada (csv.gz)
    POST /api/override                   {txn_id, action: confirm|edit|reset, rows:[...], note}

Desde una worktree (.worktrees/v-ui) busca data/ y out/ subiendo carpetas; BANKREC_DATA y BANKREC_OUT mandan si están.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import io
import json
import os
import re
import sys
import threading
import time
import traceback
from collections import Counter, OrderedDict, defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

PKG = Path(__file__).resolve().parent
STATIC = PKG / "ui_static"


def find_root() -> Path:
    start = PKG.parent
    for p in (start, *start.parents):
        if (p / "data" / "masters").is_dir():
            return p
    return start


ROOT = find_root()
os.environ.setdefault("BANKREC_DATA", str(ROOT / "data"))
os.environ.setdefault("BANKREC_OUT", str(ROOT / "out"))
DATA = Path(os.environ["BANKREC_DATA"])
OUT = Path(os.environ["BANKREC_OUT"])

PHASES = ["sample", "holdout", "live", "final"]
PHASE_DIR = {"sample": "sample", "holdout": "train", "live": "live", "final": "final"}
PHASE_LABEL = {"sample": "Muestra (jun-2025)", "holdout": "Holdout TRAIN", "live": "LIVE", "final": "FINAL"}

EXC = ["PARTIAL", "OVERPAYMENT", "SHORT_DISCOUNT", "SHORT_BANK_CHARGES", "SHORT_WITHHOLDING", "SHORT_DISPUTE",
       "DUPLICATE_PAYMENT", "UNIDENTIFIED", "RETURNED", "FX_DIFFERENCE"]
EXC_ES = {
    "PARTIAL": ("Pago parcial", "La partida sigue con saldo abierto tras este movimiento (POLICIES §3)."),
    "OVERPAYMENT": ("Sobrepago", "El cliente pagó más que el saldo abierto; el exceso queda en su cuenta (§3)."),
    "SHORT_DISCOUNT": ("Dto. pronto pago", "Descuento por pronto pago dentro de plazo + 3 días de gracia (§3)."),
    "SHORT_BANK_CHARGES": ("Comisión banco intermediario", "Transferencia no SEPA con comisión ≤ 60 EUR y ≤ 2 % (§3)."),
    "SHORT_WITHHOLDING": ("Retención fiscal", "Retención en origen del cliente (1,25 %, 1,5 % o 10 %) (§3)."),
    "SHORT_DISPUTE": ("Deducción en disputa", "El cliente descuenta un importe con motivo; esa parte sigue abierta (§3)."),
    "DUPLICATE_PAYMENT": ("Pago duplicado", "La partida ya estaba liquidada y se paga otra vez (§3)."),
    "UNIDENTIFIED": ("No identificado", "Cobro sin partida abierta: no se fuerza el cruce (§3)."),
    "RETURNED": ("Devolución", "Recibo, cheque o transferencia devuelta: reabre la partida (§3)."),
    "FX_DIFFERENCE": ("Diferencia de cambio", "Cobro en otra divisa convertido al tipo corporativo del banco (§3)."),
}
CONF_ES = {"alta": "Confianza alta", "media": "Confianza media", "baja": "Revisión humana"}

# regla -> (título para un contable, por qué se propone, confianza, apartado de POLICIES)
RULES = {
    "ref": ("Referencia de factura en el banco", "El texto del extracto cita el número de la factura (o una variante: "
            "sin prefijo, sin ceros, truncado).", "alta", "§5.5"),
    "ref_duplicate": ("Pago duplicado por referencia", "La referencia apunta a una partida ya cobrada: es un segundo pago.",
                      "media", "§3 DUPLICATE_PAYMENT"),
    "ref_represented": ("Recibo devuelto, pagado después por transferencia", "La factura tenía un recibo devuelto y el "
                        "cliente la paga ahora por transferencia citando su número.", "media", "§5.10"),
    "remittance": ("Aviso de pago del cliente", "El cliente envió un aviso con las facturas que paga y sus deducciones. "
                   "La suma del aviso cuadra con el extracto.", "alta", "§5.4"),
    "remittance_gap": ("Aviso de pago con diferencia", "Hay aviso de pago, pero no cuadra al céntimo con el extracto: la "
                       "diferencia se ha clasificado por regla. Conviene mirarlo.", "baja", "§5.4"),
    "dd_batch": ("Remesa de recibos domiciliados", "El abono de la remesa liquida todos los recibos del fichero de la "
                 "remesa (SEPA/BACS/ACH).", "alta", "§5.3"),
    "dd_return": ("Devolución de recibo", "Recibo devuelto por el banco del deudor (R-transaction): reabre la factura.",
                  "alta", "§3 RETURNED"),
    "dd_return_mandate": ("Devolución de recibo (por mandato)", "Devolución identificada por el mandato SEPA del deudor.",
                          "alta", "§3 RETURNED"),
    "card_settlement": ("Liquidación de TPV", "La liquidación del adquirente liquida los pedidos con tarjeta al bruto; "
                        "la comisión del adquirente es contractual.", "alta", "§4, §5.3"),
    "cheque_deposit": ("Remesa de cheques", "El ingreso de la remesa liquida los cheques que la componen.", "alta", "§5.3"),
    "payment_run_transfer": ("Remesa de pagos a proveedores", "La transferencia sale de nuestro fichero de pagos: "
                             "liquida los documentos de ese beneficiario.", "alta", "§5.3"),
    "payment_run_e2e": ("Transferencia por EndToEndId", "El EndToEndId del banco coincide con el de nuestro fichero de pagos.",
                        "alta", "§5.3"),
    "payment_run_confirming": ("Confirming al vencimiento", "Cargo de la remesa de confirming: liquida los documentos que "
                               "vencen ese día.", "alta", "§5.3"),
    "dispute_settlement": ("Liquidación de importe en disputa", "Pago posterior del importe que el cliente había "
                           "descontado: cierra la partida sin excepción.", "media", "§3 SHORT_DISPUTE"),
    "reversal_pair": ("Error del banco y su anulación", "Movimiento erróneo y su contraasiento: ambos son BANK_REVERSAL.",
                      "alta", "§4"),
    "kw": ("Categoría por palabras clave", "El texto del banco contiene términos propios de la categoría.", "alta", "§4"),
    "fallback_receipt": ("Cobro sin partida identificable", "Ninguna partida abierta encaja con garantías: se deja como "
                         "no identificado en vez de forzar un cruce.", "baja", "§3 UNIDENTIFIED"),
    "fallback_debit": ("Cargo sin regla aplicable", "Cargo sin partida ni categoría clara: necesita revisión.", "baja", "§4"),
    "missing": ("Sin propuesta", "El motor no emitió fila para este movimiento.", "baja", "§1"),
    "manual": ("Corregido por tesorería", "Asignación introducida a mano por el contable.", "alta", "—"),
}
PARTY_KIND = {
    "amount": ("Contraparte + importe exacto", "Sin referencia útil: {via} identifica al pagador y el importe coincide "
               "con una partida abierta suya.", "media"),
    "subset": ("Contraparte + combinación de partidas", "Sin referencia útil: {via} identifica al pagador y el importe "
               "cuadra con la suma de varias partidas abiertas suyas.", "media"),
    "short": ("Contraparte + importe con diferencia", "{via} identifica al pagador; el importe se acerca a una partida y "
              "la diferencia se ha clasificado como excepción.", "baja"),
    "fx": ("Contraparte + importe en otra divisa", "{via} identifica al pagador; el importe cuadra tras convertir la "
           "divisa.", "baja"),
}
PARTY_VIA = {"iban": "el IBAN del ordenante", "nombre": "el nombre del ordenante", "nombre_pdf": "el nombre del "
             "ordenante (con datos del PDF)", "nombre_texto": "el nombre que aparece en el texto"}
BATCH_PREFIX = ("dd_batch", "dd_return", "card_settlement", "cheque_deposit", "payment_run")
FILE_RX = re.compile(r"\b([A-Z]{2}\d{2}/[\w.\-]+\.(?:pdf|csv|txt|xlsx))", re.I)
PARTY_RX = re.compile(r"\b([CS][A-Z]{2}\d{2}\d{6})\b")
KIND_ES = {"ARI": "Factura cliente", "ARC": "Abono cliente", "API": "Factura proveedor", "APC": "Abono proveedor"}


def rule_info(rule: str):
    fam = (rule or "missing").split(":")[0]
    if fam in RULES:
        title, why, conf, pol = RULES[fam]
        if fam == "kw" and ":" in rule:
            words = " / ".join(dict.fromkeys(rule.split(":", 1)[1].split("|")))
            why = f"El texto del banco contiene «{words}»."
        return fam, title, why, conf, pol
    m = re.match(r"party_(amount|subset|short|fx)_(.+)$", fam)
    if m:
        title, why, conf = PARTY_KIND[m.group(1)]
        return fam, title, why.format(via=PARTY_VIA.get(m.group(2), m.group(2))), conf, "§5.1, §5.5"
    if fam.startswith("payment_run"):
        return fam, "Fichero de pagos", "Documento de nuestro fichero de pagos.", "alta", "§5.3"
    return fam, fam.replace("_", " ").capitalize(), "Regla del motor.", "media", "—"


def cents(s) -> int:
    if s is None or s == "":
        return 0
    return int(round(float(s) * 100))


def day(s):
    try:
        return dt.date.fromisoformat(s[:10]) if s else None
    except ValueError:
        return None


def read_rows(path):
    p = str(path)
    fh = gzip.open(p, "rt", encoding="utf-8", newline="") if p.endswith(".gz") else open(p, encoding="utf-8-sig",
                                                                                           newline="")
    with fh:
        yield from csv.DictReader(fh)


def norm_num(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def fmt_amounts(c: int) -> str:
    v = abs(c) / 100
    a = f"{v:.2f}"
    es = f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{a} {es} {a.replace('.', ',')}"


class FX:
    def __init__(self, path):
        self.s = defaultdict(list)
        if path.exists():
            for r in read_rows(path):
                self.s[r["currency"]].append((r["date"], float(r["units_per_eur"])))
        for v in self.s.values():
            v.sort()

    def rate(self, ccy, d: dt.date):
        if ccy == "EUR" or ccy not in self.s:
            return 1.0
        from bisect import bisect_right
        arr = self.s[ccy]
        i = bisect_right(arr, (d.isoformat(), float("inf"))) - 1
        return arr[max(i, 0)][1]

    def conv(self, amount, frm, to, d):
        if frm == to or not d:
            return amount
        return amount / self.rate(frm, d) * self.rate(to, d)


# ===================================================================================================== almacén por fase
class Store:
    def __init__(self, tag: str):
        self.tag = tag
        self.dp = PHASE_DIR[tag]
        self.ddir = DATA / self.dp
        self.explain_path = OUT / f"explain_{tag}.csv.gz"
        self.state = "idle"  # idle | loading | ready | error
        self.error = ""
        self.lock = threading.RLock()
        self.export_lock = threading.Lock()
        self.export_timer = None
        self.export_info = {"path": "", "at": None, "rows": 0}
        self._summary_cache = {}

    @property
    def available(self):
        return self.explain_path.exists()

    # ------------------------------------------------------------------ carga
    def ensure(self, block=True):
        with self.lock:
            if self.state in ("ready", "loading"):
                pass
            elif not self.available:
                self.state, self.error = "error", f"No existe {self.explain_path.name}: ejecuta python -m bankrec.run " \
                                                  f"--phase {self.tag}"
            else:
                self.state = "loading"
                th = threading.Thread(target=self._load_safe, daemon=True)
                th.start()
                if block:
                    th.join()
        return self.state

    def _load_safe(self):
        try:
            t0 = time.time()
            self._load()
            self.state = "ready"
            print(f"[ui] fase {self.tag} cargada en {time.time() - t0:.1f}s · {len(self.order)} movimientos", flush=True)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.state, self.error = "error", f"{type(e).__name__}: {e}"

    def _masters(self):
        mdir = DATA / "masters"
        if self.tag == "final" and (DATA / "final" / "masters_final").is_dir():
            mdir = DATA / "final" / "masters_final"
        self.customers = {r["customer_id"]: r for r in read_rows(mdir / "customers.csv")}
        self.suppliers = {r["supplier_id"]: r for r in read_rows(mdir / "suppliers.csv")}
        self.categories = [{"code": r["category"], "desc": r["description"], "gl": r["gl_account"]}
                           for r in read_rows(mdir / "categories.csv")]
        self.cat_codes = {c["code"] for c in self.categories}
        self.terms = {r["code"]: r for r in read_rows(mdir / "payment_terms.csv")}
        self.accounts = {r["account_id"]: r for r in read_rows(mdir / "bank_accounts.csv")}
        self.entities = {r["entity"]: r for r in read_rows(mdir / "entities.csv")}
        self.fx = FX(mdir / "fx_rates.csv")

    def party_name(self, pid):
        r = self.customers.get(pid) or self.suppliers.get(pid)
        return (r or {}).get("name", "")

    def _load(self):
        self._masters()
        # 1. propuesta del motor
        self.engine_rows: dict[str, list[dict]] = OrderedDict()
        for r in read_rows(self.explain_path):
            self.engine_rows.setdefault(r["bank_txn_id"], []).append({
                "item_id": r["item_id"], "amount": cents(r["amount"]) if r["item_id"] else 0,
                "category": r["category"], "exception": r["exception"], "rule": r["rule"], "evidence": r["evidence"]})
        self.explain_mtime = dt.datetime.fromtimestamp(self.explain_path.stat().st_mtime).isoformat(timespec="minutes")
        # 2. movimientos bancarios
        self.txns = {}
        for r in read_rows(self.ddir / "bank_transactions.csv.gz"):
            if r["bank_txn_id"] in self.engine_rows:
                self.txns[r["bank_txn_id"]] = r
        # 3. partidas
        ref_items = {x["item_id"] for rs in self.engine_rows.values() for x in rs if x["item_id"]}
        dates = sorted(day(t["booking_date"]) for t in self.txns.values())
        since = (dates[0] - dt.timedelta(days=430)) if dates else None
        keep_all = self.tag in ("sample", "live", "final")
        self.items = {}

        def keep(r):
            if keep_all or r["item_id"] in ref_items:
                return True
            d = day(r.get("issue_date", ""))
            return bool(d and since and d >= since)

        for fn, kind in (("ar_invoices", "ARI"), ("ap_invoices", "API"), ("credit_notes", None)):
            p = self.ddir / f"{fn}.csv.gz"
            if not p.exists():
                continue
            for r in read_rows(p):
                if not keep(r):
                    continue
                k = kind or ("ARC" if r.get("side") == "AR" else "APC")
                self.items[r["item_id"]] = {
                    "id": r["item_id"], "kind": k, "entity": r["entity"],
                    "number": r.get("document_number") or r.get("supplier_invoice_number", ""),
                    "party": r.get("customer_id") or r.get("supplier_id") or r.get("party_id", ""),
                    "party_name": r.get("customer_name") or r.get("supplier_name") or r.get("party_name", ""),
                    "ccy": r["currency"], "total": cents(r["total_amount"]), "net": cents(r.get("net_amount")),
                    "tax": cents(r.get("tax_amount")), "issue": r.get("issue_date", ""), "due": r.get("due_date", ""),
                    "terms": r.get("payment_terms", ""), "po": r.get("customer_po") or r.get("our_po", ""),
                    "method": r.get("payment_method", ""), "related": r.get("related_document", ""),
                    "reason": r.get("reason", ""), "open_start": None}
        p_open = self.ddir / "open_items_at_start.csv.gz"
        if p_open.exists():
            for r in read_rows(p_open):
                it = self.items.get(r["item_id"])
                if it is None:
                    k = r["item_type"] if r["item_type"] in KIND_ES else r["item_id"][:3]
                    it = self.items[r["item_id"]] = {
                        "id": r["item_id"], "kind": k, "entity": r["entity"], "number": r["document_number"],
                        "party": r["party_id"], "party_name": self.party_name(r["party_id"]), "ccy": r["currency"],
                        "total": cents(r["total_amount"]), "net": 0, "tax": 0, "issue": r["issue_date"],
                        "due": r["due_date"], "terms": "", "po": "", "method": "", "related": "", "reason": ""}
                it["open_start"] = cents(r["open_amount"])
        self.items_by_party = defaultdict(list)
        self.items_by_num = {}
        for it in self.items.values():
            self.items_by_party[it["party"]].append(it["id"])
            self.items_by_num[(it["entity"], norm_num(it["number"]))] = it["id"]
        for v in self.items_by_party.values():
            v.sort(key=lambda i: (self.items[i]["issue"], i))
        self._item_blob = None
        # 4. documentos
        self.pdf_index = {}
        order = ["train", "live", "final"]
        dirs = [self.dp] + ([d for d in order[: order.index(self.dp)]] if self.dp in order else [])
        for d in dirs:
            p = DATA / d / "pdfs" / "index.csv"
            if p.exists():
                for r in read_rows(p):
                    key = (r["entity"], norm_num(r["document_number"]))
                    self.pdf_index.setdefault(key, (d, r["file"], r["document_type"], r.get("document_date", "")))
        self.advice_index = {}
        p = self.ddir / "remittance_advices" / "index.csv"
        if p.exists():
            for r in read_rows(p):
                self.advice_index[r["file"]] = r
        self.pdf_text = {}
        for d in dirs:
            pt = OUT / f"pdf_text_{d}.json"
            if pt.exists():
                try:
                    for k, v in json.loads(pt.read_text(encoding="utf-8")).items():
                        self.pdf_text.setdefault((d, k), v)
                except (OSError, ValueError):
                    pass
        # 5. golden (solo fases con etiquetas: muestra y holdout)
        self.golden = None
        gp = self.ddir / "golden_allocations.csv.gz"
        if gp.exists() and self.tag in ("sample", "holdout"):
            self.golden = defaultdict(list)
            for r in read_rows(gp):
                if r["bank_txn_id"] in self.engine_rows:
                    self.golden[r["bank_txn_id"]].append({
                        "item_id": r["item_id"], "amount": cents(r["amount"]) if r["item_id"] else 0,
                        "category": r["category"], "exception": r["exception"], "segment": r.get("segment", ""),
                        "difficulty": r.get("difficulty", "")})
        # 6. score y overrides
        self.score = None
        sp = OUT / f"score_{self.tag}.json"
        if sp.exists():
            try:
                self.score = json.loads(sp.read_text(encoding="utf-8"))
            except ValueError:
                self.score = None
        self.ov_path = OUT / f"overrides_{self.tag}.json"
        self.overrides = {}
        if self.ov_path.exists():
            try:
                self.overrides = json.loads(self.ov_path.read_text(encoding="utf-8"))
            except ValueError:
                self.overrides = {}
        # 7. resumen por movimiento
        self.order = sorted(self.engine_rows, key=lambda t: (self.txns.get(t, {}).get("booking_date", ""), t))
        self.summ = {}
        self.blob = {}
        for tid in self.order:
            self._summarize(tid)
        self._summary_cache = {}
        # calentar: que la primera petición del contable también vaya por debajo de 100 ms
        self.summary()
        self.search_items({"q": "", "limit": 1})
        try:
            from . import pdfclauses, remittance  # noqa: F401
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ resumen por movimiento
    def rows_of(self, tid):
        ov = self.overrides.get(tid)
        if ov and ov.get("action") == "edit":
            return ov["rows"]
        return self.engine_rows.get(tid, [])

    def golden_ok(self, tid, rows):
        if self.golden is None:
            return None
        g = self.golden.get(tid, [])
        if not g:
            return None

        def key(rs):
            out = {}
            for r in rs:
                k = r["item_id"] or ("CAT", r["category"])
                out[k] = (r["exception"], r["amount"])
            return out

        a, b = key(rows), key(g)
        if set(a) != set(b):
            return False
        for k, (ex, am) in b.items():
            ea, aa = a[k]
            if ea != ex:
                return False
            if isinstance(k, str) and abs(aa - am) > max(1, abs(am) * 0.005):
                return False
        return True

    def _summarize(self, tid):
        t = self.txns.get(tid, {})
        rows = self.rows_of(tid)
        eng = self.engine_rows.get(tid, [])
        items = [r for r in rows if r["item_id"]]
        fam, title, _, conf, _ = rule_info((eng[0]["rule"] if eng else "missing"))
        excs = list(dict.fromkeys(r["exception"] for r in rows if r["exception"]))
        cat = "" if items else (rows[0]["category"] if rows else "UNIDENTIFIED")
        ov = self.overrides.get(tid)
        if ov:
            status = "validado" if ov.get("action") == "confirm" else "corregido"
        elif "UNIDENTIFIED" in excs or conf == "baja" or cat == "UNIDENTIFIED":
            status = "pendiente"
        else:
            status = "auto"
        parties = []
        for r in items:
            it = self.items.get(r["item_id"])
            if it:
                n = self.party_name(it["party"]) or it["party_name"]
                if n and n not in parties:
                    parties.append(n)
        cp = parties[0] if parties else (t.get("counterparty_name") or "")
        amount = cents(t.get("amount"))
        ccy = t.get("currency", "EUR")
        d = day(t.get("booking_date", ""))
        eur = abs(self.fx.conv(amount, ccy, "EUR", d)) if d else abs(amount)
        evid = " ".join(r["evidence"] for r in eng)
        s = {
            "id": tid, "date": t.get("booking_date", ""), "entity": t.get("entity", ""), "account": t.get("account_id", ""),
            "amount": amount, "ccy": ccy, "eur": round(eur), "cp": cp, "text": (t.get("text") or "")[:160],
            "rule": fam, "rule_title": title, "conf": conf, "exc": excs, "cat": cat, "n_items": len(items),
            "status": status, "gold": self.golden_ok(tid, rows),
            "batch": fam.startswith(BATCH_PREFIX), "doc": "remittance" in fam or "pdf" in fam or bool(FILE_RX.search(evid)),
        }
        self.summ[tid] = s
        blob = [tid, t.get("text", ""), t.get("ref1", ""), t.get("ref2", ""), t.get("remittance_info", ""),
                t.get("counterparty_name", ""), t.get("counterparty_account", ""), t.get("end_to_end_id", ""), cp,
                fmt_amounts(amount), evid, " ".join(parties), t.get("account_id", "")]
        for r in items:
            it = self.items.get(r["item_id"])
            blob.append(r["item_id"])
            blob.append(fmt_amounts(r["amount"]))
            if it:
                blob += [it["number"], it["party"], it["po"]]
        self.blob[tid] = " ".join(blob).upper()
        return s

    # ------------------------------------------------------------------ consultas
    def filtered(self, q: dict):
        state = q.get("state", "all")
        ent = q.get("entity", "")
        exc = q.get("exc", "")
        rule = q.get("rule", "")
        conf = q.get("conf", "")
        cat = q.get("cat", "")
        text = (q.get("q") or "").strip().upper()
        toks = text.split()
        num = None
        if len(toks) == 1:
            s = toks[0].replace("€", "")
            if re.fullmatch(r"-?[\d.,]+", s) and re.search(r"\d", s):
                if "," in s and "." in s:
                    s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
                elif "," in s:
                    s = s.replace(",", ".")
                try:
                    num = abs(int(round(float(s) * 100)))
                except ValueError:
                    num = None
        out = []
        for tid in self.order:
            s = self.summ[tid]
            if ent and s["entity"] != ent:
                continue
            if exc and exc not in s["exc"]:
                continue
            if rule and s["rule"] != rule:
                continue
            if conf and s["conf"] != conf:
                continue
            if cat and s["cat"] != cat:
                continue
            if state == "exception" and not s["exc"]:
                continue
            if state == "pending" and s["status"] != "pendiente":
                continue
            if state == "category" and (s["n_items"] or s["cat"] in ("", "UNIDENTIFIED")):
                continue
            if state == "batch" and not s["batch"]:
                continue
            if state == "docs" and not s["doc"]:
                continue
            if state == "reviewed" and s["status"] not in ("validado", "corregido"):
                continue
            if state == "golden_ko" and s["gold"] is not False:
                continue
            if toks:
                if num is not None:
                    if abs(s["amount"]) != num and f"{num / 100:.2f}" not in self.blob[tid]:
                        continue
                elif not all(tk in self.blob[tid] for tk in toks):
                    continue
            out.append(s)
        sort = q.get("sort", "date")
        if sort == "amount":
            out.sort(key=lambda s: -s["eur"])
        elif sort == "-date":
            out.reverse()
        elif sort == "conf":
            rank = {"baja": 0, "media": 1, "alta": 2}
            out.sort(key=lambda s: rank[s["conf"]])
        return out

    def summary(self, entity=""):
        key = entity or "*"
        c = self._summary_cache.get(key)
        if c:
            return c
        ss = [self.summ[t] for t in self.order if not entity or self.summ[t]["entity"] == entity]
        n = len(ss)
        st = Counter(s["status"] for s in ss)
        rules = defaultdict(lambda: [0, 0])
        for s in ss:
            rules[s["rule"]][0] += 1
            rules[s["rule"]][1] += s["eur"]
        exc_txn, exc_rows = Counter(), Counter()
        cat_n = Counter()
        conf = Counter(s["conf"] for s in ss)
        rec_eur = pend_eur = tot_eur = 0
        for s in ss:
            tot_eur += s["eur"]
            for e in s["exc"]:
                exc_txn[e] += 1
            for r in self.rows_of(s["id"]):
                if r["exception"]:
                    exc_rows[r["exception"]] += 1
            if not s["n_items"]:
                cat_n[s["cat"]] += 1
            if s["status"] == "pendiente":
                pend_eur += s["eur"]
            else:
                rec_eur += s["eur"]
        ents = Counter(self.summ[t]["entity"] for t in self.order)
        gold = None
        if self.golden is not None:
            g = Counter(s["gold"] for s in ss)
            gold = {"ok": g[True], "ko": g[False], "n": g[True] + g[False]}
        score = None
        if self.score:
            segs = sorted(self.score.get("segments", {}).items(), key=lambda x: -x[1]["n"] * (1 - x[1]["accuracy"]))
            score = {"score": self.score.get("score"), "metrics": self.score.get("metrics", {}),
                     "segments": [{"segment": k, "n": v["n"], "accuracy": v["accuracy"],
                                   "fails": int(round(v["n"] * (1 - v["accuracy"])))} for k, v in segs[:20]]}
        tabs = {
            "all": n,
            "exception": sum(1 for s in ss if s["exc"]),
            "pending": st["pendiente"],
            "category": sum(1 for s in ss if not s["n_items"] and s["cat"] not in ("", "UNIDENTIFIED")),
            "batch": sum(1 for s in ss if s["batch"]),
            "docs": sum(1 for s in ss if s["doc"]),
            "reviewed": st["validado"] + st["corregido"],
        }
        if self.golden is not None:
            tabs["golden_ko"] = sum(1 for s in ss if s["gold"] is False)
        res = {
            "phase": self.tag, "phase_label": PHASE_LABEL[self.tag], "generated_at": self.explain_mtime, "tabs": tabs,
            "entity": entity, "txns": n, "rows": sum(len(self.rows_of(s["id"])) for s in ss),
            "period": [ss[0]["date"], ss[-1]["date"]] if ss else ["", ""],
            "status": {k: st[k] for k in ("auto", "pendiente", "validado", "corregido")},
            "auto_pct": round(100 * st["auto"] / n, 2) if n else 0,
            "close_pct": round(100 * (n - st["pendiente"]) / n, 2) if n else 0,
            "amount_eur": {"total": tot_eur, "reconciled": rec_eur, "pending": pend_eur},
            "confidence": {k: conf[k] for k in ("alta", "media", "baja")},
            "entities": [{"code": e, "name": self.entities.get(e, {}).get("legal_name", e), "n": ents[e]}
                         for e in sorted(ents)],
            "rules": sorted(({"rule": k, "title": rule_info(k)[1], "conf": rule_info(k)[3], "n": v[0], "eur": v[1]}
                             for k, v in rules.items()), key=lambda x: -x["n"]),
            "exceptions": [{"code": e, "label": EXC_ES[e][0], "help": EXC_ES[e][1], "n_txn": exc_txn[e],
                            "n_rows": exc_rows[e]} for e in EXC],
            "categories": [dict(c, n=cat_n[c["code"]]) for c in self.categories],
            "score": score, "golden": gold,
            "export": self.export_info,
        }
        self._summary_cache[key] = res
        return res

    # ------------------------------------------------------------------ ficha de auditoría
    def item_view(self, iid):
        it = self.items.get(iid)
        if not it:
            return {"id": iid, "kind": iid[:3], "kind_label": KIND_ES.get(iid[:3], iid[:3]), "number": "", "known": False}
        v = {k: x for k, x in it.items() if x not in ("", None) and k not in ("net", "tax", "party_name")}
        if it["party_name"] and it["party_name"] != self.party_name(it["party"]):
            v["party_name"] = it["party_name"]  # nombre impreso en la factura (antes de un renombrado)
        v["kind_label"] = KIND_ES.get(it["kind"], it["kind"])
        v["party_now"] = self.party_name(it["party"])
        v["known"] = True
        tr = self.terms.get(it["terms"])
        if tr:
            v["terms_desc"] = tr.get("description", "")
            v["disc_pct"] = float(tr.get("early_payment_discount_pct") or 0)
            v["disc_days"] = int(float(tr.get("discount_days") or 0))
        return v

    def pdf_for(self, it):
        if not it:
            return None
        hit = self.pdf_index.get((it["entity"], norm_num(it["number"])))
        if not hit:
            return None
        d, f, dtype, ddate = hit
        return self.doc_view(d, "pdfs", f, dtype=dtype, ddate=ddate)

    def doc_view(self, d, sub, f, dtype="", ddate=""):
        path = DATA / d / sub / f
        fmt = path.suffix.lstrip(".").lower()
        v = {"kind": "advice" if sub == "remittance_advices" else "pdf", "file": f, "phase_dir": d, "fmt": fmt,
             "doc_type": dtype, "date": ddate, "exists": path.is_file(),
             "url": f"/api/document/{d}/{sub}/{f}"}
        if sub == "remittance_advices":
            meta = self.advice_index.get(f, {})
            v["payer"] = meta.get("payer_name", "")
            v["date"] = meta.get("received_date", "")
            v["doc_type"] = "aviso de pago"
        if not v["exists"]:
            return v
        if fmt in ("txt", "csv"):
            try:
                v["preview"] = path.read_text(encoding="utf-8", errors="replace")[:8000]
            except OSError:
                v["preview"] = ""
        if sub == "remittance_advices":
            try:
                from . import remittance
                adv = remittance.read_advice(path, fmt)
                v["parsed"] = {"ref": adv.get("ref", ""), "total": adv.get("total"), "ccy": adv.get("ccy", ""),
                               "pay_date": str(adv.get("pay_date") or ""),
                               "lines": [{"doc": ln["doc"], "date": str(ln.get("date") or ""), "gross": ln.get("gross"),
                                          "ded": ln.get("ded"), "reason": ln.get("reason", ""), "paid": ln.get("paid")}
                                         for ln in adv.get("lines", [])][:200]}
            except ImportError as e:
                v["parse_error"] = f"Falta la librería opcional {e.name} para leer este formato"
            except Exception as e:  # noqa: BLE001
                v["parse_error"] = f"{type(e).__name__}: {e}"
        else:
            text = self.pdf_text.get((d, f), "")
            v["text_available"] = bool(text)
            if text:
                v["preview"] = text[:6000]
                try:
                    from .pdfclauses import parse_clauses
                    v["clauses"] = clauses_es(parse_clauses(text, dtype))
                except Exception as e:  # noqa: BLE001
                    v["clauses_error"] = str(e)
        return v

    def detail(self, tid):
        t = self.txns.get(tid)
        if t is None:
            return None
        s = self.summ[tid]
        rows = self.rows_of(tid)
        eng = self.engine_rows.get(tid, [])
        bank_ccy = t["currency"]
        d = day(t["booking_date"])
        amount = cents(t["amount"])
        # por qué
        fams = OrderedDict()
        for r in eng:
            fam, title, why, conf, pol = rule_info(r["rule"])
            fams.setdefault(fam, {"rule": fam, "raw": r["rule"], "title": title, "why": why, "conf": conf, "policy": pol,
                                  "evidence": []})
            ev = humanize_evidence(r["evidence"], self)
            if ev and ev not in fams[fam]["evidence"]:
                fams[fam]["evidence"].append(ev)
        # líneas y cuadre
        lines = []
        tot = 0
        all_credit = True
        for r in rows:
            if not r["item_id"]:
                continue
            it = self.items.get(r["item_id"])
            iv = self.item_view(r["item_id"])
            kind = (it or {}).get("kind", r["item_id"][:3])
            sign = -1 if kind in ("ARC", "APC") else 1
            all_credit &= sign < 0
            ccy = (it or {}).get("ccy") or bank_ccy
            conv = r["amount"] if ccy == bank_ccy else int(round(self.fx.conv(r["amount"], ccy, bank_ccy, d)))
            tot += sign * conv
            remaining = None
            if r["exception"] in ("PARTIAL", "SHORT_DISPUTE") and it:
                base = it["open_start"] if it.get("open_start") is not None else it["total"]
                remaining = base - r["amount"]
            lines.append({"item": iv, "amount": r["amount"], "ccy": ccy, "sign": sign, "conv": conv,
                          "exception": r["exception"], "exc_label": EXC_ES.get(r["exception"], ("", ""))[0],
                          "remaining": remaining, "pdf": self.pdf_for(it)})
        if lines and all_credit:
            tot = -tot
        recon = reconcile(self, lines, rows, abs(amount), bank_ccy, tot, d) if lines else None
        # documentos citados en la evidencia (avisos de pago, PDF)
        docs, seen = [], set()
        for r in eng + rows:
            for f in FILE_RX.findall(r.get("evidence", "") or ""):
                if f in seen:
                    continue
                seen.add(f)
                sub = "remittance_advices" if Path(f).name.upper().startswith("RA_") else "pdfs"
                docs.append(self.doc_view(self.dp, sub, f))
        for ln in lines:
            if ln["pdf"] and ln["pdf"]["file"] not in seen:
                seen.add(ln["pdf"]["file"])
                docs.append(dict(ln["pdf"], about=ln["item"].get("number", "")))
        acct = self.accounts.get(t["account_id"], {})
        bank = {k: t.get(k, "") for k in ("bank_txn_id", "account_id", "entity", "bank", "statement_format",
                                          "statement_file", "booking_date", "value_date", "amount", "currency",
                                          "bank_code", "bank_subcode", "ref1", "ref2", "text", "counterparty_name",
                                          "counterparty_account", "end_to_end_id", "remittance_info", "orig_amount",
                                          "orig_currency")}
        bank["bank_name"] = acct.get("bank_name", "")
        bank["iban"] = acct.get("iban", "")
        bank["entity_name"] = self.entities.get(t["entity"], {}).get("legal_name", "")
        cat_rows = [dict(r, cat_desc=next((c["desc"] for c in self.categories if c["code"] == r["category"]), ""),
                         gl=next((c["gl"] for c in self.categories if c["code"] == r["category"]), ""))
                    for r in rows if not r["item_id"]]
        gold = None
        if self.golden is not None:
            g = self.golden.get(tid, [])
            gold = {"ok": s["gold"], "rows": [dict(x, number=(self.items.get(x["item_id"]) or {}).get("number", ""))
                                              for x in g]}
        return {
            "summary": s, "bank": bank, "why": list(fams.values()), "lines": lines, "category_rows": cat_rows,
            "recon": recon, "docs": docs[:16], "docs_total": len(docs), "engine_rows": eng,
            "override": self.overrides.get(tid),
            "golden": gold, "exc_help": {e: EXC_ES[e] for e in s["exc"] if e in EXC_ES},
        }

    # ------------------------------------------------------------------ partidas (reasignar)
    def search_items(self, q: dict):
        text = (q.get("q") or "").strip().upper()
        party = q.get("party", "")
        ent = q.get("entity", "")
        lim = min(int(q.get("limit", 30) or 30), 100)
        if self._item_blob is None:
            self._item_blob = {i: " ".join([i, it["number"], it["party"], it["party_name"], it["po"],
                                            fmt_amounts(it["total"])]).upper() for i, it in self.items.items()}
        src = self.items_by_party.get(party, []) if party else self.items.keys()
        toks = text.split()
        out = []
        for iid in src:
            it = self.items[iid]
            if ent and it["entity"] != ent:
                continue
            if toks and not all(tk in self._item_blob[iid] for tk in toks):
                continue
            out.append(self.item_view(iid))
            if len(out) >= lim:
                break
        return out

    # ------------------------------------------------------------------ overrides y re-exportación
    def apply_override(self, body: dict):
        tid = body.get("txn_id", "")
        action = body.get("action", "")
        if tid not in self.engine_rows:
            raise ValueError("Movimiento desconocido")
        if action not in ("confirm", "edit", "reset"):
            raise ValueError("Acción no válida")
        note = str(body.get("note", ""))[:500]
        with self.lock:
            if action == "reset":
                self.overrides.pop(tid, None)
            elif action == "confirm":
                self.overrides[tid] = {"action": "confirm", "rows": self.engine_rows[tid], "note": note,
                                       "at": dt.datetime.now().isoformat(timespec="seconds")}
            else:
                rows = self.validate_rows(body.get("rows") or [])
                self.overrides[tid] = {"action": "edit", "rows": rows, "note": note,
                                       "at": dt.datetime.now().isoformat(timespec="seconds"),
                                       "engine_rows": self.engine_rows[tid]}
            tmp = self.ov_path.with_suffix(".json.tmp")
            OUT.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(self.overrides, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self.ov_path)
            s = self._summarize(tid)
            self._summary_cache = {}
        self.schedule_export()
        return s

    def validate_rows(self, rows):
        if not rows:
            raise ValueError("Hace falta al menos una fila")
        out = []
        with_item = [r for r in rows if (r.get("item_id") or "").strip()]
        if with_item and len(with_item) != len(rows):
            raise ValueError("Un movimiento liquida partidas o lleva una categoría, no ambas (POLICIES §1)")
        if not with_item and len(rows) != 1:
            raise ValueError("Sin partidas va exactamente una fila con categoría (POLICIES §1)")
        seen = set()
        for r in rows:
            exc = (r.get("exception") or "").strip()
            if exc and exc not in EXC:
                raise ValueError(f"Excepción no válida: {exc}")
            iid = (r.get("item_id") or "").strip()
            if iid:
                if iid not in self.items:
                    raise ValueError(f"Partida desconocida: {iid}")
                if iid in seen:
                    raise ValueError(f"Partida repetida: {iid}")
                seen.add(iid)
                try:
                    amt = int(round(float(str(r.get("amount", "")).replace(",", ".")) * 100))
                except ValueError:
                    raise ValueError(f"Importe no válido en {iid}") from None
                if amt <= 0:
                    raise ValueError(f"El importe de {iid} debe ser positivo (POLICIES §1)")
                out.append({"item_id": iid, "amount": amt, "category": "", "exception": exc, "rule": "manual",
                            "evidence": "corrección de tesorería"})
            else:
                cat = (r.get("category") or "").strip()
                if cat not in self.cat_codes:
                    raise ValueError(f"Categoría no válida: {cat}")
                if cat == "UNIDENTIFIED":
                    exc = "UNIDENTIFIED"
                elif exc == "UNIDENTIFIED":
                    raise ValueError("UNIDENTIFIED va solo con la categoría UNIDENTIFIED (POLICIES §3)")
                out.append({"item_id": "", "amount": 0, "category": cat, "exception": exc, "rule": "manual",
                            "evidence": "corrección de tesorería"})
        return out

    def schedule_export(self):
        with self.export_lock:
            if self.export_timer:
                self.export_timer.cancel()
            self.export_timer = threading.Timer(0.8, self.export)
            self.export_timer.daemon = True
            self.export_timer.start()
        self.export_info = dict(self.export_info, pending=True)

    def export(self):
        sub = OUT / f"sub_{self.tag}_revisado.csv.gz"
        exp = OUT / f"explain_{self.tag}_revisado.csv.gz"
        with self.lock:
            snapshot = [(tid, list(self.rows_of(tid)), tid in self.overrides) for tid in self.engine_rows]
        n = 0
        for path, explain in ((sub, False), (exp, True)):
            tmp = path.with_suffix(".tmp")
            with gzip.open(tmp, "wt", encoding="utf-8", newline="") as fh:
                w = csv.writer(fh, lineterminator="\n")
                w.writerow(["bank_txn_id", "item_id", "amount", "category", "exception"]
                           + (["rule", "evidence"] if explain else []))
                for tid, rows, manual in snapshot:
                    for r in rows:
                        amt = "" if not r["item_id"] else f"{max(r['amount'], 0) / 100:.2f}"
                        base = [tid, r["item_id"], amt, "" if r["item_id"] and not explain else r["category"],
                                r["exception"]]
                        if explain:
                            base += [("manual:" if manual else "") + r["rule"], r["evidence"]]
                        w.writerow(base)
                        n += 0 if explain else 1
            os.replace(tmp, path)
        self.export_info = {"path": str(sub), "at": dt.datetime.now().isoformat(timespec="seconds"), "rows": n,
                            "pending": False, "overrides": len(self.overrides)}
        self._summary_cache = {}
        print(f"[ui] entrega revisada -> {sub} ({n} filas, {len(self.overrides)} correcciones)", flush=True)
        return sub


def humanize_evidence(ev: str, st: Store) -> str:
    if not ev:
        return ""

    def rep(m):
        n = st.party_name(m.group(1))
        return f"{m.group(1)} ({n})" if n else m.group(1)

    ev = PARTY_RX.sub(rep, ev)
    ev = re.sub(r"^refs? ", "referencia ", ev)
    ev = ev.replace("e2e ", "EndToEndId ")
    return ev


def clauses_es(c: dict) -> list[dict]:
    out = []

    def eur(x):
        return f"{x / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if isinstance(x, int) else str(x)

    for k, v in c.items():
        if k == "plan":
            out.append({"k": "Pago en vencimientos", "v": " / ".join(eur(a) for a in v) + (
                f" a {'/'.join(str(x) for x in c.get('plan_days', []))} días" if c.get("plan_days") else ""),
                "policy": "§5.8 → PARTIAL salvo el último"})
        elif k == "ret":
            out.append({"k": "Retención en garantía", "v": f"{c.get('ret_pct', '')} % · {eur(v)}"
                        + (f" · libera {c['ret_release']}" if c.get("ret_release") else ""),
                        "policy": "§5.8 → primer cobro PARTIAL"})
        elif k == "net_due":
            out.append({"k": "Importe a pagar al vencimiento", "v": eur(v), "policy": ""})
        elif k in ("advance", "adv"):
            out.append({"k": "Anticipo a deducir", "v": str(v) if not isinstance(v, int) else eur(v),
                        "policy": "§4 CUSTOMER_ADVANCE / §3 PARTIAL"})
        elif k == "replaces":
            out.append({"k": "Anula y sustituye a", "v": str(v), "policy": "§5.7"})
        elif k == "month":
            out.append({"k": "Mes de servicio", "v": str(v), "policy": "§5.6"})
        elif k == "factor":
            out.append({"k": "Crédito cedido a", "v": str(v), "policy": "§5.1"})
        elif k == "new_iban":
            out.append({"k": "IBAN nuevo", "v": str(v), "policy": "§5.11"})
        elif k == "cn_mode":
            out.append({"k": "Abono", "v": "se compensa" if v == "offset" else "se reembolsa", "policy": "§5.9"})
        elif k not in ("plan_days", "ret_pct", "ret_release"):
            out.append({"k": k, "v": v if isinstance(v, (str, int, float)) else json.dumps(v, default=str),
                        "policy": ""})
    return out


def reconcile(st: Store, lines, rows, bank, bank_ccy, tot, d):
    """Cuadre al céntimo: Σ partidas (en divisa del banco) − deducciones = extracto."""
    diff = tot - bank
    excs = [r["exception"] for r in rows if r["exception"]]
    ex = set(excs)
    res = {"documents": tot, "bank": bank, "ccy": bank_ccy, "diff": diff, "status": "ok", "label": "Cuadra al céntimo",
           "detail": "", "steps": []}
    sum_inv = sum(ln["conv"] for ln in lines if ln["sign"] > 0)
    sum_cn = sum(ln["conv"] for ln in lines if ln["sign"] < 0)
    res["steps"].append({"k": "Facturas", "v": sum_inv})
    if sum_cn:
        res["steps"].append({"k": "Abonos compensados", "v": -sum_cn})
    if abs(diff) <= 1:
        if ex & {"PARTIAL", "SHORT_DISPUTE"}:
            open_left = sum(ln["remaining"] or 0 for ln in lines)
            res["detail"] = f"Se aplica lo cobrado; queda abierto {open_left / 100:,.2f} en las partidas."
        if diff:
            res["steps"].append({"k": "Redondeo", "v": -diff})
        return res
    pct = 100 * diff / tot if tot else 0
    res["pct"] = round(pct, 3)
    card = any(r.get("rule", "").startswith("card_settlement") for r in rows)
    if diff > 0:
        label = None
        if card:
            ok = pct <= 5
            label = "Comisión del adquirente"
            detail = f"La liquidación llega neta ({pct:.2f} % de comisión): los pedidos se liquidan al bruto y la " \
                     f"comisión es contractual, no es excepción (POLICIES §4)."
        elif "FX_DIFFERENCE" in ex:
            ok = abs(pct) <= 3
            label, detail = "Diferencia de cambio", f"Spread del banco {pct:.2f} % sobre el tipo BCE (tolerancia 3 %)."
        elif "SHORT_DISCOUNT" in ex:
            exp = 0
            for ln in lines:
                it = ln["item"]
                if ln["exception"] == "SHORT_DISCOUNT" and it.get("disc_pct"):
                    exp += int(round(it["total"] * it["disc_pct"] / 100))
            ok = exp == 0 or abs(exp - diff) <= 2
            label = "Descuento pronto pago"
            detail = (f"Esperado según condiciones de pago: {exp / 100:,.2f}. " if exp else "") + \
                     f"Deducido: {diff / 100:,.2f} ({pct:.2f} % del total)."
        elif "SHORT_WITHHOLDING" in ex:
            ok = any(abs(pct - r) < 0.15 for r in (1.25, 1.5, 10))
            label, detail = "Retención fiscal", f"Retenido {pct:.2f} % (tipos válidos: 1,25 %, 1,5 %, 10 %)."
        elif "SHORT_BANK_CHARGES" in ex:
            eur = st.fx.conv(diff, bank_ccy, "EUR", d)
            ok = eur <= 6000 and pct <= 2
            label, detail = "Comisión del banco intermediario", f"{eur / 100:,.2f} EUR ({pct:.2f} %) · límite 60 EUR y 2 %."
        elif ex & {"PARTIAL", "SHORT_DISPUTE"}:
            ok, label, detail = False, "Aplicado mayor que lo cobrado", "Revisa importes aplicados frente al extracto."
        if label is None:
            ok, label, detail = False, "Descuadre sin explicar", "La diferencia no la cubre ninguna excepción."
    else:
        if "OVERPAYMENT" in ex:
            ok, label = True, "Sobrepago"
            detail = f"El cliente pagó {-diff / 100:,.2f} de más; queda a su favor en su cuenta (sin fila extra)."
        elif "DUPLICATE_PAYMENT" in ex or "RETURNED" in ex:
            ok, label, detail = abs(pct) < 1, EXC_ES[sorted(ex & {"DUPLICATE_PAYMENT", "RETURNED"})[0]][0], ""
        else:
            ok, label, detail = False, "Cobrado más que lo aplicado", "El extracto supera a las partidas sin excepción."
    res["steps"].append({"k": label, "v": -diff})
    res.update(status="explicado" if ok else "revisar", label=label, detail=detail)
    return res


# ===================================================================================================== servidor HTTP
STORES = {tag: Store(tag) for tag in PHASES}
DEFAULT = {"phase": "sample"}


class Handler(BaseHTTPRequestHandler):
    server_version = "bankrec-ui/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # silencioso salvo errores
        pass

    # -------------------------------------------------------------- utilidades
    def _send(self, code, body: bytes, ctype="application/json; charset=utf-8", extra=None):
        gz = len(body) > 2048 and "gzip" in (self.headers.get("Accept-Encoding") or "") and (
            ctype.startswith(("application/json", "text/")))
        if gz:
            body = gzip.compress(body, compresslevel=5)
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Elapsed-ms", f"{(time.perf_counter() - self._t0) * 1000:.1f}")
        if gz:
            self.send_header("Content-Encoding", "gzip")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8"))

    def _store(self, q):
        tag = q.get("phase") or DEFAULT["phase"]
        if tag not in STORES:
            self._json({"error": f"Fase no válida: {tag}"}, 400)
            return None
        st = STORES[tag]
        state = st.ensure(block=tag == DEFAULT["phase"])
        if state == "loading":
            self._json({"loading": True, "phase": tag}, 202)
            return None
        if state == "error":
            self._json({"error": st.error, "phase": tag}, 404)
            return None
        return st

    # -------------------------------------------------------------- GET
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        self._t0 = time.perf_counter()
        try:
            u = urlparse(self.path)
            q = {k: v[-1] for k, v in parse_qs(u.query).items()}
            path = unquote(u.path)
            if path in ("/", "/index.html"):
                return self._static("index.html")
            if path.startswith("/static/"):
                return self._static(path[len("/static/"):])
            if path == "/api/phases":
                return self._json({"default": DEFAULT["phase"], "phases": [
                    {"tag": t, "label": PHASE_LABEL[t], "available": STORES[t].available, "state": STORES[t].state}
                    for t in PHASES]})
            if path.startswith("/api/document/"):
                return self._document(path[len("/api/document/"):])
            st = self._store(q)
            if st is None:
                return
            if path == "/api/summary":
                res = dict(st.summary(q.get("entity", "")))
                res["phases"] = [{"tag": t, "label": PHASE_LABEL[t], "available": STORES[t].available,
                                  "state": STORES[t].state} for t in PHASES]
                return self._json(res)
            if path == "/api/transactions":
                rows = st.filtered(q)
                off = max(int(q.get("offset", 0) or 0), 0)
                lim = min(max(int(q.get("limit", 100) or 100), 1), 500)
                return self._json({"total": len(rows), "offset": off, "rows": rows[off:off + lim],
                                   "eur": sum(r["eur"] for r in rows)})
            if path.startswith("/api/transaction/"):
                d = st.detail(path[len("/api/transaction/"):])
                return self._json(d) if d else self._json({"error": "Movimiento no encontrado"}, 404)
            if path == "/api/items":
                return self._json({"items": st.search_items(q)})
            if path == "/api/categories":
                return self._json({"categories": st.categories, "exceptions": [
                    {"code": e, "label": EXC_ES[e][0], "help": EXC_ES[e][1]} for e in EXC]})
            if path == "/api/export":
                p = OUT / f"sub_{st.tag}_revisado.csv.gz"
                if not p.exists() or st.export_info.get("pending"):
                    p = st.export()
                return self._send(200, p.read_bytes(), "application/gzip",
                                  {"Content-Disposition": f'attachment; filename="{p.name}"'})
            return self._json({"error": "No encontrado"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def _static(self, name):
        p = (STATIC / name).resolve()
        if not p.is_relative_to(STATIC.resolve()) or not p.is_file():
            return self._json({"error": "No encontrado"}, 404)
        ctype = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}.get(p.suffix, "application/octet-stream")
        self._send(200, p.read_bytes(), ctype)

    def _document(self, rel):
        parts = rel.split("/")
        if len(parts) < 3 or parts[0] not in PHASE_DIR.values() or parts[1] not in ("pdfs", "remittance_advices"):
            return self._json({"error": "Ruta de documento no válida"}, 400)
        base = (DATA / parts[0] / parts[1]).resolve()
        p = (base / "/".join(parts[2:])).resolve()
        if not p.is_relative_to(base) or not p.is_file():
            return self._json({"error": "Documento no encontrado"}, 404)
        ctype = {".pdf": "application/pdf", ".csv": "text/plain; charset=utf-8", ".txt": "text/plain; charset=utf-8",
                 ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}.get(
            p.suffix.lower(), "application/octet-stream")
        disp = "attachment" if p.suffix.lower() == ".xlsx" else "inline"
        self._send(200, p.read_bytes(), ctype, {"Content-Disposition": f'{disp}; filename="{p.name}"'})

    # -------------------------------------------------------------- POST
    def do_POST(self):
        self._t0 = time.perf_counter()
        try:
            u = urlparse(self.path)
            q = {k: v[-1] for k, v in parse_qs(u.query).items()}
            n = int(self.headers.get("Content-Length") or 0)
            if n > 1_000_000:
                return self._json({"error": "Petición demasiado grande"}, 413)
            body = json.loads(self.rfile.read(n) or b"{}")
            if u.path != "/api/override":
                return self._json({"error": "No encontrado"}, 404)
            q.setdefault("phase", body.get("phase", ""))
            st = self._store({k: v for k, v in q.items() if v})
            if st is None:
                return
            try:
                s = st.apply_override(body)
            except ValueError as e:
                return self._json({"ok": False, "error": str(e)}, 422)
            return self._json({"ok": True, "summary": s, "override": st.overrides.get(body.get("txn_id")),
                               "export": {"path": str(OUT / f"sub_{st.tag}_revisado.csv.gz"), "pending": True}})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except json.JSONDecodeError:
            self._json({"error": "JSON no válido"}, 400)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Mesa de conciliación (UI local)")
    ap.add_argument("--phase", default="sample", choices=PHASES)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1", help="por defecto solo esta máquina")
    a = ap.parse_args(argv)
    DEFAULT["phase"] = a.phase
    print(f"[ui] datos: {DATA}\n[ui] salidas: {OUT}", flush=True)
    st = STORES[a.phase]
    t0 = time.time()
    if st.ensure(block=True) != "ready":
        print(f"[ui] aviso: {st.error}", flush=True)
    else:
        print(f"[ui] listo en {time.time() - t0:.1f}s", flush=True)
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    srv.daemon_threads = True
    print(f"[ui] http://{a.host}:{a.port}/  (Ctrl+C para salir)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
