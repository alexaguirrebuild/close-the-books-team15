"""Cliente mínimo de la API de Anthropic para casos residuales. Sin dependencias (urllib) y apagado por defecto.

Activar:   BANKREC_LLM=1   y   ANTHROPIC_API_KEY en el entorno o en `.env` (que no se sube a git).
Seguridad: la clave nunca se imprime ni se guarda en la caché; ante cualquier fallo devuelve None y el motor sigue con sus reglas.
Coste:     tope de llamadas por ejecución (BANKREC_LLM_MAX_CALLS, 200 por defecto) y caché en disco: el mismo prompt no se paga dos veces.
Modelo:    BANKREC_LLM_MODEL (claude-haiku-5-5 por defecto).
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = Path(os.environ.get("BANKREC_LLM_CACHE", ROOT / "cache" / "llm"))
URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-haiku-5-5"
KEY_NAME = "ANTHROPIC_API_KEY"

calls = 0   # llamadas reales hechas en esta ejecución
last_error = ""   # motivo del último fallo (estado HTTP o tipo de error); nunca contiene la clave


def enabled() -> bool:
    return os.environ.get("BANKREC_LLM") == "1" and bool(api_key())


def api_key() -> str:
    """Del entorno, o de la línea ANTHROPIC_API_KEY de `.env`. No lee ninguna otra variable del fichero."""
    key = os.environ.get(KEY_NAME, "").strip()
    if key:
        return key
    env = ROOT / ".env"
    try:
        for line in env.read_text(encoding="utf-8").splitlines():
            name, _, value = line.partition("=")
            if name.strip() == KEY_NAME:
                return value.strip().strip("'\"")
    except OSError:
        pass
    return ""


def _post(payload: dict, key: str) -> dict:
    req = urllib.request.Request(URL, data=json.dumps(payload).encode(), method="POST", headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:   # noqa: S310 - URL fija de la API
        return json.loads(resp.read())


def _cache_file(model: str, system: str, prompt: str) -> Path:
    digest = hashlib.sha256(json.dumps([model, system, prompt]).encode()).hexdigest()
    return CACHE / f"{digest}.json"


def _reason(err: Exception) -> str:
    """Estado HTTP y tipo de error de la API (`authentication_error`, `not_found_error`…); el mensaje se recorta."""
    if isinstance(err, urllib.error.HTTPError):
        try:
            body = json.loads(err.read()).get("error", {})
            return f"HTTP {err.code} {body.get('type', '')}: {str(body.get('message', ''))[:120]}".strip()
        except (ValueError, OSError, AttributeError):
            return f"HTTP {err.code}"
    return f"{type(err).__name__}: {str(err)[:120]}"


def ask(prompt: str, system: str = "", max_tokens: int = 400):
    """Texto de la respuesta, o None si está apagado, se agotó el tope o la llamada falló. Se cachea la respuesta."""
    global calls, last_error
    if not enabled():
        return None
    model = os.environ.get("BANKREC_LLM_MODEL", DEFAULT_MODEL)
    cache = _cache_file(model, system, prompt)
    try:
        return json.loads(cache.read_text(encoding="utf-8"))["text"]
    except (OSError, ValueError, KeyError):
        pass
    if calls >= int(os.environ.get("BANKREC_LLM_MAX_CALLS", "200")):
        return None
    payload = {"model": model, "max_tokens": max_tokens, "thinking": {"type": "disabled"}, "messages": [{"role": "user", "content": prompt}]}
    if system:
        payload["system"] = system
    for attempt in (0, 1):
        try:
            calls += 1
            data = _post(payload, api_key())
            text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
            if not text.strip():
                last_error = f"respuesta vacía (stop_reason={data.get('stop_reason')})"
                return None
            CACHE.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({"text": text}), encoding="utf-8")
            return text
        except urllib.error.HTTPError as err:
            last_error = _reason(err)
            if err.code not in (429, 500, 502, 503, 529) or attempt:
                return None
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as err:
            last_error = _reason(err)
            if attempt:
                return None
    return None


def ask_json(prompt: str, system: str = "", max_tokens: int = 400):
    """Como `ask`, pero espera un objeto JSON; devuelve None si la respuesta no lo es."""
    text = ask(prompt, system, max_tokens)
    if not text:
        return None
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None
