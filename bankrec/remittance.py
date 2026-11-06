"""Plugin: avisos de pago (remittance advices). POLICIES §5.4 y §3 SHORT_DISPUTE.

Los clientes grandes mandan un aviso (txt, csv, xlsx o pdf) con los documentos pagados, las deducciones y su motivo.
`prepare` lo parsea todo a una estructura común; `match` lo casa con el cobro por pagador + fecha + total
(o por la referencia de pago que cita el texto del banco) y emite una fila por documento.

El parser no depende del formato exacto: cada línea de documento se reconoce por su forma (número de documento,
fecha, importes, texto del motivo), de modo que un cambio de columnas o de idioma no lo rompe.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import re
from collections import defaultdict

from .data import ascii_upper, cents, rows

# ------------------------------------------------------------------ números y fechas
AMT_RE = re.compile(r"^\(?[-+]?\d[\d.,' ]*\)?$")
DATE_RE = re.compile(r"^(\d{1,4})[/.\-](\d{1,2})[/.\-](\d{1,4})$")
DOC_RE = re.compile(r"^(?=.*\d)[A-Za-z0-9][A-Za-z0-9\-_/.]{3,}$")


def parse_amount(s):
    """'1.116,89' / '1,295.52' / '1295.52' / '-388,88' / '(12.00)' -> céntimos (int) o None."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return int(round(float(s) * 100))
    s = str(s).strip().replace(" ", " ").replace("€", "").replace("EUR", "").strip()
    if not s or not AMT_RE.match(s):
        return None
    neg = s.startswith("(") or s.startswith("-") or s.endswith("-")
    s = re.sub(r"[()+\-\s']", "", s)
    if not s or not re.search(r"\d", s):
        return None
    last = max(s.rfind("."), s.rfind(","))
    if last < 0:
        v = float(s)
        return (-1 if neg else 1) * int(round(v * 100))
    tail = s[last + 1:]
    head = re.sub(r"[.,]", "", s[:last])
    if len(tail) == 3 and (s.count(".") + s.count(",")) >= 1 and not (s.count(".") and s.count(",")):
        # un solo separador con 3 cifras detrás: miles ("1.234"), no decimales
        v = float(head + tail)
    else:
        v = float((head or "0") + "." + tail)
    return (-1 if neg else 1) * int(round(v * 100))


def parse_date(s, prefer_dmy=True):
    """Fecha en ISO, dd/mm/aaaa, mm/dd/aaaa (inglés) o con año de 2 cifras. Devuelve date o None."""
    if isinstance(s, dt.datetime):
        return s.date()
    if isinstance(s, dt.date):
        return s
    if s is None:
        return None
    s = str(s).strip()
    m = DATE_RE.match(s)
    if not m:
        return None
    a, b, c = (int(x) for x in m.groups())
    try:
        if len(m.group(1)) == 4:
            return dt.date(a, b, c)
        y = c + 2000 if c < 100 else c
        if a > 12:
            return dt.date(y, b, a)
        if b > 12:
            return dt.date(y, a, b)
        return dt.date(y, b, a) if prefer_dmy else dt.date(y, a, b)
    except ValueError:
        return None


def norm_ref(s):
    return re.sub(r"[^A-Z0-9]", "", ascii_upper(str(s or "")))


# ------------------------------------------------------------------ parseo de una línea de documento
STOP_WORDS = {"TOTAL", "SUBTOTAL", "DOCUMENT", "DOCUMENTO", "DATE", "FECHA", "GROSS", "BRUTO", "PAID", "PAGADO", "REF",
              "AMOUNT", "IMPORTE", "PAYEE", "BENEFICIARIO", "DEDUCTION", "DEDUCCION", "KIND", "REGARDS", "SALUDOS"}


