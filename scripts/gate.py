"""Puerta de merge del equipo 15: la regla de oro de AGENTS.md hecha código.

    python scripts/gate.py v-engine            # mide .worktrees/v-engine en sample y holdout y lo compara con out/base/
    python scripts/gate.py v-engine --reuse    # no vuelve a ejecutar el motor: usa los sub_*.csv.gz que ya hay en su out/
    python scripts/gate.py --set-base          # mide main (este checkout, limpio) y lo guarda como base en out/base/

PASA solo si se cumplen las tres cosas:
  1. la nota sube en sample Y en holdout,
  2. ningún segmento tiene más fallos que en la base, ni en sample ni en holdout,
  3. las líneas añadidas no traen IDs, nombres ni importes literales de los datos (cero sobreajuste).
La puntuación de la rama se calcula con el evaluador de main (vendor/bankrec_score.py), no con el código de la rama.
La puerta solo informa: el merge y los envíos los decide una persona.
"""
from __future__ import annotations

import ast
import csv
import gzip
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


def _main_root() -> Path:
    here = Path(__file__).resolve().parents[1]
    common = subprocess.run(["git", "-C", str(here), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                            capture_output=True, text=True).stdout.strip()
    return Path(common).parent if common else here


ROOT = _main_root()
DATA = ROOT / "data"
BASE = ROOT / "out" / "base"
PHASES = ("sample", "holdout")
EXC = ["PARTIAL", "OVERPAYMENT", "SHORT_DISCOUNT", "SHORT_BANK_CHARGES", "SHORT_WITHHOLDING", "SHORT_DISPUTE",
       "DUPLICATE_PAYMENT", "UNIDENTIFIED", "RETURNED", "FX_DIFFERENCE"]
EXC_RE = re.compile(r"^([A-Z_]+)\s+(\d+)\s+(\d+)\s+(\d+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*$")


def git(*args, cwd=ROOT) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout.strip()


def env_for(out: Path) -> dict:
    e = os.environ.copy()
    e.update(PYTHONHASHSEED="0", PYTHONIOENCODING="utf-8", BANKREC_DATA=str(DATA), BANKREC_OUT=str(out))
    return e


def run_engine(cwd: Path, out: Path, phase: str, score: bool) -> None:
    out.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "bankrec.run", "--phase", phase] + ([] if score else ["--no-score"])
    print(f"  · {cwd.name} {phase}: ejecutando el motor…", flush=True)
    with open(out / f"gate_run_{phase}.log", "w", encoding="utf-8") as log:
        r = subprocess.run(cmd, cwd=cwd, env=env_for(out), stdout=log, stderr=subprocess.STDOUT)
    if r.returncode:
        sys.exit(f"ERROR: el motor falló en {cwd.name} {phase} (ver {out / f'gate_run_{phase}.log'})")


def excf1(explain: Path, phase: str) -> dict:
    """F1 por clase con el excf1 de main sobre el explain que se le pase."""
    r = subprocess.run([sys.executable, "-m", "bankrec.excf1", "--phase", phase, "--file", str(explain)],
                       cwd=ROOT, env=env_for(ROOT / "out"), capture_output=True, text=True, encoding="utf-8")
    return parse_excf1(r.stdout)


def parse_excf1(text: str) -> dict:
    res = {}
    for line in text.splitlines():
        m = EXC_RE.match(line)
        if m and m.group(1) in EXC:
            gold, tp, fp = (int(m.group(i)) for i in (2, 3, 4))
            res[m.group(1)] = {"gold": gold, "tp": tp, "fp": fp, "prec": float(m.group(5)),
                               "rec": float(m.group(6)), "f1": float(m.group(7))}
    return res


def score_file(sub: Path, phase: str) -> dict:
    sys.path.insert(0, str(ROOT / "vendor"))
    import bankrec_score as S  # evaluador oficial de main

    cats = [r["category"] for r in csv.DictReader(open(DATA / "masters" / "categories.csv", encoding="utf-8-sig"))]
    gold = BASE / "_golden_holdout.csv.gz" if phase == "holdout" else DATA / "sample" / "golden_allocations.csv.gz"
    g = S.load_golden(str(gold), str(BASE / f"_txns_{phase}.csv.gz"), EXC, cats)
    res = S.score_submission(g, sub.read_bytes(), str(sub))
    if not res["ok"]:
        sys.exit(f"ERROR: {sub} no valida: {res['errors'][:3]}")
    return res


