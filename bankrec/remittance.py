"""Plugin: avisos de pago (remittance advices). POLICIES §5.4 y §3 SHORT_DISPUTE.

Los clientes grandes mandan un aviso (txt, csv, xlsx o pdf) con los documentos pagados, las deducciones y su motivo.
`prepare` lo parsea todo a una estructura común; `match` lo casa con el cobro por pagador + fecha + total
(o por la referencia de pago que cita el texto del banco) y emite una fila por documento.

El parser no depende del formato exacto: cada línea de documento se reconoce por su forma (número de documento,
fecha, importes, texto del motivo), de modo que un cambio de columnas o de idioma no lo rompe.
"""
from __future__ import annotations

import bisect
import csv
import datetime as dt
import io
import re
from collections import defaultdict

from .data import ascii_upper, cents, rows
from .engine import PAYMENT_FACTORY

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
        if amts[1] < 0 < gross and -amts[1] < gross or (amts[1] < 0 and gross < 0 and amts[1] > gross):
            ded = amts[1]  # bruto + deducción (sin columna "pagado")
            paid = gross + ded
        else:
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
    engine.ra_fx = defaultdict(list)
    engine.ra_fx_dates = {}
    engine.ra_num = defaultdict(list)
    for it in engine.items.values():
        if it.number:
            engine.ra_num[norm_ref(it.number)].append(it)
    idx = base / "index.csv"
    if not idx.exists():
        return
    for r in rows(idx):
        path = base / r["file"]
        if not path.exists():
            continue
        try:
            adv = read_advice(path, r.get("format"))
            received = dt.date.fromisoformat(r["received_date"][:10])
        except Exception as e:  # un aviso roto no debe tumbar la fase
            engine.ra_errors = getattr(engine, "ra_errors", []) + [(r["file"], repr(e))]
            continue
        if not adv["lines"]:
            engine.ra_empty = getattr(engine, "ra_empty", []) + [r["file"]]
            continue
        adv["file"] = r["file"]
        adv["entity"] = r["entity"]
        adv["payer_name"] = r["payer_name"]
        adv["received"] = received
        sum_paid = sum(x["paid"] for x in adv["lines"])
        if adv["total"] is None:
            adv["total"] = sum_paid
        adv["sum_paid"] = sum_paid
        adv["party"] = engine.resolve_name(r["payer_name"])
        adv["refn"] = norm_ref(adv["ref"])
        engine.ra_list.append(adv)
        if adv['ccy']:
            engine.ra_fx[adv['entity']].append(adv)
        engine.ra_by_total[(adv["entity"], adv["total"])].append(adv)
        if sum_paid != adv["total"]:
            engine.ra_by_total[(adv["entity"], sum_paid)].append(adv)
        if len(adv["refn"]) >= 5:
            engine.ra_by_ref[adv["refn"]].append(adv)
    engine.ra_fx_dates = {}
    for ent, lst in engine.ra_fx.items():
        lst.sort(key=lambda a: a["received"])
        engine.ra_fx_dates[ent] = [a["received"] for a in lst]


# ------------------------------------------------------------------ casado
DISPUTE_RX = re.compile(r"PRICE|PRECIO|PREIS|PRIX|DAMAG|DANAD|ROTO|DEFECT|ENDOMMAG|BESCH|SHORT|INCOMPLET|FALTA|MANQU|"
                        r"PENAL|LATE|RETRASO|RETARD|VERSPAET|CO-?OP|COOP|MKT|MARKETING|LOGIST|FLETE|FREIGHT|TRANSPORT|"
                        r"DISPUT|CLAIM|RECLAM|RETURN|DEVOLUC|QUALITY|CALIDAD|DIFERENCIA|DIFFERENCE|DIFF|MERCANC|GOODS")