def parse_line(cells):
    """Una línea de documento a partir de celdas/tokens: (doc, date, gross, ded, reason, paid) o None."""
    cells = [c for c in cells if c is not None and str(c).strip() != ""]
    if not cells:
        return None
    toks = []
    for c in cells:
        toks.extend(str(c).replace(" ", " ").split())
    doc = date = None
    amts, words = [], []
    for tk in toks:
        d = parse_date(tk) if DATE_RE.match(tk) else None
        if d and date is None:
            date = d
            continue
        a = parse_amount(tk) if AMT_RE.match(tk) else None
        has_dec = bool(re.search(r"[.,]\d{1,2}$", tk))
        if a is not None and (has_dec or (doc is not None and len(re.sub(r"\D", "", tk)) < 5) or tk in ("0", "-0")):
            amts.append(a)
            continue
        if doc is None and DOC_RE.match(tk) and ascii_upper(tk) not in STOP_WORDS:
            doc = tk
            continue
        if re.search(r"[A-Za-z]", tk):
            words.append(tk)
    if doc is None or not amts:
        return None
    gross = ded = None
    paid = amts[-1]
    if len(amts) >= 3:
        gross, ded = amts[0], amts[-2]
    elif len(amts) == 2:
        gross = amts[0]
        ded = paid - gross if paid != gross else 0
    else:
        gross = paid
        ded = 0
    reason = " ".join(words).strip()
    return {"doc": doc, "date": date, "gross": gross, "ded": ded, "reason": reason, "paid": paid}


# ------------------------------------------------------------------ lectores por formato
def _blank():
    return {"ref": "", "pay_date": None, "ccy": "", "total": None, "lines": [], "payer": ""}


def _scan_header(adv, text):
    """Referencia, fecha de pago, importe y divisa a partir de texto libre."""
    m = re.search(r"(?:payment advice|aviso de pago|avis de paiement|zahlungsavis|payment ref\.?|ref\.?)\W+"
                  r"([A-Za-z0-9][A-Za-z0-9/\-_.]{3,})", text, re.I)
    if m and not adv["ref"]:
        adv["ref"] = m.group(1).strip(".")
    m = re.search(r"(-?[\d][\d.,]*[.,]\d{2})\s*(EUR|USD|GBP|PLN|CHF|MXN|BRL|CNY|AUD|CAD|ZAR|MAD)\b", text)
    if m and adv["total"] is None:
        adv["total"] = parse_amount(m.group(1))
        adv["ccy"] = m.group(2)
    m = re.search(r"\b(?:on|con fecha|le|am|vom|date)\s+(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4})", text, re.I)
    if m and not adv["pay_date"]:
        # inglés: mm/dd; el resto dd/mm
        english = bool(re.search(r"payment of|have sent|please be advised", text, re.I))
        adv["pay_date"] = parse_date(m.group(1), prefer_dmy=not english)


def read_txt(path):
    text = path.read_text(encoding="utf-8", errors="replace")
    adv = _blank()
    _scan_header(adv, text)
    m = re.search(r"^Subject:\s*(.*)$", text, re.M)
    if m:
        _scan_header(adv, m.group(1))
    english = bool(re.search(r"payment of|please be advised|remittance", text, re.I))
    for ln in text.splitlines():
        if not ln.startswith((" ", "\t")):
            continue
        r = parse_line(ln.split())
        if r:
            if english and r["date"] is None:
                pass
            adv["lines"].append(r)
    return adv


def _hdr_map(cells):
    """Índices de columnas por nombre (sinónimos en es/en/fr/de)."""
    m = {}
    for i, c in enumerate(cells):
        k = ascii_upper(str(c or "")).strip()
        if not k:
            continue
        if re.search(r"DOCUMENT|FACTURA|INVOICE|BELEG|RECHNUNG|N[UO]M|VOUCHER|REF", k) and "doc" not in m and "ORDER" not in k \
                and "PEDIDO" not in k:
            m["doc"] = i
        elif re.search(r"^(DATE|FECHA|DATUM|DOC_DATE)", k) and "date" not in m:
            m["date"] = i
        elif re.search(r"GROSS|BRUT|BRUTTO", k) and "gross" not in m:
            m["gross"] = i
        elif re.search(r"DISCOUNT|DEDUC|DTO|ABZUG|RETENC|RETENU", k) and "ded" not in m:
            m["ded"] = i
        elif re.search(r"REASON|MOTIVO|CONCEPT|GRUND|MOTIF|NOTE", k) and "reason" not in m:
            m["reason"] = i
        elif re.search(r"^(PAID|PAGADO|PAYE|BEZAHLT|NET|IMPORTE PAG|PAYMENT AMOUNT)", k) and "paid" not in m:
            m["paid"] = i
    return m


