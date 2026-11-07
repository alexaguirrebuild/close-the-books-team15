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
import urllib.request
import zipfile
from pathlib import Path

BASE = "https://hackathon.usetalky.com"
ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data"
UA = {"User-Agent": "team15-bankrec-downloader"}


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


def main(ids: list[str]) -> int:
    RAW.mkdir(parents=True, exist_ok=True)
    cfg = config()
    dls = {d["id"]: d for d in cfg["downloads"]}
    ok = True
    for i in ids:
        d = dls.get(i)
        if not d or not d.get("available"):
            print(f"[{i}] no disponible todavía", flush=True)
            ok = False
            continue
        dest = RAW / Path(d["url"]).name
        if dest.exists() and d["sha256"] and sha256(dest) == d["sha256"]:
            print(f"[{i}] ya estaba descargado", flush=True)
        else:
            print(f"[{i}] descargando {d['url']} ({d['size']})", flush=True)
            fetch(BASE + d["url"], dest)
        got = sha256(dest)
        if d["sha256"] and got != d["sha256"]:
            print(f"[{i}] SHA256 NO COINCIDE: {got} != {d['sha256']}", flush=True)
            ok = False
            continue
        print(f"[{i}] sha256 OK", flush=True)
        extract(dest)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["docs", "train-lite", "live"]))
