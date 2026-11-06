# TRASPASO

Quien recibe este repo (Claude, Codex, Antigravity o una persona) lee esto antes que nada y lo sobrescribe al terminar su parte. Si algo falla dos veces, se escribe aquí y se para.

- **Último que escribió:** Claude (PC de Alex), viernes 6-nov por la noche.
- **Pedía Alex:** dejar listos para mañana el motor, el evaluador, los scripts y los docs del día. Ningún envío LIVE antes de las 10:00.

## Cifras medidas (con `PYTHONHASHSEED=0`)

| Medición | Nota | Detalle |
|---|---|---|
| sample (jun-2025) | 81,46 | sin saldos abiertos: plazos y parciales salen peor que en real |
| TRAIN holdout lento (`--phase train --since 2025-01-01`) | **69,36** | F1 0,786 (precisión 0,91, recall 0,69) · acierto por movimiento 0,73 · macro-F1 excepciones **0,41** · 103 min |
| TRAIN holdout rápido (`--phase holdout`) | **71,74** | ene-jun 2025 con saldos exactos a 1-ene, igual que arranca LIVE · F1 0,806 · acierto 0,75 · excepciones 0,44 · 16 min. **Es la referencia para iterar** |
| LIVE | sin enviar | `out/sub_live.csv.gz` valida en local: 590.949 filas, 203.083/203.083 movimientos · 7 min 49 s |

## Dónde se pierden los puntos (holdout lento)

1. **Excepciones (pesan 20 %, macro-F1 0,41).** La palanca más barata: cada una de las 10 clases cuenta igual.
2. **Recall 0,69 frente a precisión 0,91.** Dejamos partidas sin asignar más de lo que fallamos.
3. Segmentos con más fallos: `ar_ref_noise` (15,4 k), `ap_payment_multi` (6,1 k), `ap_payment_single` (4,0 k), `ar_short_discount` (2,0 k), `ar_same_amount_fifo` (2,0 k), `ar_1toN_credit_note` (acierto 0,39), `ap_credit_note_netted` (0,30), `ar_dd_returned` (0,52), `ar_n_to_m` (0,23).
4. **Casi a cero, ganancia rápida:** `ap_factored` (0,07, 1,3 k fallos) y `ap_confirming` (0,08, 0,7 k).

## Siguiente paso sugerido

- Iterar con `python -m bankrec.run --phase holdout` y `python -m bankrec.diff` sobre el segmento elegido. Un segmento por persona o por agente, en worktrees separados (ver `docs/HERRAMIENTAS.md`).
- Orden propuesto: `ap_factored` + `ap_confirming` → excepciones (`SHORT_WITHHOLDING`, `RETURNED`, `SHORT_DISCOUNT` tardío) → `ar_1toN_credit_note` y `ap_credit_note_netted` → `ar_ref_noise`.
- Envío LIVE solo cuando el holdout suba 2 puntos o más.

## Cuidado

- `python -m bankrec.diff --phase holdout --segment <seg> -n 5` enseña los fallos del holdout rápido.
- El holdout rápido usa `data/live/open_items_at_start.csv.gz` para reconstruir los saldos: necesita LIVE descargado.