def _from_table(table):
    """table: lista de listas de celdas (csv/xlsx). Reconoce cabecera de aviso + tabla de documentos."""
    adv = _blank()
    hdr = None
    for cells in table:
        cells = [("" if c is None else c) for c in cells]
        flat = " ".join(str(c) for c in cells)
        low = ascii_upper(flat)
        # cabecera de pago: payment_ref,X,payment_date,Y,currency,EUR  /  Payment advice,REF,2025-06-02
        for i, c in enumerate(cells):
            k = ascii_upper(str(c)).replace("_", " ").strip()
            nxt = cells[i + 1] if i + 1 < len(cells) else ""
            if k in ("PAYMENT REF", "REF", "REFERENCE", "PAYMENT ADVICE", "AVISO DE PAGO", "REFERENCIA") and nxt not in ("", None):
                adv["ref"] = adv["ref"] or str(nxt).strip()
                d = parse_date(cells[i + 2]) if i + 2 < len(cells) and not isinstance(cells[i + 2], (int, float)) else None
                if d and k in ("PAYMENT ADVICE", "AVISO DE PAGO") and not adv["pay_date"]:
                    adv["pay_date"] = d
            elif k in ("PAYMENT DATE", "DATE", "FECHA", "FECHA DE PAGO") and nxt not in ("", None) and not adv["pay_date"]:
                adv["pay_date"] = parse_date(nxt)
            elif k in ("CURRENCY", "DIVISA", "MONEDA", "WAEHRUNG") and nxt:
                adv["ccy"] = str(nxt).strip()
        if hdr is None:
            m = _hdr_map(cells)
            if "doc" in m and "paid" in m:
                hdr = m
                continue
        if re.match(r"^\s*(TOTAL|SUBTOTAL|SUMA)\b", low):
            nums = [parse_amount(c) for c in cells[1:] if c not in ("", None)]
            nums = [n for n in nums if n is not None]
            if nums:
                adv["total"] = nums[-1]
            continue
        if hdr is not None:
            def g(key):
                i = hdr.get(key)
                return cells[i] if i is not None and i < len(cells) else None
            doc = g("doc")
            paid = parse_amount(g("paid"))
            if doc in (None, "") or paid is None or not DOC_RE.match(str(doc).strip()):
                continue
            gross = parse_amount(g("gross"))
            ded = parse_amount(g("ded"))
            gross = paid - (ded or 0) if gross is None else gross
            adv["lines"].append({"doc": str(doc).strip(), "date": parse_date(g("date")), "gross": gross,
                                 "ded": ded or 0, "reason": str(g("reason") or "").strip(), "paid": paid})
        else:
            r = parse_line(cells) if len(cells) >= 3 else None
            if r and r["date"]:
                adv["lines"].append(r)
    return adv


def read_csv(path):
    raw = path.read_bytes().decode("utf-8-sig", errors="replace")
    delim = ";" if raw.count(";") > raw.count(",") else ","
    return _from_table(list(csv.reader(io.StringIO(raw), delimiter=delim)))


def read_xlsx(path):
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    table = []
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            table.append(list(row))
    adv = _from_table(table)
    return adv


def read_pdf(path):
    import logging
    import pypdf
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    rd = pypdf.PdfReader(str(path))
    toks = []
    for pg in rd.pages:
        toks.extend((pg.extract_text() or "").splitlines())
    adv = _blank()
    full = "\n".join(toks)
    # cabecera: valores en la línea siguiente a la etiqueta
    def after(label_rx):
        for i, t in enumerate(toks):
            if re.match(label_rx, t.strip(), re.I) and i + 1 < len(toks):
                return toks[i + 1].strip()
        return None
    ref = after(r"^(payment ref\.?|ref\.?|referencia|r[eé]f[eé]rence)$")
    if ref:
        adv["ref"] = ref
    d = after(r"^(date|fecha|datum)$")
    if d:
        adv["pay_date"] = parse_date(d)
    am = after(r"^(amount|importe|montant|betrag)$")
    if am:
        m = re.match(r"(-?[\d.,]+)\s*([A-Z]{3})?", am)
        if m:
            adv["total"] = parse_amount(m.group(1))
            adv["ccy"] = m.group(2) or ""
    # documentos: cada línea empieza por un token con forma de número de documento
    start = 0
    for i, t in enumerate(toks):
        if re.match(r"^(paid|pagado|pay[eé]|bezahlt)$", t.strip(), re.I):
            start = i + 1
    recs, cur = [], None
    for t in toks[start:]:
        s = t.strip()
        if not s or s.startswith("Documento sint") or "Synthetic" in s:
            continue
        first = s.split()[0]
        is_doc = (DOC_RE.match(first) and not DATE_RE.match(first) and not AMT_RE.match(first)) or \
                 (re.fullmatch(r"\d{5,}", first) and cur is None) or \
                 (re.fullmatch(r"\d{5,}", first) and cur is not None and len(cur) >= 3 and parse_amount(first) is not None
                  and DATE_RE.match(cur[1] if len(cur) > 1 else "") and False)
        if is_doc and (cur is None or len(cur) >= 3):
            if cur:
                recs.append(cur)
            cur = [s]
        elif cur is not None:
            cur.append(s)
    if cur:
        recs.append(cur)
    for rc in recs:
        r = parse_line(rc)
        if r:
            adv["lines"].append(r)
    return adv