def fails(seg: dict) -> int:
    return int(round(seg["n"] * (1 - seg["accuracy"])))


# ------------------------------------------------------------------ sobreajuste
ID_RE = re.compile(r"\b(?:BT|AR[IC]|AP[IC])-\d{4}-\d{4,}\b"        # movimientos y partidas
                   r"|\b[CS][A-Z]{2}\d{8}\b|\bG[A-Z]{2}\d{6}\b"      # clientes, proveedores y grupos
                   r"|\b[A-Z]{2}\d{2}[A-Z0-9]{12,30}\b")              # IBAN
AMT_RE = re.compile(r"(?<![\w.])(\d{2,})\.(\d{2})(?![\d.])")
TOK_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-/]{5,}")
WORD_RE = re.compile(r"[^\W\d_]{6,}")
ID_COLS = ("item_id", "document_number", "supplier_invoice_number", "customer_po", "our_po", "related_document",
           "bank_txn_id", "ref1", "ref2", "end_to_end_id", "mandate_id", "sepa_mandate_id", "tax_id", "iban",
           "customer_id", "supplier_id", "counterparty_account")


def added_lines(wt: Path):
    """(fichero, línea, texto) de lo que la rama añade respecto a su punto de partida con main, commits y árbol."""
    mb = git("merge-base", "main", "HEAD", cwd=wt)
    diff = git("diff", "-U0", mb, "--", ".", ":(exclude)*.md", ":(exclude)docs/**", cwd=wt)
    f, n = None, 0
    for line in diff.splitlines():
        if line.startswith("+++ "):
            f = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@"):
            n = int(re.search(r"\+(\d+)", line).group(1))
        elif line.startswith("+") and f:
            yield f, n, line[1:]
            n += 1
    for f in git("ls-files", "--others", "--exclude-standard", cwd=wt).splitlines():
        if f.endswith(".md") or f.startswith("docs/"):
            continue
        try:
            for i, line in enumerate((wt / f).read_text(encoding="utf-8").splitlines(), 1):
                yield f, i, line
        except (UnicodeDecodeError, OSError):
            yield f, 0, "<fichero binario o ilegible añadido>"


def known_ids(tokens: set) -> dict:
    """Busca los tokens candidatos en los datos de sample y train (solo se carga si hay candidatos)."""
    hit = {}
    files = [DATA / "masters" / "customers.csv", DATA / "masters" / "suppliers.csv"]
    for ph in ("sample", "train"):
        files += [DATA / ph / f for f in ("ar_invoices.csv.gz", "ap_invoices.csv.gz", "credit_notes.csv.gz",
                                          "bank_transactions.csv.gz", "golden_allocations.csv.gz")]
    for p in files:
        if not p.exists():
            continue
        fh = gzip.open(p, "rt", encoding="utf-8", newline="") if p.suffix == ".gz" else open(p, encoding="utf-8-sig")
        with fh:
            rd = csv.DictReader(fh)
            cols = [c for c in rd.fieldnames or [] if c in ID_COLS]
            for r in rd:
                for c in cols:
                    v = r[c]
                    if v in tokens and v not in hit:
                        hit[v] = f"{p.parent.name}/{p.name}:{c}"
        if len(hit) == len(tokens):
            break
    return hit


def names() -> tuple[set, set]:
    full, words = set(), {}
    for f, cols in (("customers.csv", ("name", "trade_name")), ("suppliers.csv", ("name",))):
        p = DATA / "masters" / f
        if not p.exists():
            continue
        for r in csv.DictReader(open(p, encoding="utf-8-sig")):
            for c in cols:
                v = (r.get(c) or "").strip()
                if len(v) >= 8:
                    full.add(v.casefold())
                    for w in set(WORD_RE.findall(v.casefold())):
                        words[w] = words.get(w, 0) + 1
    rare = {w for w, k in words.items() if k <= 2}
    return full, rare


