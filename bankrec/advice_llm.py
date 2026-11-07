"""Plugin: avisos de pago en un formato que el parser no lee, resueltos con un modelo. Apagado salvo BANKREC_LLM=1.

Seguro para FINAL: el plan avisa de que un cliente grande cambia el diseño de sus avisos (PLAN.md §2, «Drift»). El parser de
`remittance.py` reconoce cada línea por su forma, pero si un aviso nuevo no da ninguna línea o falla, hoy se descarta entero.
Aquí se le pide al modelo que lo lea, y solo se aceptan las líneas cuyo número de documento y cuyos importes aparecen
literalmente en el fichero. Lo que no se pueda comprobar se descarta, y el motor sigue sin ese aviso como hasta ahora.
Debe ir ANTES de `remittance` en BANKREC_PLUGINS: envuelve `remittance.read_advice`, que `remittance.prepare` llama.
"""
from __future__ import annotations

import datetime as dt
import re

from . import llm, remittance

MAX_CHARS = 6000
SYSTEM = ("You extract the lines of a customer payment (remittance) advice. Answer with one JSON object and nothing else. "
          "Copy document numbers and amounts exactly as written in the text, as strings. Omit anything you are not sure about.")
PROMPT = """File content:
{text}

Return JSON:
{{"ref": payment reference or "", "pay_date": "YYYY-MM-DD" or "", "currency": "EUR" etc. or "", "total": total amount paid as written or "",
  "lines": [{{"doc": document number, "gross": invoice amount, "deduction": deducted amount (negative if shown so) or "0",
              "paid": amount paid for this document, "reason": deduction reason in the file's words or ""}}]}}
Include one line per document. Return {{"lines": []}} if the text is not a payment advice."""


def raw_text(path, fmt: str) -> str:
    """Texto del aviso, sea cual sea el formato; vacío si no se puede leer."""
    fmt = (fmt or path.suffix.lstrip(".")).lower()
    try:
        if fmt == "pdf":
            from pypdf import PdfReader
            return "\n".join(p.extract_text() or "" for p in PdfReader(str(path)).pages)
        if fmt == "xlsx":
            import openpyxl
            book = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
            return "\n".join(" ; ".join("" if c is None else str(c) for c in row) for sheet in book for row in sheet.iter_rows(values_only=True))
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:   # noqa: BLE001 - un aviso ilegible no debe tumbar la fase
        return ""


def _amounts_in(text: str) -> set:
    """Importes (en valor absoluto, céntimos) que aparecen como token en el texto."""
    out = set()
    for tok in re.split(r"[\s;|\t]+", text):
        tok = tok.strip(",:")
        if remittance.AMT_RE.match(tok):
            value = remittance.parse_amount(tok)
            if value is not None:
                out.add(abs(value))
    return out


def clean(data: dict, text: str):
    """Aviso en la estructura del motor, con solo lo comprobable en el texto; None si no queda ninguna línea."""
    present, flat = _amounts_in(text), remittance.norm_ref(text)
    lines = []
    for raw in data.get("lines") or []:
        doc = str(raw.get("doc", "")).strip()
        paid = remittance.parse_amount(str(raw.get("paid", "")))
        if not doc or paid is None or abs(paid) not in present or len(remittance.norm_ref(doc)) < 4 or remittance.norm_ref(doc) not in flat:
            continue
        gross = remittance.parse_amount(str(raw.get("gross", "")))
        ded = remittance.parse_amount(str(raw.get("deduction", "0"))) or 0
        if gross is None or abs(gross) not in present:
            gross = paid
        if ded and abs(ded) not in present:
            ded = paid - gross
        lines.append({"doc": doc, "date": None, "gross": gross, "ded": ded, "reason": str(raw.get("reason", ""))[:80], "paid": paid})
    if not lines:
        return None
    adv = remittance._blank()
    adv["lines"] = lines
    adv["ref"] = str(data.get("ref", "")).strip()
    total = remittance.parse_amount(str(data.get("total", "")))
    adv["total"] = total if total is not None and abs(total) in present else None
    adv["ccy"] = str(data.get("currency", "")).strip().upper()[:3] if re.fullmatch(r"[A-Za-z]{3}", str(data.get("currency", "")).strip()) else ""
    try:
        adv["pay_date"] = dt.date.fromisoformat(str(data.get("pay_date", ""))[:10])
    except ValueError:
        adv["pay_date"] = None
    return adv


def prepare(engine) -> None:
    original = remittance.read_advice
    if getattr(original, "llm_wrapped", False):
        return

    def read_advice(path, fmt):
        try:
            adv = original(path, fmt)
            if adv["lines"]:
                return adv
            failure = None
        except Exception as err:   # noqa: BLE001 - se informa al motor si el modelo tampoco lo lee
            adv, failure = None, err
        if llm.enabled():
            text = raw_text(path, fmt)[:MAX_CHARS]
            data = llm.ask_json(PROMPT.format(text=text), SYSTEM, max_tokens=1500) if text.strip() else None
            read = clean(data, text) if data else None
            if read:
                return read
        if failure is not None:
            raise failure
        return adv

    read_advice.llm_wrapped = True
    remittance.read_advice = read_advice