READERS = {"txt": read_txt, "csv": read_csv, "xlsx": read_xlsx, "pdf": read_pdf}


def read_advice(path, fmt):
    fmt = (fmt or path.suffix.lstrip(".")).lower()
    return READERS[fmt](path)


# ------------------------------------------------------------------ preparación
def prepare(engine):
    ph = engine.ph
    base = ph.dir / "remittance_advices"
    engine.ra_list = []
    engine.ra_by_total = defaultdict(list)
    engine.ra_by_ref = defaultdict(list)
    engine.ra_used = set()
    idx = base / "index.csv"
    if not idx.exists():
        return
    for r in rows(idx):
        path = base / r["file"]
        if not path.exists():
            continue
        try:
            adv = read_advice(path, r.get("format"))
        except Exception as e:  # un aviso roto no debe tumbar la fase
            engine.ra_errors = getattr(engine, "ra_errors", []) + [(r["file"], repr(e))]
            continue
        if not adv["lines"]:
            continue
        adv["file"] = r["file"]
        adv["entity"] = r["entity"]
        adv["payer_name"] = r["payer_name"]
        adv["received"] = dt.date.fromisoformat(r["received_date"][:10])
        sum_paid = sum(x["paid"] for x in adv["lines"])
        if adv["total"] is None:
            adv["total"] = sum_paid
        adv["sum_paid"] = sum_paid
        adv["party"] = engine.resolve_name(r["payer_name"])
        adv["refn"] = norm_ref(adv["ref"])
        engine.ra_list.append(adv)
        engine.ra_by_total[(adv["entity"], adv["total"])].append(adv)
        if sum_paid != adv["total"]:
            engine.ra_by_total[(adv["entity"], sum_paid)].append(adv)
        if len(adv["refn"]) >= 5:
            engine.ra_by_ref[adv["refn"]].append(adv)


# ------------------------------------------------------------------ casado
DISPUTE_RX = re.compile(r"PRICE|PRECIO|PREIS|PRIX|DAMAG|DANAD|ROTO|DEFECT|ENDOMMAG|BESCH|SHORT|INCOMPLET|FALTA|MANQU|"
                        r"PENAL|LATE|RETRASO|RETARD|VERSPAET|CO-?OP|COOP|MKT|MARKETING|LOGIST|FLETE|FREIGHT|TRANSPORT|"
                        r"DISPUT|CLAIM|RECLAM|RETURN|DEVOLUC|QUALITY|CALIDAD|DIFERENCIA|DIFFERENCE|DIFF|MERCANC|GOODS")
DISCOUNT_RX = re.compile(r"DISCOUNT|DESCUENTO|DTO|PRONTO PAGO|ESCOMPTE|SKONTO|EARLY")
WITHHOLD_RX = re.compile(r"WITHHOLD|RETENCION|RETENUE|RETENCAO|IRPF|TAX AT SOURCE|QUELLENSTEUER")
CHARGE_RX = re.compile(r"BANK CHARGE|COMISION|GASTOS BANC|FRAIS BANC|BANKGEB|CHARGES")


def _family(engine, p):
    if not p:
        return set()
    fam = {p} | engine.ph.children.get(p, set())
    c = engine.ph.customers.get(p)
    if c:
        if c.get("parent_customer_id"):
            fam.add(c["parent_customer_id"])
            fam |= engine.ph.children.get(c["parent_customer_id"], set())
        if c.get("group_id"):
            fam |= {k for k, v in engine.ph.customers.items() if v.get("group_id") == c["group_id"]}
    return fam


def _cited(t, adv):
    r = adv["refn"]
    if len(r) < 5:
        return False
    if r in t.tokens:
        return True
    dg = re.sub(r"\D", "", r)
    return len(dg) >= 8 and (dg in t.tokens or dg in re.sub(r"\D", "", t.up))


