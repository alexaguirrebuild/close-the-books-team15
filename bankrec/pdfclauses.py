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
import datetime as dt
import json
import os
import re
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from .data import ascii_upper, name_key, num_variants

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
    try:
        with ProcessPoolExecutor() as ex:
            return list(ex.map(_extract, paths, chunksize=64))
    except Exception:  # sin __main__ importable (stdin, notebooks): en serie
        return [_extract(p) for p in paths]


def _load_texts(pdir: Path, phase: str, wanted: list[str]):
    """Devuelve {file: texto} para los ficheros pedidos, con caché en out/pdf_text_<fase>.json."""
    cache_path = OUT / f"pdf_text_{phase}.json"
    cache = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    # Un texto vacío es un fallo de lectura, no un resultado: guardarlo dejaría ese PDF vacío en todas las ejecuciones siguientes.
    cache = {f: tx for f, tx in cache.items() if tx}
    missing = [f for f in wanted if f not in cache]
    if missing:
        texts = _extract_many([str(pdir / f) for f in missing])
        unread = sum(1 for tx in texts if not tx)
        if unread:
            print(f"  AVISO: {unread} PDF sin texto legible en {pdir}: se pierden sus cláusulas", flush=True)
        for f, tx in zip(missing, texts):
            if tx:
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


# ------------------------------------------------------------------ pagador en formatos que el motor no lee
def _extra_payers(t):
    up = ascii_upper(t.text)
    out = []
    m = re.search(r"/DE\s+(.+?)\s*/MOTIF", up)  # mt940 francés: VIR SEPA RECU /DE <pagador> /MOTIF ...
    if m:
        out.append(m.group(1))
    m = re.search(r"TRANSFER\.? EN DIV\.?\s*\|\s*[A-Z0-9]{6,12}\s*-\s*(.+?)\s*(?:\||$)", up)  # n43 divisas: BIC -NOMBRE
    if m:
        out.append(m.group(1))
    m = re.search(r"TRANSFERENCIAS?\s+DE\s+(.+?)\s*\|\s*(.+?)\s*(?:\|\s*(?:CONCEPTO|ORDENANTE)|$)", up)  # n43: nombre cortado a 38 car.
    if m:
        out += [m.group(1) + m.group(2), m.group(1) + " " + m.group(2)]
    m = re.search(r"/A\s+(.+?)\s*/MOTIF", up)  # mt940 francés, pago nuestro: VIR SEPA EMIS /A <beneficiario> /MOTIF ...
    if m:
        out.append(m.group(1))
    return out


def _name_cands(engine, raw):
    """Todas las contrapartes cuyo nombre (sin forma jurídica) coincide: 'Zubiri Jardins EURL' y '... SAS' son dos."""
    k = name_key(raw)
    toks = k.split()
    out = set()
    if not toks:
        return out
    for pid in engine.ph.name_index.get(toks[0], ()):
        for nk in engine.ph.party_names.get(pid, ()):
            if nk == k or (len(k) >= 8 and (nk.startswith(k) or k.startswith(nk))):
                out.add(pid)
                break
    return out


def _wrap_resolve(engine):
    if hasattr(engine, "resolve_parties") and hasattr(engine, "resolve_names"):
        orig_ps = engine.resolve_parties

        def resolve_parties(t):
            ps, how = orig_ps(t)
            ps = list(ps)
            for raw in _extra_payers(t):  # formatos de pagador que payer_text no lee
                for p in engine.resolve_names(raw):
                    if p not in ps:
                        ps.append(p)
                        how = how or "nombre_pdf"
            if len(ps) > 1:  # homónimos: se adelanta (sin demás cambios de orden) el que tiene una partida abierta que cuadra con el cobro
                cash = abs(t.amount)
                kinds = {"ARI", "ARC", "APC"} if t.amount > 0 else {"API", "APC", "ARC"}

                def score(pid):
                    its = engine.open_items_of(pid, t, kinds)
                    return 1 if any(abs(engine.to_bank(i.open, i, t) - cash) <= 2 for i in its) else 0

                ps = sorted(ps, key=lambda pid: -score(pid))
            return ps, how

        engine.resolve_parties = resolve_parties
        return
    orig = engine.resolve_party

    def resolve_party(t):
        r = orig(t)
        if r[1] == "iban":
            return r
        raws = [x for x in (t.cp_name, engine.payer_text(t)) if x] + _extra_payers(t)
        cands = set()
        for raw in raws:
            cands |= _name_cands(engine, raw)
        if not cands:
            return r
        if len(cands) == 1 and not r[0]:
            return next(iter(cands)), "nombre_pdf"
        if len(cands) > 1:  # homónimos: gana el que tiene una partida abierta que cuadra con el cobro
            kinds = {"ARI", "ARC"} if t.amount > 0 else {"API", "APC"}
            cash = abs(t.amount)
            good = []
            for pid in sorted(cands):
                for it in engine.open_items_of(pid, t, kinds):
                    if abs(engine.to_bank(it.open, it, t) - cash) <= 2 or it.number and any(
                            v in t.tokens for v in num_variants(it.number)):
                        good.append(pid)
                        break
            if len(good) == 1 and good[0] != r[0]:
                return good[0], "nombre_pdf"
        return r

    engine.resolve_party = resolve_party


