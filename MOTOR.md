# Motor `bankrec` · `bankrec` engine

**English below.**

## Español

### Qué es esto

Es el motor de conciliación que llevamos desde el viernes en otro repo (`close-the-books-team15`), traído aquí para trabajar todos desde el repo de Pablo. Lee los movimientos del banco, decide qué facturas o abonos liquida cada uno y con qué importe, pone la categoría o la excepción, y genera el fichero de envío en el formato oficial. Con Python 3.11 o superior y la librería estándar ya funciona; `pypdf` y `openpyxl` son opcionales para leer los PDF y los xlsx.

### Cómo va

| Medición | Nota |
|---|---|
| Holdout rápido (ene-jun 2025, arranca con los mismos saldos que LIVE) | **82,33** · F1 0,860 · acierto por movimiento 0,867 · excepciones 0,667 |
| Sample (jun-2025) | 83,77 |
| Fichero LIVE | valida en local: 203.083/203.083 movimientos, unos 8 min |
| Envíos LIVE | ninguno todavía |

El plan del repo daba ~61 para «reglas bien afinadas». Ya estamos por encima, así que no hace falta construir el pipeline desde cero: la mayor parte de los pasos 1 a 4 y 7 de `PLAN.md` ya existen y están medidos.

### Cómo encaja con `PLAN.md`

| Paso de `PLAN.md` | En el motor |
|---|---|
| 1. Cargador y saldos abiertos | `bankrec/data.py` |
| 2. Terceros (nombre truncado, IBAN) | `bankrec/data.py` (índices) y `engine.py` |
| 3. Lotes: remesas de pago, adeudos, tarjetas, avisos de pago, devoluciones | `engine.py` y el plugin `bankrec/remittance.py` |
| 4. Solucionador por movimiento (número impreso, importe, FIFO, 1:N, abonos, descuento, retención, comisiones, FX, parcial) | `engine.py` (`settle()` aplica POLICIES §2-§3) |
| 5. Movimientos sin partida (category, UNIDENTIFIED, duplicados) | reglas en `engine.py`. **Aquí está el margen** |
| 6. Capa de Claude para casos residuales | **no existe todavía**. Los PDF se leen con reglas (`bankrec/pdfclauses.py`) |
| 7. Validador y envío | `scripts/submit.py`, que valida con el evaluador oficial (`vendor/`) antes de gastar un hueco |

Cada fila guarda su regla y su evidencia en `out/explain_<fase>.csv.gz`. Es el porqué de cada asignación, y le sirve al jurado.

### Dónde se pierden los puntos ahora

- `UNIDENTIFIED`: precisión 0,26, con 11.764 falsos positivos en movimientos que no tienen partida.
- `SHORT_BANK_CHARGES`: precisión 0,25. `DUPLICATE_PAYMENT`: 0,54.
- Las excepciones pesan un 20 % y las 10 clases cuentan igual. Es lo más barato de subir.

### Arrancar

```
python scripts/download.py docs train-lite live              # datos a data/ (nunca se suben)
python -m bankrec.run --phase holdout                         # referencia para iterar, ~16 min
python -m bankrec.diff --phase holdout --segment <seg> -n 5   # fallos de un segmento
python -m bankrec.run --phase live --no-score                 # genera out/sub_live.csv.gz (no envía)
python scripts/submit.py LIVE out/sub_live.csv.gz              # el único camino para enviar
```

Las reglas de trabajo están en `AGENTS.md` (la leen Claude, Codex y Antigravity). El estado del día está en `docs/TRASPASO.md`; quien termine algo lo actualiza.

### Sobre `cuadra/`

Es para revisar fallos a mano y para la demo del vídeo, **no para generar el envío**: en LIVE y FINAL no produce ningún fichero, y la nota sale del motor. Lee la salida del motor, y su test con datos reales comprueba que nota y segmentos coinciden con el evaluador oficial. Más detalle en `cuadra/README.md`.

### Por confirmar entre todos

- **Límite de envíos LIVE.** `PLAN.md` dice 1 por minuto y 100 al día; nuestro plan, 1 cada 180 s y 20 al día. Ningún documento oficial lo dice: hay que mirarlo en la web. Hasta entonces, el más estricto.
- **Horario.** Propuesta: congelar el código a las 16:30, prueba completa en LIVE a las 17:00 y envío FINAL a las 18:45.

### Reparto propuesto

