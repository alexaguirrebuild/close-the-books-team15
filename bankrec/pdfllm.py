"""Plugin: lectura de cláusulas de PDF con un modelo, solo cuando las regex no leen nada. Apagado salvo BANKREC_LLM=1.

Es un seguro para FINAL, donde cambian los formatos y los idiomas (PLAN.md §2, «Drift»). Se pide al modelo solo cuando:
  - `pdfclauses.parse_clauses` devuelve vacío, y
  - el documento trae texto fuera de la plantilla (más de MIN_EXTRA caracteres tras la línea de total).
No se fía de la respuesta: cada importe y cada número de factura devuelto tiene que aparecer en el texto del PDF, y solo
se aceptan las claves que ya consume `pdfclauses`. Lo demás se descarta y el motor sigue con sus reglas.
Debe ir ANTES de `pdfclauses` en BANKREC_PLUGINS: envuelve `pdfclauses.parse_clauses`, que `pdfclauses.prepare` llama.
"""
from __future__ import annotations

import re

from . import llm, pdfclauses
from .data import ascii_upper

MIN_EXTRA = 80
SKIP_TYPES = {"proforma", "bank_account_notice"}
CREDIT_TYPES = {"credit_note", "supplier_credit_note"}
AMOUNT_KEYS = ("ret", "net_due", "adv", "net_adv")
SYSTEM = ("You extract payment clauses from invoice text. Answer with one JSON object and nothing else. "
          "Copy amounts and document numbers exactly as written in the text, as strings. Omit any key you are not sure about.")
PROMPT = """Document type: {doc_type}
Text:
{text}

Return JSON with only the keys that apply:
  "plan": list of instalment amounts, e.g. ["1.000,00", "1.000,00"]
  "ret": retention amount held back; "net_due": amount payable at the due date
  "adv": advance already received to be deducted; "net_adv": net amount to pay after the advance
  "replaces": number of the invoice this document cancels and replaces
  "cn_mode": "offset" if a credit note will be deducted from the next payment, "refund" if it will be paid out by transfer
Return {{}} if the text contains none of these."""

_DISCLAIMER = re.compile(r"Documento sint[eé]tico.*$", re.I)


def extra_text(raw: str) -> str:
    """Texto tras la línea de total que no es plantilla (condiciones estándar, IBAN, aviso de documento sintético)."""
    text = re.sub(r"\s+", " ", raw)
    m = re.search(r"(?:GESAMT|TOTAL TTC|TOTALE?|GESAMTBETRAG)\s*[\d. ,]+\s*[A-Z]{3}(.*)$", text)
    tail = _DISCLAIMER.sub("", m.group(1) if m else "")
    tail = re.sub(r"IBAN:\s*\S+\s*BIC:\s*\S+", "", tail)
    tail = re.sub(r"(Forma de pago|Zahlungsbedingungen|Conditions|Terms|Pagamento|Condições)\s*:[^A-Z]{0,60}(d[ií]as[^A-Z]*)?", "", tail)
    return tail.strip()


def _amounts_in(raw: str) -> set:
    return {a for a in (pdfclauses.parse_amt(m) for m in re.findall(pdfclauses.AMT, raw)) if a}


def clean(data: dict, raw: str, doc_type: str) -> dict:
    """Solo lo que está literalmente en el texto y es una clave conocida."""
    present = _amounts_in(raw)
    out = {}
    for key in AMOUNT_KEYS:
        value = pdfclauses.parse_amt(str(data[key])) if key in data else None
        if value in present:
            out[key] = value
    plan = data.get("plan")
    if isinstance(plan, list) and len(plan) >= 2:
        values = [pdfclauses.parse_amt(str(x)) for x in plan]
        if all(v in present for v in values):
            out["plan"] = values
    number = str(data.get("replaces", "")).strip(".-/ ")
    if number and number.upper() in re.sub(r"\s+", " ", ascii_upper(raw)):
        out["replaces"] = number.upper()
    if doc_type in CREDIT_TYPES and data.get("cn_mode") in ("offset", "refund"):
        out["cn_mode"] = data["cn_mode"]
    return out


def prepare(engine) -> None:
    original = pdfclauses.parse_clauses
    if getattr(original, "llm_wrapped", False):
        return

    def parse_clauses(raw: str, doc_type: str) -> dict:
        found = original(raw, doc_type)
        if found or doc_type in SKIP_TYPES or not llm.enabled() or len(extra_text(raw)) < MIN_EXTRA:
            return found
        data = llm.ask_json(PROMPT.format(doc_type=doc_type, text=re.sub(r"\s+", " ", raw)[:3000]), SYSTEM)
        return clean(data, raw, doc_type) if data else found

    parse_clauses.llm_wrapped = True
    pdfclauses.parse_clauses = parse_clauses