# ------------------------------------------------------------------ prepare
def prepare(engine):
    ph = engine.ph
    _wrap_resolve(engine)
    engine.clauses = {}
    phase_dirs = [(ph.name, ph.dir, False)]
    if ph.name in PHASES:
        for p in PHASES[:PHASES.index(ph.name)]:
            d = ph.dir.parent / p
            if (d / "pdfs" / "index.csv").exists():
                phase_dirs.append((p, d, True))
    replaced = []  # (item_X, item_replacement)
    engine.replaced_map = {}
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
            engine.clauses[it.id] = c
            if c.get("replaces") and it.kind in ("ARI", "API"):
                x = engine.item_by_number(c["replaces"], r["entity"], party=r["party_id"] or None, kinds={it.kind})
                if x is not None and x.id != it.id:
                    replaced.append((x, it))
                elif x is None:  # la anulada ya no es partida abierta: su número apunta igualmente a la nueva
                    replaced.append((c["replaces"], it))
    # (a) §5.7 anula y sustituye: un pago que cita X liquida la factura nueva
    for x, new in replaced:
        num = x if isinstance(x, str) else x.number
        for v in num_variants(num):  # solo texto libre: registrarlo en by_num rompe los lotes de adeudos
            engine.replaced_map.setdefault(v, set()).add(new.id)
        if not isinstance(x, str):
            engine.clauses.setdefault(x.id, {})["replaced_by"] = new.id
    # índice importe -> partidas con cláusula que lo explican (para cobros sin contraparte resoluble)
    engine.clause_amt = {}
    for iid, c in engine.clauses.items():
        it = engine.items.get(iid)
        if it is None:
            continue
        vals = list(c.get("plan", ())) + [c.get(k) for k in ("ret", "net_due", "net_adv")]
        if c.get("adv") and not c.get("net_adv"):
            vals.append(it.total - c["adv"])
        for v in vals:
            if v:
                engine.clause_amt.setdefault(v, set()).add(iid)


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
        hit = [k for k, a in enumerate(plan) if abs(cash - a) <= 0]
        hit = hit or [k for k, a in enumerate(plan) if _near(cash, a)]
        if hit:
            n = len(plan)
            m = re.search(r"\b(\d)\s*/\s*(\d)\b", t.up)
            said = None
            if m and int(m.group(2)) == n and 1 <= int(m.group(1)) <= n:
                said = int(m.group(1)) - 1
            paid = it.total - it.open
            consumed = 0
            while consumed < n and sum(plan[:consumed + 1]) <= paid + TOL:
                consumed += 1
            consumed = min(consumed, n - 1)
            if said is not None and said in hit:
                k = said
            elif len(hit) == 1:
                k = hit[0]
            elif paid > TOL and consumed in hit:
                k = consumed  # el saldo ya dice cuántas cuotas van
            elif c.get("plan_days") and it.issue and engine.ph.name == "sample":
                # solo la muestra no trae el histórico: la cuota cuyo vencimiento cae más cerca del cobro
                k = min(hit, key=lambda j: abs((t.date - (it.issue + dt.timedelta(days=c["plan_days"][j]))).days))
            else:
                k = hit[0] if paid <= TOL else min(hit, key=lambda j: abs(j - consumed))
            # última cuota (§3): el texto lo dice, no queda saldo tras este cobro, o es la cuota final del plan
            last = (k == n - 1) or (it.open - cash <= TOL)
            return cash, ("" if last else "PARTIAL"), f"instalment_{k + 1}/{n}"
    return None


