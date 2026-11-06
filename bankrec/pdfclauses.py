"""Plugin: cláusulas que solo están en los PDF (POLICIES §3 PARTIAL y §5.6-§5.11).

prepare(engine): extrae el texto de los PDF de la fase (y de las fases previas, solo para partidas abiertas al
inicio), detecta cláusulas por documento y las guarda en engine.clauses[item_id].
match(engine, t): cobros de facturas con plazos, retención en garantía o anticipo a deducir; mes de servicio.

Detecta (todo por texto, sin ids ni nombres):
  plan      "Forma de pago: N vencimientos a 90/120 días (a / b EUR)", "N instalments at ..."
  retention "Retención en garantía del 10 % (X EUR) ... Importe a pagar al vencimiento: Y"
  advance   "A deducir anticipo recibido según proforma PF.. de X EUR. Importe neto a pagar Y"
  replaces  "anula y sustituye a la factura X" / "cancels and replaces invoice X"
  month     "Cuota de mantenimiento ... correspondiente a mayo 2025" / "month of May 2025"
  factor    "El crédito ... ha sido cedido a <entidad>"
  new_iban  aviso de IBAN nuevo (facturas de proveedor y bank_account_notice)
  cn_mode   abono: 'offset' (se compensará) o 'refund' (se reembolsará / pagado por transferencia)
"""
from __future__ import annotations

import csv
import json
import os
import re
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from .data import ascii_upper, num_variants

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get("BANKREC_OUT", ROOT / "out"))
PHASES = ["train", "live", "final"]
KIND_BY_DOC = {"invoice": {"ARI"}, "supplier_invoice": {"API"}, "credit_note": {"ARC"}, "supplier_credit_note": {"APC"}}

MONTHS = {}
for _m, _names in enumerate([
        "ENE ENERO JAN JANUARY JANUAR JANVIER JANEIRO", "FEB FEBRERO FEBRUARY FEBRUAR FEVRIER FEVEREIRO",
        "MAR MARZO MARCH MAERZ MARZ MARS MARCO", "ABR ABRIL APR APRIL AVRIL",
        "MAY MAYO MAI MAIO", "JUN JUNIO JUNE JUNI JUIN JUNHO", "JUL JULIO JULY JULI JUILLET JULHO",
        "AGO AGOSTO AUG AUGUST AOUT", "SEP SEPT SEPTIEMBRE SETIEMBRE SEPTEMBER SEPTEMBRE SETEMBRO",
        "OCT OCTUBRE OCTOBER OKT OKTOBER OCTOBRE OUTUBRO", "NOV NOVIEMBRE NOVEMBER NOVEMBRE NOVEMBRO",
        "DIC DICIEMBRE DEC DECEMBER DEZ DEZEMBER DECEMBRE DEZEMBRO"], start=1):
    for _n in _names.split():
        MONTHS[_n] = _m
MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))

AMT = r"(\d{1,3}(?:[\.  ]\d{3})*(?:,\d{1,2})?|\d+(?:\.\d{1,2})?|\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?)"


def parse_amt(s: str):
    """'5.871,50' / '5,511.15' / '5 206,73' -> céntimos."""
    s = re.sub(r"[  ]", "", s.strip())
    if not s or not re.search(r"\d", s):
        return None
    if "," in s and "." in s:
        dec = "," if s.rfind(",") > s.rfind(".") else "."
        thou = "." if dec == "," else ","
        s = s.replace(thou, "").replace(dec, ".")
    elif "," in s:
        head, tail = s.rsplit(",", 1)
        s = head.replace(",", "") + "." + tail if len(tail) <= 2 else s.replace(",", "")
    elif "." in s:
        head, tail = s.rsplit(".", 1)
        s = head.replace(".", "") + "." + tail if len(tail) <= 2 else s.replace(".", "")
    try:
        return int(round(float(s) * 100))
    except ValueError:
        return None


# ------------------------------------------------------------------ extracción de texto
def _extract(path):
    try:
        from pypdf import PdfReader
        return "\n".join((p.extract_text() or "") for p in PdfReader(path).pages)
    except Exception:
        return ""


def _extract_many(paths):
    if len(paths) < 400:
        return [_extract(p) for p in paths]
    with ProcessPoolExecutor() as ex:
        return list(ex.map(_extract, paths, chunksize=64))