DISCOUNT_RX = re.compile(r"DISCOUNT|DESCUENTO|DTO|PRONTO PAGO|ESCOMPTE|SKONTO|EARLY")
WITHHOLD_RX = re.compile(r"WITHHOLD|RETENCION|RETENUE|RETENCAO|RETIDO|IRPF|IRRF|\bISR\b|\bRET\b|\bWHT\b|TAX AT SOURCE|QUELLENSTEUER|IMPOSTO|IMPUESTO")
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
    """El texto del banco cita la referencia de pago del aviso (como token entero o literal)."""
    r = adv["refn"]
    if len(r) < 5:
        return False
    if r in t.tokens:
        return True
    raw = ascii_upper(adv["ref"]).strip()
    return len(raw) >= 8 and raw in t.up


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
    fxset = set()
    # avisos en otra divisa que la cuenta (POLICIES §3 FX_DIFFERENCE): el total convertido cuadra con el spread del banco
    lst = engine.ra_fx.get(t.entity, ())
    keys = engine.ra_fx_dates.get(t.entity, ())
    lo = bisect.bisect_left(keys, t.date - dt.timedelta(days=15))
    hi = bisect.bisect_right(keys, t.date + dt.timedelta(days=5))
    for a in lst[lo:hi]:
        if a["ccy"] == t.ccy or a["file"] in cands:
            continue
        if t.orig_ccy == a["ccy"] and t.orig_amount:
            ok = abs(abs(t.orig_amount) - a["total"]) <= 2
        else:
            conv = engine.ph.fx.conv(a["total"], a["ccy"], t.ccy, t.date)
            # el banco convierte peor que la referencia: spread de 0,1 % a 1,5 % (POLICIES §3)
            ok = conv and -0.002 <= (conv - t.amount) / conv <= 0.016
        if ok:
            cands[a["file"]] = a
            fxset.add(a["file"])
    best, best_key = None, None
    for a in cands.values():
        if a["file"] in engine.ra_used:
            continue
        delta = (a["received"] - t.date).days
        cited = _cited(t, a)
        total_ok = a["total"] == cash or a["sum_paid"] == cash or a["file"] in fxset
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
    """Partida de una línea. El signo manda: pagado negativo = abono (los números con y sin prefijo chocan)."""
    pref = {"ARC"} if line["paid"] < 0 else {"ARI"}
    ents = {t.entity} | PAYMENT_FACTORY.get(t.entity, set())
    amt = abs(line["paid"])
    it = None
    for kinds in (pref, {"ARI", "ARC"}):
        # 1) número idéntico (con prefijo); 2) sin prefijo / ceros: la entidad tiene que coincidir siempre
        exact = [engine.items[i] for i in engine.ph.by_num.get(norm_ref(line["doc"]), ())
                 if engine.items[i].kind in kinds and engine.items[i].entity in ents]
        if not exact:  # índice propio (otros plugins pueden redirigir ph.by_num)
            exact = [i for i in engine.ra_num.get(norm_ref(line["doc"]), ()) if i.kind in kinds and i.entity in ents]
        if exact:
            exact.sort(key=lambda i: (-(i.open > 0), abs(i.open - amt) > 2, i.issue or dt.date.min))
            it = exact[0]
            break
        cand = engine.item_by_number(line["doc"], t.entity, party=party, kinds=kinds, amount=amt)
        if cand is not None and cand.entity in ents:
            it = cand
            break
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
        if ids:
            ids.sort(key=lambda i: (-(i.open > 0), i.issue or dt.date.min, i.id))
            it = ids[0]
    return it


SETTLE_RX = re.compile(r"(?:SETTLEMENT|REGULARI[ZS]A[A-Z]*|REGLEMENT|SALDO FRA)\s+([A-Z0-9][A-Z0-9\-]{4,})")


def _settlement(engine, t):
    """Pago del importe discutido de una factura (POLICIES §3, vacío: «the settlement of a disputed amount»).

    El texto dice «SETTLEMENT <factura>» o «REGULARIZACION <factura>» y el cobro es menor que el abierto:
    la disputa se hizo antes (otro pago con SHORT_DISPUTE), así que aquí se aplica lo cobrado y sin excepción."""
    m = SETTLE_RX.search(re.sub(r"\s*\|\s*", " ", t.up))
    if not m:
        return False
    party, _ = engine.resolve_party(t)
    it = engine.item_by_number(m.group(1), t.entity, party=party, kinds={"ARI"})
    if it is None or it.entity != t.entity:
        return False
    cash = abs(t.amount)
    if it.ccy == t.ccy:
        cash_item = cash
    elif t.orig_ccy == it.ccy and t.orig_amount:
        cash_item = abs(t.orig_amount)
    else:
        cash_item = engine.ph.fx.conv(cash, t.ccy, it.ccy, t.date)
    if 0 < it.open <= cash_item + 2:
        return False  # pago completo: lo resuelve el motor (sobrepago, FX...)
    exc = "FX_DIFFERENCE" if it.ccy != t.ccy else ""
    engine.emit(t, it, cash_item, exc, "dispute_settlement", f"liquidación de importe discutido {it.number}")
    return True


def _discount_amount(engine, it):
    term = engine.ph.terms.get(it.terms or "")
    if not term or not term.get("early_payment_discount_pct"):
        return None
    return int(round(it.total * float(term["early_payment_discount_pct"]) / 100))


