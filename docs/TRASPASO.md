# TRASPASO

Quien recibe este repo (Claude, Codex, Antigravity o una persona) lee esto antes que nada y lo sobrescribe al terminar su parte. Si algo falla dos veces, se escribe aquí y se para.

- **Último que escribió:** Claude (PC de Pablo), sábado 7-nov, 14:10.
- **Rama de integración:** `origin/Dev`. Toda rama nueva sale de ahí. `team15-dev` no comparte historia con `Dev` y no sirve de base.
- **Referencia para superar:** holdout **89,91**, en `Dev`.
- **Última nota oficial:** LIVE **88,63** con `648cc9c`. **No se sube nada a LIVE hasta llegar a 95 en holdout** (decisión de Pablo, 13:45). Los envíos los decide una persona.
- **Horario:** las reglas nuevas acaban a las 16:30; ensayo a las 17:00; código congelado a las 17:30; FINAL de 18:00 a 19:30.

## Quién lleva qué

Reparto medido a las 14:00. Di en el canal del equipo qué lote coges. Un lote por persona, y **solo se tocan los ficheros de tu lote**. Los techos suponen esa parte perfecta: no se suman ni son previsiones.

| Lote | Ficheros | Qué ataca | Dónde está | Techo |
|---|---|---|---|---|
| 1. Citas incompletas | `bankrec/citas_parciales.py`, `tests/test_citas_parciales.py` | La regla `ref` asigna lo que cita el texto y deja el resto: faltan 31.945 filas en 3.309 movimientos | `ar_short_dispute` 11.706, `ar_1toN_credit_note` 9.096, `ar_1toN_text` 6.201, `ar_ref_noise` 4.106 | el mayor; +4,10 junto con el lote 2 |
| 2. Pagos y otras fuentes | `bankrec/proveedores.py`, `tests/test_proveedores.py`, `bankrec/lotes_cobro.py`, `tests/test_lotes_cobro.py`, `bankrec/devoluciones.py`, `tests/test_devoluciones.py` | Pagos con partidas que acaban en `fallback_debit` (7.238 filas en 844 movimientos), remesas, cheques y adeudos | `ap_confirming` 5.562, `ap_fx` 1.674, `ar_cheque_deposit` 572, `ar_dd_batch` 477, `ar_dd_returned` 460 | +0,26 `ap_confirming`, +0,25 `ar_dd_returned`, +0,14 `ap_fx` |
| 3. Cobros sin asignar | `bankrec/mismo_importe.py`, `tests/test_mismo_importe.py` | Cobros en UNIDENTIFIED con cliente, pero sin suma exacta única. Parte de `integracion-sesiones` hasta que entre la PR #12 | Ambiguos o con demasiadas candidatas: 24.021 filas. No cuadran: 18.573. Sobre todo `ar_ref_noise`, `ar_1toN_credit_note`, `ar_n_to_m`, `ar_partial`, `ar_retention` | +1,86 y +2,52 |
| 4. Etiquetas y soluciones seguras | `bankrec/cobros_cortos.py`, `tests/test_cobros_cortos.py` | 1.146 movimientos con las partidas bien y la etiqueta mal, OVERPAYMENT y las soluciones que ya dan precisión alta | `ar_short_discount` 240, `ar_n_to_m` 197, `ar_instalment` 128, `ar_discount_late` 118, `ar_overpayment` | +1,01 etiquetas, +0,90 `ar_overpayment` |

| Fuera de los lotes | Quién |
|---|---|
| Nombres cortados: `bankrec/nombres_cortados.py` | Sesión de Pablo. Pendiente de holdout |
| `cuadra/` | Sesión de Pablo. Solo revisión y demo: no genera envíos |

**No tocar:** el resto de `bankrec/`, incluido `engine.py`, y `scripts/`, `vendor/`, `pipeline/`, `cuadra/`, `pyproject.toml` y `uv.lock`.

## Cómo se trabaja en un lote

Las normas de `CLAUDE.md` siguen vigentes: plan aprobado, TDD y holdout antes de la PR.

```bash
git fetch origin
git worktree add ../Paella-<lote> -b <lote> origin/Dev
cd ../Paella-<lote>
git config core.hooksPath .githooks   # exige gitleaks instalado
export BANKREC_DATA=<ruta>/data BANKREC_M2_DIR=<ruta>/out
export BANKREC_PLUGINS=devoluciones,m2_avisos,remittance,pdfclauses,learned,<plugin>
uv run python -m bankrec.run --phase holdout
```

- **Avisos enlazados:** copia `m2_remittance.csv.gz` de `train` y de `sample` a `<ruta>/out/<fase>/matches/`. No van en git. Pídeselos a Pablo: sin ellos la nota sale más baja y no se puede comparar con la referencia.
- **Plugin:** un módulo en `bankrec/` con `prepare(engine)` y `match(engine, t)`. `bankrec/devoluciones.py` sirve de modelo. `match` se ejecuta después de las remesas y las categorías claras, y antes de `m_refs` y `m_party_amount`.
- **Para cambiar algo anterior o interno:** envuelve en `prepare` el método que toque (`engine.m_cheque`, `engine.m_payment_run`, `engine.explain_short`…), sin editar `engine.py`. Lo hace `_wrap_resolve` en `bankrec/pdfclauses.py`.
- **Activarlo:** tu PR no lo activa. Quien la fusiona añade el plugin a la lista de `engine.py` y vuelve a comprobar el holdout. En la PR pon la nota de antes y de después.

