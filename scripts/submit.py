"""Envío oficial a la plataforma (el mismo flujo de la página Enviar), solo con librería estándar.

    python scripts/submit.py LIVE out/sub_live.csv.gz            # pide confirmación
    python scripts/submit.py FINAL out/sub_final.csv.gz          # pide escribir FINAL: solo hay UN envío

Antes de subir valida el fichero en local con el evaluador oficial (ids de la fase, duplicados, formato).
Pedir la URL de subida ya gasta un hueco (20/día por fase, 1 cada 180 s): no se envía por probar.
El token sale de la variable de entorno HK_TOKEN o del fichero .env (que no se sube a git).
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = os.environ.get("HK_API", "https://hackathon.usetalky.com/api")
FINAL_STATES = {"scored", "received", "invalid", "error"}
POLL_SECONDS = 5  # como mucho cada 5 s: toda la sala comparte IP
POLL_DEADLINE_SECONDS = 600
SERVER_ERROR = 500


def require_https(url: str) -> None:
    """HK_API se puede sobrescribir: el token nunca sale sin cifrar."""
    if urllib.parse.urlsplit(url).scheme != "https":
        sys.exit(f"HK_API debe usar https: {url}")


def token() -> str:
    tok = os.environ.get("HK_TOKEN", "")
    env = ROOT / ".env"
    if not tok and env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("HK_TOKEN="):
                tok = line.split("=", 1)[1].strip()
    if not tok:
        sys.exit("Falta HK_TOKEN (en .env o en el entorno).")
    return tok


def local_validate(phase: str, path: Path) -> None:
    sys.path.insert(0, str(ROOT / "vendor"))
    import bankrec_score as S

    folder = ROOT / "data" / phase.lower()
    cats = [l.split(",")[0] for l in (ROOT / "data" / "masters" / "categories.csv").read_text(encoding="utf-8").splitlines()[1:]]
    exc = ["PARTIAL", "OVERPAYMENT", "SHORT_DISCOUNT", "SHORT_BANK_CHARGES", "SHORT_WITHHOLDING", "SHORT_DISPUTE",
           "DUPLICATE_PAYMENT", "UNIDENTIFIED", "RETURNED", "FX_DIFFERENCE"]
    empty = ROOT / "out" / "_empty_golden.csv.gz"
    empty.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(empty, "wt") as g:
        g.write("bank_txn_id,item_id,amount,category,exception\n")
    gold = S.load_golden(str(empty), str(folder / "bank_transactions.csv.gz"), exc, cats)
    res = S.score_submission(gold, path.read_bytes(), path.name)
    if not res["ok"]:
        print(json.dumps(res["errors"][:20], indent=1, ensure_ascii=False))
        sys.exit("El fichero NO pasa la validación local. No se envía.")
    m = res["metrics"]
    print(f"Validación local OK · filas={m.get('rows')} · movimientos cubiertos={m.get('txns_covered')}/{m.get('txns_total')}")
    if m.get("txns_covered") != m.get("txns_total"):
        sys.exit("Faltan movimientos de la fase. No se envía.")


def call(method, url, tok=None, body=None, timeout=60):
    if tok:
        require_https(url)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read() or b"{}")


def s3_upload(url, fields, name, payload):
    b = uuid.uuid4().hex
    buf = io.BytesIO()
    for k, v in fields.items():  # los campos primero, el fichero el último
        buf.write(f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    buf.write(f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
              f"Content-Type: application/octet-stream\r\n\r\n".encode())
    buf.write(payload)
    buf.write(f"\r\n--{b}--\r\n".encode())
    req = urllib.request.Request(url, data=buf.getvalue(), method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={b}")
    with urllib.request.urlopen(req, timeout=900) as r:
        return r.status


def strict_validate(phase: str, path: Path) -> None:
    """Además del evaluador oficial: cobertura total, partidas que existen, 2 decimales. Ver pipeline/README.md."""
    command = ["uv", "run", "--project", str(ROOT / "pipeline"), "bankcheck", "validate", "--phase", phase.lower(), str(path.resolve())]
    if subprocess.run(command, cwd=ROOT).returncode != 0:
        sys.exit("El validador estricto rechaza el fichero. No se envía.")


def describe(path: Path) -> str:
    """Lo último que ve quien confirma: en FINAL no hay segunda oportunidad y un fichero antiguo también valida."""
    data = path.read_bytes()
    stat = path.stat()
    modified = time.strftime("%H:%M:%S del %d/%m", time.localtime(stat.st_mtime))
    return f"Fichero: {path.resolve()}\n  {stat.st_size / 1e6:.1f} MB · modificado a las {modified} · SHA-256 {hashlib.sha256(data).hexdigest()}"


def upload_problem(up: dict, path: Path) -> str | None:
    """Sube el fichero y devuelve qué ha fallado. Sin excepciones: el hueco ya está gastado y hay que dejar registro."""
    try:
        st = s3_upload(up["upload"]["url"], up["upload"]["fields"], path.name, path.read_bytes())
    except OSError as error:
        return str(error)
    return None if st in (200, 201, 204) else f"estado {st}"


def wait_for_score(sub_id: str, tok: str) -> dict:
    """Pide la nota y sondea hasta el plazo. Con el hueco gastado, un fallo de red o un 5xx se reintenta, no tira el script.

    Un 4xx al pedir la nota no se repite: quizá la petición anterior llegó y se perdió la respuesta, y el estado lo aclara."""
    deadline = time.time() + POLL_DEADLINE_SECONDS
    sub: dict = {}
    requested = False
    while True:
        try:
            if requested:
                _, sub = call("GET", f"{API}/submissions/{sub_id}", tok)
            else:
                _, sub = call("POST", f"{API}/submissions/{sub_id}/score", tok, {})
                requested = True
        except urllib.error.HTTPError as error:
            requested = requested or error.code < SERVER_ERROR
            print(f"Consulta fallida, se reintenta: {error}", file=sys.stderr, flush=True)
        except (OSError, ValueError) as error:
            print(f"Consulta fallida, se reintenta: {error}", file=sys.stderr, flush=True)
        if sub.get("status") in FINAL_STATES or time.time() >= deadline:
            return sub
        time.sleep(POLL_SECONDS)


def record_submission(phase: str, path: Path, sub_id: str, sub: dict) -> None:
    log = ROOT / "out" / "envios.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"t": time.strftime("%H:%M:%S"), "phase": phase, "file": path.name, "sub_id": sub_id,
                            "status": sub.get("status"), "score": sub.get("score")}) + "\n")


def request_upload(phase: str, path: Path, tok: str) -> dict:
    """Sin reintentos: cada petición gasta un hueco, y en FINAL solo hay uno."""
    try:
        st, up = call("POST", f"{API}/submissions/upload-url", tok, {"phase": phase, "filename": path.name})
    except (OSError, ValueError) as error:
        sys.exit(f"upload-url falló: {error}. Mira en la plataforma si ha gastado hueco antes de repetir.")
    if st != 201:
        sys.exit(f"upload-url falló: {st} {up}")
    return up


def main(phase: str, path_s: str) -> None:
    phase = phase.upper()
    path = Path(path_s)
    if phase not in ("LIVE", "FINAL"):
        sys.exit("Fase: LIVE o FINAL")
    require_https(API)
    tok = token()
    local_validate(phase, path)
    strict_validate(phase, path)
    print(describe(path))
    ok = input(f"Vas a gastar un envío {phase} con {path.name}. Escribe {'FINAL' if phase == 'FINAL' else 'si'}: ")
    if ok.strip() != ("FINAL" if phase == "FINAL" else "si"):
        sys.exit("Cancelado.")
    up = request_upload(phase, path, tok)
    sub_id = up["sub_id"]
    print(f"Hueco gastado. sub_id={sub_id}", flush=True)
    problem = upload_problem(up, path)
    sub = {"status": "upload_failed", "detail": problem} if problem else wait_for_score(sub_id, tok)
    print(json.dumps(sub, indent=2, ensure_ascii=False))
    record_submission(phase, path, sub_id, sub)
    if problem:
        sys.exit(f"La subida falló ({problem}). sub_id={sub_id}. Mira su estado en la plataforma antes de repetir.")
    if sub.get("status") not in FINAL_STATES:
        sys.exit(f"Sin estado final tras {POLL_DEADLINE_SECONDS} s. sub_id={sub_id}. Míralo en la plataforma: no reenvíes a ciegas.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