def _load_texts(pdir: Path, phase: str, wanted: list[str]):
    """Devuelve {file: texto} para los ficheros pedidos, con caché en out/pdf_text_<fase>.json."""
    cache_path = OUT / f"pdf_text_{phase}.json"
    cache = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    missing = [f for f in wanted if f not in cache]
    if missing:
        texts = _extract_many([str(pdir / f) for f in missing])
        for f, tx in zip(missing, texts):
            cache[f] = tx
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    return {f: cache.get(f, "") for f in wanted}


# ------------------------------------------------------------------ detección de cláusulas
def parse_clauses(raw: str, doc_type: str) -> dict:
    tx = re.sub(r"\s+", " ", ascii_upper(raw))
    c = {}
    # plazos: "3 vencimientos a 90/120/150 dias (5.871,50 / 5.871,50 / 5.871,50 EUR)"
    m = re.search(r"(\d+)\s+(?:VENCIMIENTOS|INSTALMENTS?|INSTALLMENTS?|RATEN|ECHEANCES|PRESTACOES|PLAZOS|PRESTACIONES)"
                  r"\s+(?:A|AT|ZU|AUX|EM)?\s*([\d/]+)\s*(?:DIAS|DAYS|TAGE|JOURS)?\s*\(([^)]*)\)", tx)
    if m:
        amts = [parse_amt(x) for x in re.split(r"/", re.sub(r"\s*[A-Z]{3}\s*$", "", m.group(3).strip()))]
        days = [int(x) for x in m.group(2).split("/") if x.isdigit()]
        if amts and all(a for a in amts) and len(amts) == int(m.group(1)):
            c["plan"] = amts
            c["plan_days"] = days if len(days) == len(amts) else []
    # retención en garantía
    m = re.search(r"(?:RETENCION|RETENTION|EINBEHALT|RETENUE|RETENCAO)[^()]{0,60}?(\d+(?:[.,]\d+)?)\s*%\s*\(" + AMT
                  + r"\s*[A-Z]{3}\)", tx)
    if m:
        c["ret_pct"] = float(m.group(1).replace(",", "."))
        c["ret"] = parse_amt(m.group(2))
        m2 = re.search(r"(?:IMPORTE A PAGAR AL VENCIMIENTO|AMOUNT PAYABLE AT DUE DATE|ZAHLBETRAG|MONTANT A PAYER)"
                       r"\s*:?\s*" + AMT, tx)
        if m2:
            c["net_due"] = parse_amt(m2.group(1))
        m3 = re.search(r"\((\d{2}/\d{2}/\d{4})\)", tx[m.end():m.end() + 120])
        if m3:
            c["ret_release"] = m3.group(1)
    # anticipo de proforma
    m = re.search(r"(?:A DEDUCIR ANTICIPO|LESS ADVANCE|ANTICIPO RECIBIDO|ADVANCE RECEIVED|ABZUGLICH ANZAHLUNG|"
                  r"ACOMPTE DEDUIT)[^.]*?(PF[\dA-Z\-]+)[^\d]{0,10}" + AMT + r"\s*[A-Z]{3}", tx)
    if m:
        c["adv_pf"] = m.group(1)
        c["adv"] = parse_amt(m.group(2))
        m2 = re.search(r"(?:IMPORTE NETO A PAGAR|NET AMOUNT DUE|NETTOBETRAG ZU ZAHLEN|MONTANT NET A PAYER)\s*:?\s*" + AMT, tx)
        if m2:
            c["net_adv"] = parse_amt(m2.group(1))
    # anula y sustituye
    m = re.search(r"(?:ANULA Y SUSTITUYE A? ?LA FACTURA|ANULA Y SUSTITUYE|CANCELS AND REPLACES INVOICE|"
                  r"CANCELS AND REPLACES|ANNULE ET REMPLACE LA FACTURE|ANNULE ET REMPLACE|ERSETZT RECHNUNG|"
                  r"CANCELA E SUBSTITUI A FATURA)\s+([A-Z0-9][A-Z0-9\-/\.]*\d[A-Z0-9\-/\.]*)", tx)
    if m:
        c["replaces"] = m.group(1).strip(".-/")
    # mes de servicio
    m = re.search(r"(?:CORRESPONDIENTE A|MONTH OF|MONAT|MOIS DE|MES DE|MES DO|MES)\s+(" + MONTH_RE + r")\s+(?:DE\s+)?(\d{4})", tx)
    if m and doc_type in ("invoice", "supplier_invoice"):
        c["month"] = (int(m.group(2)), MONTHS[m.group(1)])
    # cesión a factoring
    m = re.search(r"(?:HA SIDO CEDIDO A|HAS BEEN ASSIGNED TO|CEDIDO A|ABGETRETEN AN|CEDEE A|CEDIDO PARA)\s+(.+?)\.\s", tx + " ")
    if m:
        c["factor"] = m.group(1).strip()
    # IBAN nuevo
    m = re.search(r"(?:NUEVO IBAN|NEW IBAN|NOUVEL IBAN|NEUE IBAN)\s*:?\s*([A-Z]{2}\d{2}[A-Z0-9]{10,30})", tx)
    if m:
        c["new_iban"] = m.group(1)
    elif doc_type == "bank_account_notice":
        m = re.search(r"A PARTIR DEL (\d{2}/\d{2}/\d{4}).*?CUENTA ([A-Z]{2}\d{2}[A-Z0-9]{10,30})", tx)
        if m:
            c["new_iban"] = m.group(2)
            c["iban_from"] = m.group(1)
    # abonos: compensado en el próximo pago o reembolsado por transferencia
    if doc_type in ("credit_note", "supplier_credit_note"):
        if re.search(r"REEMBOLS|REFUNDED|REMBOURS|ERSTATTET|BANK TRANSFER|TRANSFERENCIA BANCARIA|PAID OUT|ABONADO MEDIANTE", tx):
            c["cn_mode"] = "refund"
        elif re.search(r"COMPENSAR|COMPENSAD|DEDUCTED FROM|DEDUCIR|OFFSET|DEDUITE|VERRECHNET", tx):
            c["cn_mode"] = "offset"
        m = re.search(r"(?:ANULACION TOTAL DE LA FACTURA|CANCELLING OF INVOICE N.?|CANCELLATION OF INVOICE)\s*([A-Z0-9][A-Z0-9\-/\.]*\d)", tx)
        if m:
            c["cancels"] = m.group(1)
    return c