def _text_month(t):
    """Mes de servicio que nombra el texto del banco: palabra exacta (no 'SEPA'), con año de 2 o 4 cifras si lo hay."""
    words = re.findall(r"[A-Z]+|\d+", t.up)
    for i, w in enumerate(words):
        if w in MONTHS and w not in ("MAR", "DEC", "JUN", "JUL", "AGO", "MAI") or w in ("MAR", "DEC", "JUN", "JUL", "AGO", "MAI")                 and i + 1 < len(words) and words[i + 1].isdigit() and len(words[i + 1]) in (2, 4):
            year = None
            nxt = words[i + 1] if i + 1 < len(words) else ""
            if nxt == "DE" and i + 2 < len(words):
                nxt = words[i + 2]
            if nxt.isdigit() and len(nxt) in (2, 4):
                year = int(nxt) if len(nxt) == 4 else 2000 + int(nxt)
            return year, MONTHS[w]
    return None


def _name_evidence(engine, it, words):
    """¿El texto del banco nombra a la contraparte de la partida? (nombres truncados incluidos)"""
    for k in engine.ph.party_names.get(it.party, ()):
        toks = k.split()
        if not toks:
            continue
        hit = 0
        for tk in toks:
            if tk in words or (len(tk) >= 4 and any(len(w) >= 4 and (w.startswith(tk) or tk.startswith(w)) for w in words)):
                hit += 1
        if hit >= min(2, len(toks)) and hit >= 0.6 * len(toks):
            return True
    return False


HINT = re.compile(r"PLAZO|ECHEANCE|INSTALMENT|INSTALLMENT|RATE \d|RETENTION|RETENCION|GARANTIA|GARANTIE|ANTICIPO|ADVANCE|ACOMPTE")


def match(engine, t):
    cl = getattr(engine, "clauses", None)
    if not cl:
        return False
    # (a) §5.7: el texto cita una factura anulada sin partida -> se liquida la que la sustituye
    rm = getattr(engine, "replaced_map", None)
    if rm:
        hits = [tok for tok in t.tokens if tok in rm]
        if hits:
            added = []
            for tok in hits:
                for iid in rm[tok]:
                    if iid not in engine.ph.by_num[tok]:
                        engine.ph.by_num[tok].add(iid)
                        added.append((tok, iid))
            n0 = len(engine.rows)
            try:
                ok = engine.m_refs(t) and len(engine.rows) > n0
            finally:
                for tok, iid in added:
                    engine.ph.by_num[tok].discard(iid)
            if ok:
                return True
            del engine.rows[n0:]
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
    # cobros cuyo importe es el de una cuota / retención / neto de anticipo: con evidencia de nombre
    if True:
        words = set(re.sub(r"[^A-Z0-9]+", " ", t.up).split())
        for d in (0, 1, -1, 2, -2):
            for iid in getattr(engine, "clause_amt", {}).get(cash + d, ()):
                if iid in seen:
                    continue
                it = engine.items[iid]
                if it.kind in kinds and it.open > 0 and engine.entity_ok(it, t) and (it.issue is None or it.issue <= t.date):
                    if _name_evidence(engine, it, words) or (not party and HINT.search(t.up) and not t.cp_iban):
                        cands.append((2, it))
                        seen.add(iid)
    if not cands:
        return False
    if party:  # si hay contraparte, lo citado debe ser suyo
        fam = {party} | engine.ph.children.get(party, set())
        cands = [(p, i) for p, i in cands if p != 1 or i.party in fam]  # lo citado manda sobre el nombre
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
                if not mc and it.issue:  # cuotas mensuales: se facturan a primeros del mes siguiente (§5.6)
                    y, mo = (it.issue.year, it.issue.month - 1) if it.issue.month > 1 else (it.issue.year - 1, 12)
                    mc = (y, mo)
                if not mc:
                    continue
                if mc[1] == mon and (yr is None or mc[0] == yr):
                    pick.append(it)
            if pick:
                it = sorted(pick, key=lambda i: (i.issue or __import__("datetime").date.min, i.id))[0]
                engine.emit(t, it, it.open, "", "pdf_clause", f"service_month {tm}")
                return True
    return False