def doc_lines(wt: Path, f: str, cache={}) -> set:
    """Líneas de docstrings y cadenas sueltas: un ejemplo de formato ahí documenta, no decide."""
    key = (wt, f)
    if key not in cache:
        cache[key] = set()
        if f.endswith(".py"):
            try:
                tree = ast.parse((wt / f).read_text(encoding="utf-8"))
            except (SyntaxError, OSError, UnicodeDecodeError):
                return cache[key]
            for node in ast.walk(tree):
                if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    cache[key].update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    return cache[key]


def overfit_scan(wt: Path) -> tuple[list, list]:
    """Devuelve (bloqueantes, avisos)."""
    lines = list(added_lines(wt))
    block, warn, cand = [], [], {}
    full, rare = names()
    for f, n, text in lines:
        code = text.split("#", 1)[0]
        where = f"{f}:{n}"
        in_code = bool(code.strip()) and n not in doc_lines(wt, f)
        target = block if in_code else warn
        for m in ID_RE.finditer(text):
            target.append(f"{where} ID literal {m.group(0)!r}")
        for m in AMT_RE.finditer(text):
            if m.group(2) != "00":
                target.append(f"{where} importe literal {m.group(0)}")
            else:
                warn.append(f"{where} importe redondo {m.group(0)} (¿umbral?)")
        low = text.casefold()
        for nm in full:
            if nm in low:
                target.append(f"{where} nombre de contraparte {nm!r}")
        quoted = " ".join(re.findall(r"[\"']([^\"']*)[\"']", text)).casefold()
        for w in set(WORD_RE.findall(quoted)) & rare:
            warn.append(f"{where} palabra rara de un nombre de los maestros {w!r}")
        for tok in TOK_RE.findall(text):
            if sum(ch.isdigit() for ch in tok) >= 4:
                cand.setdefault(tok, []).append((where, in_code))
    if cand:
        for tok, src in known_ids(set(cand)).items():
            for where, in_code in cand[tok]:
                (block if in_code else warn).append(f"{where} {tok!r} existe en los datos ({src})")
    return sorted(set(block)), sorted(set(warn))


# ------------------------------------------------------------------ base
def set_base() -> None:
    if git("rev-parse", "--abbrev-ref", "HEAD") != "main" or git("status", "--porcelain", "--", "bankrec", "vendor"):
        sys.exit("ERROR: la base se mide en main con bankrec/ y vendor/ limpios")
    BASE.mkdir(parents=True, exist_ok=True)
    out = ROOT / "out"
    for ph in PHASES:
        run_engine(ROOT, out, ph, score=True)
        shutil.copy(out / "_txns.csv.gz", BASE / f"_txns_{ph}.csv.gz")
        if ph == "holdout":
            shutil.copy(out / "_golden_holdout.csv.gz", BASE / "_golden_holdout.csv.gz")
        shutil.copy(out / f"score_{ph}.json", BASE / f"score_{ph}.json")
        r = subprocess.run([sys.executable, "-m", "bankrec.excf1", "--phase", ph], cwd=ROOT, env=env_for(out),
                           capture_output=True, text=True, encoding="utf-8")
        (BASE / f"excf1_{ph}.log").write_text(r.stdout, encoding="utf-8")
    (BASE / "SHA").write_text(git("rev-parse", "HEAD") + "\n", encoding="utf-8")
    print(f"base guardada en {BASE} @ {git('rev-parse', '--short', 'HEAD')}")