# ------------------------------------------------------------------ prepare
def prepare(engine):
    ph = engine.ph
    engine.clauses = {}
    engine.clause_by_party_iban = {}
    phase_dirs = [(ph.name, ph.dir, False)]
    if ph.name in PHASES:
        for p in PHASES[:PHASES.index(ph.name)]:
            d = ph.dir.parent / p
            if (d / "pdfs" / "index.csv").exists():
                phase_dirs.append((p, d, True))
    replaced = []  # (item_X, item_replacement)
    for name, d, previous in phase_dirs:
        idx = d / "pdfs" / "index.csv"
        if not idx.exists():
            continue
        with open(idx, encoding="utf-8-sig", newline="") as fh:
            docs = list(csv.DictReader(fh))
        # fases previas: solo documentos cuya partida sigue abierta al inicio
        linked = {}
        for r in docs:
            kinds = KIND_BY_DOC.get(r["document_type"])
            if kinds is None and r["document_type"] not in ("bank_account_notice", "proforma"):
                continue
            if r["document_type"] in ("bank_account_notice", "proforma"):
                if previous:
                    continue
                linked[r["file"]] = (r, None)
                continue
            it = engine.item_by_number(r["document_number"], r["entity"], party=r["party_id"] or None, kinds=kinds)
            if it is None or (previous and it.open <= 0):
                continue
            linked[r["file"]] = (r, it)
        if not linked:
            continue
        texts = _load_texts(d / "pdfs", name, list(linked))
        for f, (r, it) in linked.items():
            c = parse_clauses(texts.get(f, ""), r["document_type"])
            if not c:
                continue
            if r["document_type"] == "bank_account_notice":
                if c.get("new_iban") and r["party_id"]:
                    ph.party_by_iban.setdefault(c["new_iban"], r["party_id"])
                continue
            c["doc"] = r["document_type"]
            if c.get("new_iban") and r["document_type"] == "supplier_invoice" and r["party_id"]:
                ph.party_by_iban.setdefault(c["new_iban"], r["party_id"])  # §5.11 proveedor con IBAN nuevo
            if c.get("factor"):
                c["factor"] = c["factor"]
            engine.clauses[it.id] = c
            if c.get("replaces") and it.kind in ("ARI", "API"):
                x = engine.item_by_number(c["replaces"], r["entity"], party=r["party_id"] or None, kinds={it.kind})
                if x is not None and x.id != it.id:
                    replaced.append((x, it))
    # (a) §5.7 anula y sustituye: un pago que cita X liquida la factura nueva
    for x, new in replaced:
        for v in num_variants(x.number):
            s = ph.by_num.get(v)
            if s is not None:
                s.discard(x.id)
                s.add(new.id)
        engine.clauses.setdefault(x.id, {})["replaced_by"] = new.id


# ------------------------------------------------------------------ match
TOL = 2


