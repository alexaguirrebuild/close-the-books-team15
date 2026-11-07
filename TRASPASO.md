# v-engine · excepciones

Codex trabaja sobre las diez clases de excepción. Los cambios de motor y sus pruebas están en esta carpeta.
El diagnóstico está en `docs/EXCEPTION_DIAGNOSIS.md`.

## Estado

- Sample, semilla 0, mismo entorno: **79,92 → 80,79**. Macro-F1: **0,654794 → 0,692801**.
- Pasan **13 pruebas** de `tests/test_engine_exceptions.py`.
- Holdout completo pendiente: **199.987 movimientos**, **1.476.215 partidas** seleccionadas.
- No aprobar el parche para integración hasta terminar holdout.
- La preparación del motor recorre **50.890 avisos** y un índice de **131.637 PDF** de TRAIN;
  los recuentos se midieron en sus `index.csv`. El corpus no se recorta para acelerar el resultado.

## Validación

Los artefactos están en `../../out/v-engine/`:

- `baseline_score_sample.json`, `candidate_score_sample.json`.
- `baseline_explain_sample.csv.gz`, `candidate_explain_sample.csv.gz`.
- `validate_sample.py`, `validate_holdout.py`: comparación con el mismo estado inicial.
- `validation_holdout_lean.log`: ejecución activa de holdout, sin serializar la cartera.
- `summarize_exceptions.py`: FP/FN por clase y confusiones, sin cargar todo el golden en memoria.

La referencia original es `snap/base/bankrec/engine.py`, blob Git
`feb44b18209974e921d3f9a1271bca71542145c6`. Se fija esa copia porque HEAD ha cambiado durante la sesión.
No usar el HEAD actual como referencia inicial: ya contiene parte de estos parches.

El Python de `.venv` no tiene `pypdf` ni `openpyxl`. Los resultados anteriores corresponden a ese entorno.
No compararlos con `base/score_sample.json`, generado con lectura documental disponible.
La caché `pdf_text_sample.json` de la validación sin dependencias tiene entradas vacías.

## Memoria

Se observaron otros dos Python de evaluación con unos 6 GB privados cada uno, además del nuestro.
El cargador original crea todo TRAIN antes de cortar; varias ejecuciones simultáneas provocan paginación.
El cargador de diagnóstico filtra antes de crear objetos y compacta filas auxiliares, conservando los mismos
saldos, valores e índices. Los dos motores usan la misma cartera en un proceso; se restauran los saldos.
No se modifica `bankrec/data.py`. Ejecutar `validate_holdout.py fresh` para evitar cachés.

## Pendiente

1. Finalizar la comparación de holdout contra la referencia fija.
2. Ejecutar `bankrec.excf1` y `bankrec.diff --segment ...` sobre los artefactos finales de holdout.
3. Añadir al diagnóstico la tabla final de ambas fases y los FP/FN de las diez clases.
4. Conservar solo cambios que superen las validaciones exigidas.

Codex no ha ejecutado commits, push ni envíos a la plataforma.
