# TRASPASO

Quien recibe este repo (Claude, Codex, Antigravity o una persona) lee esto antes que nada y lo sobrescribe al terminar su parte. Si algo falla dos veces, se escribe aquí y se para.

- **Último que escribió:** Claude (PC de Pablo), sábado 7-nov, 13:20.
- **Rama de integración:** `origin/Dev`. Toda rama nueva sale de ahí. `team15-dev` no comparte historia con `Dev` y no sirve de base.
- **Referencia para superar:** holdout **89,26**, en `Dev`.
- **Última nota oficial:** LIVE **88,63** con `648cc9c`. Los envíos los decide una persona.
- **Horario:** las reglas nuevas acaban a las 16:30; ensayo a las 17:00; código congelado a las 17:30; FINAL de 18:00 a 19:30.

## Quién lleva qué

Di en el canal del equipo qué lote coges. Un lote por persona, y **solo se tocan los ficheros de tu lote**.

| Lote | Ficheros | Segmentos | Fallos en LIVE |
|---|---|---|---|
| 1. Cobros cortos | `bankrec/cobros_cortos.py`, `tests/test_cobros_cortos.py` | `ar_short_discount`, `ar_partial`, `ar_overpayment`, `ar_retention`, `ar_discount_late`, `ar_short_withholding`, `ar_short_dispute`, `ar_fx` | ~4.120 |
| 2. Mismo importe y n:m | `bankrec/mismo_importe.py`, `tests/test_mismo_importe.py` | `ar_n_to_m`, `ar_same_amount_fifo`, `ar_same_amount_month`, `ar_third_party_agency`, `nonitem_intercompany`, `ar_advance_deduction` | ~2.940 |
| 3. Cheques y adeudos | `bankrec/lotes_cobro.py`, `tests/test_lotes_cobro.py`, `bankrec/devoluciones.py`, `tests/test_devoluciones.py` | `ar_cheque_deposit`, `ar_cheque_returned`, `ar_dd_returned`, `ar_dd_batch`, `ar_dd_represented_transfer` | ~1.650 |
| 4. Proveedores | `bankrec/proveedores.py`, `tests/test_proveedores.py` | `ap_confirming`, `ap_credit_note_netted`, `ap_direct_debit`, `ap_partial`, `ap_fx`, `ap_duplicate_payment`, `ap_duplicate_refund` | ~1.190 |

| Fuera de los lotes | Quién |
|---|---|
| `m_refs` en `bankrec/engine.py`, con `ar_ref_noise`, `ar_1toN_text` y `ar_1toN_credit_note` | Sesión de Pablo, rama `mejoras-citas` |
| Flujo de FINAL: `scripts/final_run.py` y `docs/RUNBOOK_FINAL.md` | Sesión de Pablo, rama `feat/flujo-final` |
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
| holdout tras 6bfb068 | **89,26** | F1 0,921 · acierto 0,903 · excepciones 0,807. **Referencia actual** |

## Cuidado

- `python -m bankrec.diff --phase holdout --segment <seg> -n 5` enseña los fallos del holdout.
- El holdout usa `data/live/open_items_at_start.csv.gz` para reconstruir los saldos: necesita LIVE descargado.
- `sample` no decide. Una regla que dio 85,49 en `sample` dio 81,49 en holdout.