def _near(a, b, tol=TOL):
    return abs(a - b) <= tol


def _fit(engine, it, c, cash, t):
    """¿El importe del cobro cuadra con alguna cláusula de la factura? -> (céntimos, excepción, etiqueta)."""
    if it.ccy != t.ccy:
        return None
    # liberación de la retención: el resto cobrado del todo, sin excepción (§3)
    if c.get("ret") and _near(cash, c["ret"]) and not _near(cash, it.total):
        return cash, "", "retention_release"
    # primer cobro con retención: queda saldo => PARTIAL (§3)
    if c.get("net_due") and _near(cash, c["net_due"]):
        return cash, "PARTIAL", "retention"
    # anticipo de proforma a deducir (§3)
    if c.get("net_adv") and _near(cash, c["net_adv"]):
        return cash, "PARTIAL", "advance_deduction"
    if c.get("adv") and not c.get("net_adv") and _near(cash, it.total - c["adv"]):
        return cash, "PARTIAL", "advance_deduction"
    plan = c.get("plan")
    if plan:
        hit = [k for k, a in enumerate(plan) if _near(cash, a)]
        if hit:
            n = len(plan)
            k = hit[0]
            m = re.search(r"\b(\d)\s*/\s*(\d)\b", t.up)
            if m and int(m.group(2)) == n and 1 <= int(m.group(1)) <= n and int(m.group(1)) - 1 in hit:
                k = int(m.group(1)) - 1
            elif len(hit) > 1 and c.get("plan_days") and it.issue:
                # varias cuotas iguales: la que más cerca cae del vencimiento
                import datetime as dt
                k = min(hit, key=lambda j: abs((t.date - (it.issue + dt.timedelta(days=c["plan_days"][j]))).days))
            last = (k == n - 1) or _near(it.open, cash)
            return cash, ("" if last else "PARTIAL"), f"instalment_{k + 1}/{n}"
    return None


def _text_month(t):
    m = re.search(r"\b(" + MONTH_RE + r")[A-Z]*\.?\s*(?:DE\s+)?(\d{4}|\d{2})?\b", t.up)
    if not m:
        return None
    y = m.group(2)
    year = None
    if y:
        year = int(y) if len(y) == 4 else 2000 + int(y)
    return year, MONTHS[m.group(1)]


def match(engine, t):
    cl = getattr(engine, "clauses", None)
    if not cl:
        return False
    cash = abs(t.amount)
    if t.amount > 0:
        kinds = {"ARI"}
    else:
        kinds = {"API"}
    party, how = engine.resolve_party(t)
    cited = set()
    for tok in t.tokens:
        cited |= engine.ph.by_num.get(tok, set())
    cands = []
    seen = set()
    for iid in cited:
        it = engine.items[iid]
        if it.kind in kinds and it.open > 0 and engine.entity_ok(it, t):
            cands.append((0, it))
            seen.add(iid)
    if party:
        for it in engine.open_items_of(party, t, kinds):
            if it.id not in seen:
                cands.append((1, it))
                seen.add(it.id)
    if not cands:
        return False
    if party:  # si hay contraparte, lo citado debe ser suyo
        fam = {party} | engine.ph.children.get(party, set())
        cands = [(p, i) for p, i in cands if i.party in fam] or cands
    # (c) §3 y §5.8: plazos, retención y anticipo
    fits = []
    for prio, it in cands:
        c = cl.get(it.id)
        if not c:
            continue
        f = _fit(engine, it, c, cash, t)
        if f:
            fits.append((prio, it.issue, it.id, it, f))
    if fits:
        fits.sort(key=lambda x: (x[0], x[1] or __import__("datetime").date.min, x[2]))
        _, _, _, it, (amt, exc, label) = fits[0]
        engine.emit(t, it, amt, exc, "pdf_clause", label)
        return True
    # (b) §5.6 mes de servicio: mismo importe en varias facturas, el texto nombra un mes
    same = [it for _, it in cands if it.ccy == t.ccy and _near(it.open, cash)]
    if len(same) > 1:
        tm = _text_month(t)
        if tm:
            yr, mon = tm
            pick = []
            for it in same:
                mc = (cl.get(it.id) or {}).get("month")
                if not mc:
                    continue
                if mc[1] == mon and (yr is None or mc[0] == yr):
                    pick.append(it)
            if pick:
                it = sorted(pick, key=lambda i: (i.issue or __import__("datetime").date.min, i.id))[0]
                engine.emit(t, it, it.open, "", "pdf_clause", f"service_month {tm}")
                return True
    return False