# ------------------------------------------------------------------ juicio
def judge(branch: str, reuse: bool) -> int:
    wt = ROOT / ".worktrees" / branch
    if not wt.exists():
        sys.exit(f"ERROR: no existe {wt}. Crea el worktree: git worktree add -B {branch} .worktrees/{branch} main")
    for f in [BASE / "SHA", BASE / "_golden_holdout.csv.gz"] + [BASE / f"{k}_{p}.{e}" for p in PHASES
                                                                  for k, e in (("score", "json"), ("_txns", "csv.gz"))]:
        if not f.exists():
            sys.exit(f"ERROR: falta {f}. Mide la base: python scripts/gate.py --set-base")
    base_sha = (BASE / "SHA").read_text().strip()
    reasons, notes = [], []
    if git("rev-parse", "main") != base_sha:
        notes.append(f"la base es de {base_sha[:7]} pero main está en {git('rev-parse', '--short', 'main')}: "
                     "vuelve a medirla con --set-base")
    mb = git("merge-base", "main", "HEAD", cwd=wt)
    if mb != git("rev-parse", "main"):
        reasons.append(f"la rama parte de {mb[:7]}, no del main actual: rebase sobre main y repite")
    if git("status", "--porcelain", "--", "bankrec", cwd=wt):
        notes.append("hay cambios sin commit en bankrec/: se mide el árbol, no el último commit")
    commits = git("log", "--oneline", "main..HEAD", cwd=wt)
    print(f"== {branch} @ {git('rev-parse', '--short', 'HEAD', cwd=wt)} · {len(commits.splitlines())} commit(s) sobre main")
    out = wt / "out"
    res = {}
    for ph in PHASES:
        sub = out / f"sub_{ph}.csv.gz"
        if not reuse or not sub.exists():
            run_engine(wt, out, ph, score=False)
        cand = score_file(sub, ph)
        base = json.loads((BASE / f"score_{ph}.json").read_text(encoding="utf-8"))
        ce = excf1(out / f"explain_{ph}.csv.gz", ph)
        be = parse_excf1((BASE / f"excf1_{ph}.log").read_text(encoding="utf-8")) if (BASE / f"excf1_{ph}.log").exists() else {}
        (out / f"gate_score_{ph}.json").write_text(json.dumps(cand, indent=1, default=str), encoding="utf-8")
        res[ph] = (base, cand, be, ce)
        d = cand["score"] - base["score"]
        bm, cm = base["metrics"], cand["metrics"]
        print(f"\n[{ph}] nota {base['score']:.2f} -> {cand['score']:.2f} ({d:+.2f})   "
              + "  ".join(f"{k} {bm[k]:.4f}->{cm[k]:.4f}" for k in ("allocation_f1", "txn_accuracy", "exception_macro_f1")))
        if d <= 0:
            reasons.append(f"{ph}: la nota no sube ({d:+.2f})")
        worse, better = [], []
        for seg in sorted(set(base["segments"]) | set(cand["segments"])):
            fb = fails(base["segments"].get(seg, {"n": 0, "accuracy": 1}))
            fc = fails(cand["segments"].get(seg, {"n": 0, "accuracy": 1}))
            (worse if fc > fb else better if fc < fb else []).append((seg, fb, fc))
        for seg, fb, fc in sorted(worse, key=lambda x: x[1] - x[2]):
            print(f"   EMPEORA {seg:28s} fallos {fb} -> {fc} (+{fc - fb})")
            reasons.append(f"{ph}: el segmento {seg} pasa de {fb} a {fc} fallos")
        for seg, fb, fc in sorted(better, key=lambda x: x[2] - x[1])[:8]:
            print(f"   mejora  {seg:28s} fallos {fb} -> {fc} ({fc - fb})")
        for c in EXC:
            if c in ce or c in be:
                fb_, fc_ = be.get(c, {}).get("f1", 0.0), ce.get(c, {}).get("f1", 0.0)
                if abs(fc_ - fb_) >= 0.005:
                    print(f"   exc {c:20s} F1 {fb_:.2f} -> {fc_:.2f} ({2.0 * (fc_ - fb_):+.2f} pts máx.)")
    block, warn = overfit_scan(wt)
    print(f"\n[sobreajuste] {len(block)} bloqueante(s), {len(warn)} aviso(s)")
    for b in block[:30]:
        print("   BLOQUEA", b)
    for w in warn[:15]:
        print("   aviso  ", w)
    reasons += [f"literal: {b}" for b in block]
    for n in notes:
        print("   nota:", n)
    print("\nVEREDICTO:", "PASA" if not reasons else "FALLA")
    for r in reasons[:20]:
        print("   -", r)
    return 0 if not reasons else 1


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    argv = sys.argv[1:] if argv is None else argv
    if "--set-base" in argv:
        set_base()
        return 0
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 1:
        print(__doc__)
        return 2
    return judge(args[0], "--reuse" in argv)


if __name__ == "__main__":
    sys.exit(main())
