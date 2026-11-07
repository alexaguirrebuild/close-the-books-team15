# Plan: Alex, bloque C (Excepciones e importes) en el repo de Pablo + auditoría de LIVE

## Contexto

- **Dónde se integra todo:** en `origin/Dev` de `CAP-Shinkei/Paella_Hackathon`. Holdout **89,26**, LIVE **88,63** (`docs/TRASPASO.md` de Dev, 13:20).
- **Nuestro repo `papialex12/close-the-books-team15` está superado.** Su holdout es 82,33. Lo que hice hoy (`v-pablo`, que también subí como `team15-dev`) ya está en Dev: proforma con `it=None`, `issue or dt.date.min`, `net_credit` por `c.open`, desempate por id en `resolve_names` y el endurecimiento de `submit.py`. Comprobado con `git show pablo/Dev:...`.
- **Reparto de Pablo** (WhatsApp y su artifact): a Alex le toca el **bloque C**, Excepciones e importes, hasta el vídeo de las 16:00.

  | Segmento | Máx. | Síntoma (medido sobre 86,83) |
  |---|---|---|
  | `ar_overpayment` | +0,89 | 400 líneas, 40,5 % bien, 207 → UNIDENTIFIED |
  | `ar_short_discount` | +0,52 | 769 → UNIDENTIFIED |
  | `ar_discount_late` | +0,17 | — |
  | `ar_short_withholding` | +0,33 | 898 perdidas |
  | `ar_partial` | +0,32 | 880 → UNIDENTIFIED |

  Equivale al lote 1 «Cobros cortos» del TRASPASO: solo se tocan `bankrec/cobros_cortos.py` y `tests/test_cobros_cortos.py`, sin editar `engine.py`.
- **Codex:** el de `v-parsers` no aportó nada (+0,01 con pypdf). El de `v-engine` sí dejó material de este bloque: el commit `9675d4c` (+109 líneas en `engine.py` y +154 en tests), más trabajo sin commitear y `docs/EXCEPTION_DIAGNOSIS.md`. Incluye sobrepago con una sola factura abierta, descuento agregado con plazo y retenciones y descuentos repartidos entre varias facturas. Está escrito sobre el motor viejo, medido sin pypdf (sample 79,92 → 80,79) y nunca pasó por holdout. Sirve como **propuesta de reglas**, no para meterlo tal cual.
- **El análisis de Antigravity está desfasado.**
  - Sus cifras son del motor de 82,33.
  - `out/sub_live.csv.gz` no existe en esta máquina y la ruta `$HOME\close-the-books-team15` es incorrecta.
  - Su «Acción B.1» ya está en Dev.
  - El relleno FIFO de `ar_ref_noise` va contra lo que ya se midió («asignar sin evidencia exacta baja la nota»: 82,33 → 81,49) y además es bloque de Fonti.
  - Lo de los equipos con 93–95 no está verificado.
  - Para el bloque C sí sirven dos ideas: la liberación de retención con excepción vacía (§3) y la retención repartida entre varias facturas.

## Pasos en esta sesión (pocos y rápidos)

1. Guardar lo de Codex: commitear el trabajo pendiente de `.worktrees/v-engine` (`engine.py`, tests, `learned.py`, `EXCEPTION_DIAGNOSIS.md`) en la rama `v-engine` y hacer push a `origin` (papialex12). Así la sesión del PC lo puede leer con `git fetch`.
2. Avisar a la sesión «Parsers documentales» de que la integración está en Dev de Pablo y de que `remittance.py` y los avisos son del bloque de Alina, para que porte o pare.
3. Congelar `papialex12`: nada de LIVE con nuestro motor, porque se sabe peor y gastaría uno de los 20 envíos. **Con tu OK**, borrar `team15-dev` del repo de Pablo: es redundante, y el TRASPASO ya dice que no vale de base.

## Plan para la sesión nueva (Remote Control en tu PC, con más RAM)

### Preparación (unos 15 min)
- `git clone` de Paella_Hackathon, luego `git worktree add ../Paella-cobros-cortos -b cobros-cortos origin/Dev` y `git config core.hooksPath .githooks`. Ese hook exige **gitleaks** instalado.
- Instalar `uv sync` (con pypdf y openpyxl) y descargar `data/live` y `train-lite`. Pedir a Pablo `m2_remittance.csv.gz` de train y sample y copiarlos en `out/<fase>/matches/`: sin ellos la nota no es comparable con 89,26.
- Fijar `PYTHONHASHSEED=0` y `BANKREC_PLUGINS=devoluciones,m2_avisos,remittance,pdfclauses,learned,cobros_cortos`.
- Antes de tocar nada, medir el holdout de Dev limpio y confirmar 89,26 en tu máquina.