## Qué falta para 95

Cascada en holdout, con el golden solo para evaluar: cada fila supone perfecta esa parte y todas las anteriores. Son techos, no previsiones, y dependen del orden.

| Parte | Sube | Acumulado | Dónde mirar |
|---|---|---|---|
| Etiquetas e importes en filas ya bien asignadas | +1,01 | 90,92 | `explain_short`, `settle`. Confusiones: PARTIAL y vacía (1.071), SHORT_DISCOUNT como PARTIAL (263), vacía como DUPLICATE_PAYMENT (171) |
| Quitar filas falsas | +0,46 | 91,38 | Precisión de `ref_cited`, `ref_completed` y `party_due_subset` (acierta el 81 %) |
| Número citado entero | +0,11 | 91,49 | Poco que rascar |
| Cita parcial (20.567 filas) | +1,54 | 93,03 | `_settle_cited`, `_complete_cited`: `ar_short_dispute` 8.280, `ar_1toN_credit_note` 5.138, `ar_1toN_text` 2.672, `ar_ref_noise` 2.116 |
| Sin cita, hasta 14 filas (34.807 filas) | +5,22 | 98,25 | `m_party_amount`, `scan_names`: `ar_ref_noise` 17.812, `ar_1toN_credit_note` 4.174, `ar_n_to_m` 2.366 |
| Sin cita, remesa grande (34.364 filas) | +1,75 | 100,00 | En cobros no hay evidencia. Sí en `ap_confirming` (4.597) y `ap_fx` (1.549), que van por remesas, y en `ar_1toN_remittance` (1.440), que tiene aviso |

La clasificación por cita es aproximada: 7.663 filas no tienen número localizado y cuentan como sin cita.

### Qué dio la medida de los cobros en UNIDENTIFIED

De 12.920 cobros, 8.149 tienen partidas. **El cliente no es el problema:** está identificado en 7.322 de esos 8.149, pero también en 2.660 de los 4.771 que son UNIDENTIFIED de verdad, así que tener cliente no basta para asignar.

| Solución única | Bien | Mal | Nota si se asignan todas, acumulada |
|---|---|---|---|
| Suma exacta | 16 | 84 | **No usar:** +0,00 |
| Con descuento | 197 | 17 | +0,04 |
| Con retención | 97 | 0 | +0,06 |
| Con parcial al 40, 50 o 60 % | 410 | 32 | +0,10; total 90,11 |

**Por tanto, las sumas exactas no llevan a 95: dan +0,20.** Simulación sin propagar saldos: decide el holdout. Lo que queda exige otra evidencia: un patrón nuevo, como el del parcial, o completar las citas. Probado y descartado: los pagos grandes no siguen un orden de facturas, y los extractos crudos no traen más referencias.

## Dónde se pierden los puntos (LIVE 88,63)

Nota: `100 × (0,5·F1 + 0,3·acierto + 0,2·macro-F1)`. Por tanto:
- Subir 0,1 el F1 de una clase de excepción da **+0,2 puntos**.
- 1.000 movimientos más acertados dan **unos +0,15**, más lo que suba el F1 de asignaciones.

| Clase de excepción (holdout) | Precisión | Recall | F1 |
|---|---|---|---|
| UNIDENTIFIED | 0,33 | 0,99 | **0,50** |
| OVERPAYMENT | 0,82 | 0,41 | **0,54** |
| PARTIAL | 0,84 | 0,78 | 0,81 |
| SHORT_BANK_CHARGES | 0,91 | 0,75 | 0,83 |
| DUPLICATE_PAYMENT | 0,78 | 0,88 | 0,83 |

De los 9.206 falsos UNIDENTIFIED, 8.990 son cobros que sí tienen partidas (`ar_ref_noise` 4.248, `ar_partial` 868, `ar_n_to_m` 700, `ar_1toN_credit_note` 695). Baja si se encuentran esas partidas con evidencia exacta, no retocando la etiqueta.

## Cifras medidas (con `PYTHONHASHSEED=0`)

| Medición | Nota | Detalle |
|---|---|---|
| holdout tras b3bc63f | 82,33 | F1 0,860 · acierto 0,867 · excepciones 0,667 |
| holdout tras bd99d1d | 86,83 | F1 0,914 · acierto 0,880 · excepciones 0,736 |
| LIVE con bd99d1d | 86,84 | primera subida: 79,61 |
| holdout tras cccc17e | 87,31 | F1 0,916 · acierto 0,890 · excepciones 0,740 |
| holdout tras 648cc9c | 89,07 | F1 0,920 · acierto 0,900 · excepciones 0,802 |
| LIVE con 648cc9c | 88,63 | F1 0,917 · acierto 0,897 · excepciones 0,794 |
| holdout tras 6bfb068 | 89,26 | F1 0,921 · acierto 0,903 · excepciones 0,807 |
| holdout con `mejoras-citas` | **89,91** | F1 0,926 · acierto 0,913 · excepciones 0,811 · `unidentified` 0,9925. **Referencia actual** |

## Cuidado

- `python -m bankrec.diff --phase holdout --segment <seg> -n 5` enseña los fallos del holdout.
- El holdout usa `data/live/open_items_at_start.csv.gz` para reconstruir los saldos: necesita LIVE descargado.
- `sample` no decide. Una regla que dio 85,49 en `sample` dio 81,49 en holdout.
