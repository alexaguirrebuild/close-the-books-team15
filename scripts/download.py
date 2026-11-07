"""Descarga los datos oficiales del hackathon y comprueba el sha256.

Uso: python scripts/download.py docs train-lite live [final]
Los enlaces y hashes salen de https://hackathon.usetalky.com/config.json.
Es la descarga oficial de la página Datos: no toca ninguna otra ruta de la plataforma.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tarfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

BASE = "https://hackathon.usetalky.com"
ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data"
UA = {"User-Agent": "team15-bankrec-downloader"}
NO_SHA_FLAG = "--sin-sha"
RANGE_NOT_SATISFIABLE, SERVER_ERROR = 416, 500
REQUIRED_AFTER_EXTRACT = {"final": ("final/bank_transactions.csv.gz", "final/open_items_at_start.csv.gz", "final/masters_final")}


class Refused(RuntimeError):
    """El servidor ha dicho que no: reintentar enseguida no cambia nada, y FINAL da 403 hasta que se publica."""


def config() -> dict:
    req = urllib.request.Request(f"{BASE}/config.json", headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(url: str, dest: Path, tries: int = 30) -> None:
    """Descarga reanudable (cabecera Range): si se corta, sigue donde iba."""
    t0 = time.time()
    for attempt in range(tries):
        have = dest.stat().st_size if dest.exists() else 0
        headers = dict(UA)
        if have:
            headers["Range"] = f"bytes={have}-"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=120) as r:
                if have and r.status != 206:
                    have = 0  # el servidor no admite Range: empezar de cero
                total = have + int(r.headers.get("Content-Length") or 0)
                done, last = have, 0.0
                with dest.open("ab" if have else "wb") as f:
                    while chunk := r.read(1 << 20):
                        f.write(chunk)
                        done += len(chunk)
                        if time.time() - last > 15:
                            rate = (done - have) / max(1.0, time.time() - t0) / 1e6
                            print(f"  {dest.name}: {100 * done / max(total, 1):.0f}% ({done >> 20} MB, {rate:.2f} MB/s)", flush=True)
                            last = time.time()
            print(f"  {dest.name}: {dest.stat().st_size >> 20} MB en {time.time() - t0:.0f}s", flush=True)
            return
        except urllib.error.HTTPError as ex:
            if ex.code == RANGE_NOT_SATISFIABLE and have:
                return  # Ya estaba entero: el sha256 decide si vale.
            if ex.code < SERVER_ERROR:
                raise Refused(f"el servidor responde {ex.code}") from ex
            print(f"  {dest.name}: error {ex.code}, reintento {attempt + 1}/{tries}", flush=True)
            time.sleep(5)
        except (TimeoutError, OSError) as ex:
            print(f"  {dest.name}: corte ({type(ex).__name__}), reintento {attempt + 1}/{tries}", flush=True)
            time.sleep(5)
    raise RuntimeError(f"no se pudo descargar {url}")


def extract(path: Path) -> None:
    if path.name.endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            z.extractall(OUT)
    else:
        with tarfile.open(path) as t:
            t.extractall(OUT, filter="data")
    print(f"  extraído {path.name}", flush=True)


def verified(i: str, d: dict, dest: Path) -> bool:
    """Un fichero con el sha256 mal se borra: si no, la siguiente descarga reanudaría sobre algo roto."""
    if not d["sha256"]:
        print(f"[{i}] sha256 SIN COMPROBAR: lo has pedido con {NO_SHA_FLAG}", flush=True)
        return True
    got = sha256(dest)
    if got != d["sha256"]:
        print(f"[{i}] SHA256 NO COINCIDE: {got} != {d['sha256']}. Fichero borrado: repite la descarga.", flush=True)
        dest.unlink()
        return False
    print(f"[{i}] sha256 OK", flush=True)
    return True


def download_one(i: str, d: dict, allow_no_sha: bool) -> bool:
    if not d.get("available"):
        print(f"[{i}] no disponible todavía", flush=True)
        return False
    if not d["sha256"] and not allow_no_sha:
        print(f"[{i}] config.json no trae sha256: no se puede comprobar. Repite en un minuto, o usa {NO_SHA_FLAG} solo si la organización lo confirma.", flush=True)
        return False
    dest = RAW / Path(d["url"]).name
    if d["sha256"] and dest.exists() and sha256(dest) == d["sha256"]:
        print(f"[{i}] ya estaba descargado", flush=True)
    else:
        print(f"[{i}] descargando {d['url']} ({d['size']})", flush=True)
        try:
            fetch(BASE + d["url"], dest)
        except RuntimeError as error:
            print(f"[{i}] {error}. Si acaba de publicarse, repite en un minuto.", flush=True)
            return False
    if not verified(i, d, dest):
        return False
    extract(dest)
    missing = [path for path in REQUIRED_AFTER_EXTRACT.get(i, ()) if not (OUT / path).exists()]
    if missing:
        print(f"[{i}] el archivo no tiene la forma esperada: falta {', '.join(missing)}", flush=True)
        return False
    return True


def main(ids: list[str], allow_no_sha: bool = False) -> int:
    RAW.mkdir(parents=True, exist_ok=True)
    try:
        cfg = config()
    except (OSError, ValueError) as error:
        print(f"No se puede leer config.json: {error}. Repite en un minuto.", flush=True)
        return 1
    dls = {d["id"]: d for d in cfg["downloads"]}
    results = [download_one(i, dls.get(i, {}), allow_no_sha) for i in ids]
    return 0 if all(results) else 1


if __name__ == "__main__":
    arguments = sys.argv[1:]
    wanted = [argument for argument in arguments if argument != NO_SHA_FLAG]
    sys.exit(main(wanted or ["docs", "train-lite", "live"], allow_no_sha=NO_SHA_FLAG in arguments))