### Trabajo («ultracode», con las reglas del `CLAUDE.md` de Dev: plan mode, TDD y decide el holdout)
- **Análisis en paralelo** (workflow de solo lectura, un agente por segmento): `ar_overpayment`, los dos de descuento, `ar_short_withholding` y `ar_partial`. Cada agente ejecuta `python -m bankrec.diff --phase holdout --segment <seg> -n 20` y lee `out/explain_holdout.csv.gz`. Devuelve la causa medida con casos reales, el apartado de POLICIES, la evidencia exacta que exigiría y los casos de «no forzar». Como referencia lleva el diff de `9675d4c` y `EXCEPTION_DIAGNOSIS.md`.
- **Implementación en serie, una regla por commit, en este orden de puntos:** sobrepago → descuento a plazo / fuera de plazo → retención (varias facturas y liberación) → parcial con evidencia.
  - La regla va en `cobros_cortos.py` envolviendo en `prepare()` el método que toque (`_party_amount`, `settle`, `explain_short`, `discount_ok`). El modelo es `_wrap_resolve` de `bankrec/pdfclauses.py`; el plugin modelo es `bankrec/devoluciones.py` y el test modelo `tests/test_terceros.py`.
  - Aviso: `57ea31f` («SHORT_DISCOUNT solo en las facturas que llegan a plazo», rama `mejoras-excepciones`) **no está en Dev**. Antes de reescribir el descuento, preguntar a Pablo si entra.
- **Holdouts uno detrás de otro** (unos 16 min y 5 GB cada uno; en paralelo no caben). Con ~2,5 h hasta las 16:30 salen 6–8 holdouts: hay que priorizar.
- **La regla entra solo si** cumple los criterios del `CLAUDE.md` de Dev:
  - el holdout sube por encima de 89,26 (o de la referencia que esté vigente);
  - la precisión no cae más de 0,02;
  - `unidentified` y `unidentified_lookalike` no empeoran;
  - dos ejecuciones dan el mismo fichero;
  - LIVE tarda menos de 20 min.
- PR a `Dev` con la nota de antes y después. Quien fusiona activa el plugin.

### Auditoría de LIVE y FINAL (tu papel de revisor; revisión final con Mythos/Fable 5.1)
Checklist sobre `origin/Dev` y sobre cada PR antes de fusionar:
1. **Caídas:** los plugins corren sin `try` en `Engine.run`, así que buscar en los módulos nuevos (`devoluciones`, `duplicados`, `m2_avisos`, `preflight`, `cobros_cortos`) sorts con `None`, `KeyError` de columnas que pueden faltar en FINAL y divisiones por cero. Revisar `bankrec/preflight.py`.
2. **Sobreajuste:** Pablo quitó `scripts/gate.py`, así que se pierde el escáner de literales. Pasar su `overfit_scan` (está en nuestro `scripts/gate.py`) o un grep de IDs, IBAN e importes sobre el diff. FINAL trae una sociedad nueva y clientes renombrados.
3. **Determinismo y tiempo:** comparar el sha256 de dos `sub_live` con semilla 0, y comprobar que LIVE tarda menos de 20 min.
4. **Secretos y envío:** gitleaks en el hook, `HK_TOKEN` solo en `.env`, que `submit.py` no imprima el token, sondeo cada ≥5 s y `sub_id` registrado (ya en Dev: `tok = token()` antes del `input`, `except OSError`).
5. **Cadena de suministro:** sin dependencias nuevas fuera de `uv.lock`.

## Verificación
- `uv run --group dev python -m pytest tests` en verde antes de cada commit.
- `python -m bankrec.run --phase holdout` con semilla 0: la nota sube frente a la referencia y se cumplen los criterios de arriba.
- Al final, `python -m bankrec.run --phase live --no-score` dos veces: mismo sha256 y menos de 20 min. El envío lo decide una persona, con `scripts/submit.py LIVE`.
- Actualizar `docs/TRASPASO.md` de Dev con la nota y lo que queda.