def _explain_gap(engine, t, lines, gap):
    """El aviso lista importes brutos y el cobro es menor, sin decir dónde. Devuelve {item_id: (importe, excepción)}.

    Orden: (1) descuento pronto pago en un subconjunto de facturas cuya suma de descuentos es la diferencia
    (POLICIES §3 SHORT_DISCOUNT; fuera de plazo -> PARTIAL); (2) una factura con retención / comisiones / descuento;
    (3) pago parcial de una sola factura por una fracción redonda; (4) la última queda parcial."""
    inv = [(ln, it) for ln, it in lines if it.kind == "ARI" and not ln["ded"] and it.open > 0]
    if not inv:
        return {}
    # (1) subconjunto de descuentos
    disc = [(it, _discount_amount(engine, it)) for _, it in inv]
    disc = [(it, d) for it, d in disc if d]
    reach = {0: ()}
    for k, (it, d) in enumerate(disc):
        for sm, comb in list(reach.items()):
            ns = sm + d
            if ns <= gap + 2 * len(disc) and ns not in reach:
                reach[ns] = comb + (k,)
        if len(reach) > 200000:
            break
    for tol in range(0, 2 * len(disc) + 3):
        for g in (gap - tol, gap + tol):
            if g in reach and reach[g]:
                res = {}
                for k in reach[g]:
                    it = disc[k][0]
                    paid = it.open - disc[k][1]
                    ok = engine.discount_ok(it, t, paid)
                    if ok == "SHORT_DISCOUNT":
                        res[it.id] = (it.open, "SHORT_DISCOUNT")
                    else:
                        res[it.id] = (paid, "PARTIAL")
                return res
    # (2) una factura con retención / comisiones bancarias
    for ln, it in inv:
        e = engine.explain_short(it, t, gap, it.open - gap)
        if e in ("SHORT_WITHHOLDING", "SHORT_BANK_CHARGES"):
            return {it.id: (it.open, e)}
    # (3) fracción redonda de una factura
    cands = []
    for ln, it in inv:
        if 0 < gap < it.open:
            r = gap / it.open
            for q in (0.5, 0.25, 0.75, 0.1, 0.2, 0.3, 0.4, 0.6, 0.7, 0.8, 0.9, 0.05, 0.15):
                if abs(r - q) <= 0.0006:
                    cands.append((it, q))
                    break
    if len(cands) >= 1:
        it = sorted(cands, key=lambda x: -x[0].open)[0][0]
        return {it.id: (it.open - gap, "PARTIAL")}
    # (4) la última factura de la lista
    it = inv[-1][1]
    if gap < it.open:
        return {it.id: (it.open - gap, "PARTIAL")}
    return {}


def match(engine, t):
    if t.amount <= 0:
        return False
    if _settlement(engine, t):
        return True
    if not getattr(engine, "ra_list", None):
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
    # diferencia entre lo que lista el aviso (neto de deducciones explícitas) y el cobro
    override = {}
    if all(it.ccy == t.ccy for _, it in out):
        listed = sum(ln["paid"] for ln, _ in out)
        gap = listed - t.amount
        if gap > 2 and len(out) == len(adv["lines"]):
            override = _explain_gap(engine, t, out, gap)
    for ln, it in out:
        _emit_line(engine, t, adv, ln, it, ev, override.get(it.id))
    engine.ra_used.add(adv["file"])
    return True


def _withholding_rate(engine, it, short):
    from .engine import WITHHOLDING
    rates = WITHHOLDING.get(engine.ph.party_country.get(it.party))
    return bool(rates) and it.side == "AR" and any(abs(short - it.total * r) <= max(2, it.total * 0.0005) for r in rates)


def _emit_line(engine, t, adv, ln, it, ev, override=None):
    fxline = it.ccy != t.ccy
    if it.kind == "ARC":
        engine.emit(t, it, abs(ln["paid"]) if abs(ln["paid"]) > 0 else it.total, "", "remittance", ev)
        return
    paid = abs(ln["paid"])
    reason = ascii_upper(ln["reason"])
    if override:
        amount, exc = override
        engine.emit(t, it, amount, exc, "remittance_gap", ev)
        return
    exc = ""
    amount = paid
    short = it.open - paid
    if ln["ded"] or short > 2:
        if DISCOUNT_RX.search(reason) or not reason:
            e = engine.explain_short(it, t, short, paid)
            if e in ("SHORT_DISCOUNT", "SHORT_WITHHOLDING", "SHORT_BANK_CHARGES"):
                exc, amount = e, max(it.open, paid)
            elif short > 2:
                # descuento fuera de plazo o sin motivo: la diferencia queda abierta (POLICIES §3 PARTIAL)
                exc, amount = "PARTIAL", paid
        elif WITHHOLD_RX.search(reason):
            exc, amount = "SHORT_WITHHOLDING", max(it.open, paid)
        elif CHARGE_RX.search(reason) and not DISPUTE_RX.search(reason):
            exc, amount = "SHORT_BANK_CHARGES", max(it.open, paid)
        elif short > 2 and _withholding_rate(engine, it, short):
            # motivo raro pero la diferencia es exactamente un tipo de retención del país (POLICIES §3)
            exc, amount = "SHORT_WITHHOLDING", max(it.open, paid)
        elif short > 2:
            exc, amount = "SHORT_DISPUTE", paid
        else:
            amount = max(it.open, paid)
    if amount > it.total and it.total > 0:
        # el aviso paga más que la factura: sobrepago (la parte que sobra queda en cuenta)
        if exc == "" and amount <= 1.10 * it.total:
            exc = "OVERPAYMENT"
        amount = it.total
    if fxline and exc in ("", "SHORT_DISCOUNT", "SHORT_WITHHOLDING", "SHORT_BANK_CHARGES", "OVERPAYMENT"):
        exc = "FX_DIFFERENCE"
    engine.emit(t, it, amount, exc, "remittance", ev)
