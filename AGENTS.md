# Equipo 15 · Close the Books: instrucciones para agentes y personas

Este fichero lo leen Claude Code (vía `CLAUDE.md`), Codex (nativo) y Antigravity. Es la única fuente de verdad del proyecto.

## El reto, en corto
Conciliación bancaria de Grupo Valtermia, un grupo ficticio. Para cada movimiento bancario hay que dar las partidas que liquida (`item_id` y `amount` en la divisa de la partida) o su categoría, y la excepción que corresponda. Las reglas exactas con las que se etiquetó están en `bankrec-docs-and-masters/POLICIES.md`, que **manda sobre cualquier intuición**. El formato está en `bankrec-docs-and-masters/FORMAT.md` y las columnas en `bankrec-docs-and-masters/DATA_DICTIONARY.md`.

Puntuación: `100 × (0,5·F1 de pares + 0,3·exactitud por movimiento + 0,2·macro-F1 de excepciones)`. Las 10 excepciones pesan lo mismo en el macro-F1, así que acertar `SHORT_WITHHOLDING` (rara) vale tanto como `FX_DIFFERENCE`.

## Cómo se ejecuta
```
python -m bankrec.run --phase sample                      # muestra (1 mes con golden): genera y puntúa en segundos
python -m bankrec.run --phase train --since 2025-01-01    # holdout de TRAIN, imita a LIVE
python -m bankrec.run --phase live --no-score             # genera out/sub_live.csv.gz (NO envía)
python -m bankrec.diff --phase sample --segment ar_partial -n 5   # QA: fallos de un segmento, nuestra respuesta frente a la buena
```
- Sin dependencias: el motor corre con la librería estándar de Python 3.11 o superior. Opcional: `pypdf` y `openpyxl` para los PDF y los xlsx.
- Los datos van en `data/<fase>/` y `data/masters/`. **Nunca se suben a git.** Se descargan con `python scripts/download.py live train-lite`.
- El evaluador oficial está en `vendor/bankrec_score.py`. No se toca: es el mismo código que corre el servidor.

## Arquitectura (`bankrec/`)
- `data.py`: carga de la fase, las partidas con saldo abierto y los índices por número, sufijo, contraparte, IBAN y nombre.
- `engine.py`: bucle por movimiento en orden de fecha. El orden de las reglas es: anulaciones → remesas SEPA y devoluciones → liquidaciones de tarjeta → cheques → remesas de pago → categorías fuertes → **plugins** → referencias → contraparte+importe → categoría o UNIDENTIFIED. `settle()` aplica POLICIES §2-§3 (descuento, retención, comisiones, FX, parcial y sobrepago).
- **Plugins:** un módulo `bankrec/<nombre>.py` con `prepare(engine)` y/o `match(engine, t) -> bool`. Si devuelve True, debe haber emitido filas con `engine.emit(...)` o `engine.emit_cat(...)`. Los que se cargan son `remittance`, `pdfclauses` y `learned`. **Cada agente trabaja en su propio módulo** para no pisarse.
- Cada fila lleva `rule` y `evidence`, que se guardan en `out/explain_<fase>.csv.gz`: el porqué de cada asignación. Es la trazabilidad que contamos al jurado.

## Reglas de trabajo
1. Cada cambio se mide en `sample` y, si está descargado, en el holdout de TRAIN. Si baja la puntuación, no entra.
2. Una regla nueva cita el apartado de POLICIES que implementa.
3. No sobreajustar a la muestra: nada de ids, nombres ni importes concretos en el código.
4. Commits pequeños con un mensaje que diga qué segmento mejora y cuánto.

## Juego limpio y seguridad (innegociable)
- No se ataca la plataforma: nada de escaneos, fuerza bruta ni intentos de saltarse límites. Solo los flujos oficiales de descarga y envío.
- Sin golden de LIVE ni de FINAL por ninguna vía.
- El token del equipo vive solo en `.env` (`HK_TOKEN`). Nunca en código, commits, prompts a otros servicios, capturas ni el vídeo.
- Las consultas de estado de un envío van como mucho cada 5 s: toda la sala comparte IP.
- Límites: 20 envíos LIVE al día, uno cada 180 s, y **un único envío FINAL**. Los envíos los decide una persona, no un agente.