def _find_adv(engine, t):
    cash = t.amount
    cands = {}
    for a in engine.ra_by_total.get((t.entity, cash), []):
        cands[a["file"]] = a
    # divisa distinta: tolerancia 3 % (FX); se busca por pagador
    for tok in t.tokens:
        for a in engine.ra_by_ref.get(tok, []):
            if a["entity"] == t.entity:
                cands.setdefault(a["file"], a)
    party, _ = engine.resolve_party(t)
    fam = _family(engine, party)
    best, best_key = None, None
    for a in cands.values():
        if a["file"] in engine.ra_used:
            continue
        delta = (a["received"] - t.date).days
        cited = _cited(t, a)
        total_ok = a["total"] == cash or a["sum_paid"] == cash
        if not cited and not (-5 <= delta <= 15):
            continue
        if not cited and not total_ok:
            continue
        pm = bool(party and a["party"] and (a["party"] in fam or party in _family(engine, a["party"])))
        if party and a["party"] and not pm and not cited:
            continue
        key = (cited, pm, -abs(delta))
        if best is None or key > best_key:
            best, best_key = a, key
    return best


def _lookup(engine, line, t, party):
    amt = abs(line["paid"]) if line["ded"] == 0 else None
    it = engine.item_by_number(line["doc"], t.entity, party=party, kinds={"ARI", "ARC"})
    if it is None:
        it = engine.item_by_number(line["doc"], None, party=party, kinds={"ARI", "ARC"})
    if it is None:
        # número del cliente (su pedido/voucher)
        from .data import num_variants
        ids = set()
        for v in num_variants(line["doc"]):
            ids |= engine.ph.by_po.get(v, set())
        ids = [engine.items[i] for i in ids if engine.items[i].kind in ("ARI", "ARC")]
        if party:
            fam = _family(engine, party)
            ids = [i for i in ids if i.party in fam] or ids
        if len(ids) >= 1:
            ids.sort(key=lambda i: (-(i.open > 0), i.issue))
            it = ids[0]
    return it


def match(engine, t):
    if t.amount <= 0 or not getattr(engine, "ra_list", None):
        return False
    adv = _find_adv(engine, t)
    if adv is None:
        return False
    party, _ = engine.resolve_party(t)
    party = party or adv["party"]
    ev = f"aviso {adv['file']}"
    out = []
    seen = set()
    for ln in adv["lines"]:
        it = _lookup(engine, ln, t, party)
        if it is None or it.id in seen:
            continue
        seen.add(it.id)
        out.append((ln, it))
    if not out:
        return False
    for ln, it in out:
        _emit_line(engine, t, adv, ln, it, ev)
    engine.ra_used.add(adv["file"])
    return True


def _emit_line(engine, t, adv, ln, it, ev):
    if it.kind == "ARC":
        engine.emit(t, it, it.open if it.open > 0 else it.total, "", "remittance", ev)
        return
    fxline = it.ccy != t.ccy
    paid = abs(ln["paid"])
    reason = ascii_upper(ln["reason"])
    full_open = it.open
    exc = ""
    amount = full_open
    if full_open <= 0:
        # ya liquidada: pago repetido (POLICIES §3 DUPLICATE_PAYMENT)
        engine.emit(t, it, paid, "DUPLICATE_PAYMENT", "remittance", ev)
        return
    short = full_open - paid
    if ln["ded"] and ln["ded"] != 0 or short > 2:
        if DISCOUNT_RX.search(reason) or not reason:
            e = engine.explain_short(it, t, short, paid)
            if e in ("SHORT_DISCOUNT", "SHORT_WITHHOLDING", "SHORT_BANK_CHARGES"):
                exc, amount = e, full_open
            elif short > 2 and not reason:
                exc, amount = "PARTIAL", paid
            elif short > 2:
                exc, amount = "SHORT_DISPUTE", paid
        elif WITHHOLD_RX.search(reason):
            exc, amount = "SHORT_WITHHOLDING", full_open
        elif CHARGE_RX.search(reason) and not DISPUTE_RX.search(reason):
            exc, amount = "SHORT_BANK_CHARGES", full_open
        elif short > 2:
            exc, amount = "SHORT_DISPUTE", paid
    elif paid > full_open + 2:
        exc, amount = "OVERPAYMENT", full_open
    if fxline and exc in ("", "SHORT_DISCOUNT", "SHORT_WITHHOLDING", "SHORT_BANK_CHARGES", "OVERPAYMENT"):
        exc = "FX_DIFFERENCE"
    engine.emit(t, it, amount, exc, "remittance", ev)