- **Pablo:** motor (`engine.py`) y FINAL.
- **Una persona:** movimientos sin partida, con clasificador entrenado con TRAIN o capa de Claude. Es el paso 5 o 6 del plan y donde más puntos hay.
- **Otra persona:** un segmento cada vez con `bankrec.diff`, y la regla citando POLICIES.
- **Alex:** envíos, vídeo y presentación.

Cada uno trabaja en su rama o en su propio plugin (`bankrec/<nombre>.py`) para no pisarse. Un cambio entra solo si sube el holdout.

---

## English

### What this is

This is the reconciliation engine we have been building since Friday in another repo (`close-the-books-team15`), brought here so we all work from Pablo's repo. It reads the bank lines, decides which invoices or credit notes each one settles and for how much, sets the category or the exception, and writes the submission file in the official format. It runs on Python 3.11+ with the standard library only; `pypdf` and `openpyxl` are optional, for PDFs and xlsx files.

### Where it stands

| Measurement | Score |
|---|---|
| Fast holdout (Jan-Jun 2025, starts from the same balances as LIVE) | **82.33** · F1 0.860 · transaction accuracy 0.867 · exceptions 0.667 |
| Sample (Jun 2025) | 83.77 |
| LIVE file | passes local validation: 203,083/203,083 bank lines, about 8 min |
| LIVE submissions | none yet |

The repo plan puts "well-tuned rules" at ~61. We are already above that, so there is no need to build the pipeline from scratch: most of steps 1-4 and 7 in `PLAN.md` already exist and are measured.

### How it maps to `PLAN.md`

| `PLAN.md` step | In the engine |
|---|---|
| 1. Loader and open-item ledger | `bankrec/data.py` |
| 2. Party resolution (truncated name, IBAN) | `bankrec/data.py` (indexes) and `engine.py` |
| 3. Batches: payment runs, direct debits, cards, remittance advices, returns | `engine.py` and the `bankrec/remittance.py` plugin |
| 4. Per-line solver (printed number, amount, FIFO, 1:N, credit notes, discount, withholding, charges, FX, partial) | `engine.py` (`settle()` implements POLICIES §2-§3) |
| 5. No-item lines (category, UNIDENTIFIED, duplicates) | rules in `engine.py`. **This is where the headroom is** |
| 6. Claude layer for residual cases | **not built yet**. PDFs are read with rules (`bankrec/pdfclauses.py`) |
| 7. Validator and upload | `scripts/submit.py`, which validates with the official scorer (`vendor/`) before using a slot |

Every row stores its rule and evidence in `out/explain_<phase>.csv.gz`: the reason behind each allocation, which is useful for the judges.

### Where we lose points now

- `UNIDENTIFIED`: precision 0.26, with 11,764 false positives on lines that have no item.
- `SHORT_BANK_CHARGES`: precision 0.25. `DUPLICATE_PAYMENT`: 0.54.
- Exceptions weigh 20 % and all 10 classes count the same. It is the cheapest place to gain.

### Getting started

```
python scripts/download.py docs train-lite live              # data into data/ (never committed)
python -m bankrec.run --phase holdout                         # reference for iterating, ~16 min
python -m bankrec.diff --phase holdout --segment <seg> -n 5   # errors for one segment
python -m bankrec.run --phase live --no-score                 # writes out/sub_live.csv.gz (does not upload)
python scripts/submit.py LIVE out/sub_live.csv.gz              # the only way to upload
```

Working rules are in `AGENTS.md` (read by Claude, Codex and Antigravity; written in Spanish). The state of the day is in `docs/TRASPASO.md`; whoever finishes something updates it.

### About `cuadra/`

It is for reviewing failures by hand and for the video demo, **not for generating the submission**: in LIVE and FINAL it produces no file, and the score comes from the engine. It reads the engine output, and its real-data test checks that score and segments match the official scorer. More in `cuadra/README.md`.

### To agree on

- **LIVE submission limit.** `PLAN.md` says 1 per minute and 100 per day; our plan says 1 every 180 s and 20 per day. No official doc states it: check the website. Until then, assume the stricter one.
- **Schedule.** Proposal: code freeze at 16:30, full LIVE dry run at 17:00, FINAL submission at 18:45.

### Proposed split

- **Pablo:** engine (`engine.py`) and FINAL.
- **One person:** no-item lines, with a classifier trained on TRAIN or a Claude layer. That is step 5 or 6 of the plan and where most points are.
- **Another person:** one segment at a time with `bankrec.diff`, and the rule citing POLICIES.
- **Alex:** submissions, video and presentation.

Everyone works on their own branch or plugin (`bankrec/<name>.py`) so nobody steps on anyone else. A change only goes in if the holdout goes up.
