# Equipo 15 · Close the Books

Conciliación bancaria explicable para Grupo Valtermia (Claude Code Hackathon, La Nave, 7-nov-2026).

## Arrancar en 2 minutos

```
git clone <este repo> && cd close-the-books-team15
python scripts/download.py live train-lite      # o copia data/ de un compañero (pendrive) si el WiFi va lento
python -m bankrec.run --phase sample            # genera y puntúa con el evaluador oficial
```

Python 3.11 o superior, sin instalar nada. Opcional para PDF y xlsx: `pip install pypdf openpyxl`.

## Dónde está cada cosa

- `AGENTS.md`: reglas del proyecto para personas y agentes. **Léelo primero.**
- `docs/PLAN_DIA.md`: horario, reparto, protocolo de envíos y checklist FINAL.
- `docs/HERRAMIENTAS.md`: Claude, Codex y Antigravity, y quién hace qué.
- `docs/POLICIES.md`: las reglas con las que se etiquetó. Mandan sobre todo lo demás.
- `bankrec/`: el motor. `python -m bankrec.diff --segment <seg>` es la herramienta de QA.
- `scripts/submit.py`: el único camino para enviar. Valida en local y pide confirmación.

## Seguridad

El token del equipo va **solo** en `.env` (copia `.env.example`). Antes de cada commit pasa gitleaks:

```
git config core.hooksPath .githooks
```
