# bankcheck

Ingesta, validación y evaluación para el reto Close the Books. Carga la carpeta completa de **una** fase en un almacén DuckDB. **Ningún comando habla con la plataforma de envíos.**

Requiere [`uv`](https://docs.astral.sh/uv/). Las dependencias se instalan solas al primer `uv run`.

## Datos

Se autodetecta la raíz. Orden: `--data-root`, `$BANKREC_DATA_ROOT`, `./data`, `./`, `../`, `~/Downloads`. Una raíz vale si tiene `<fase>/bank_transactions.csv.gz` y `masters/` o `bankrec-docs-and-masters/masters/`.

```
train/  live/  final/               descomprimidos en la raíz del repo, ignorados por git
bankrec-docs-and-masters/           maestros, documentación y starter
bankrec-docs-and-masters/sample/    muestra de un mes, ignorada por git
```

## Comandos

Desde la raíz del repo:

```bash
uv run --project pipeline bankcheck ingest   --phase train|live|final|sample
uv run --project pipeline bankcheck baseline --phase live --out out/live/baseline.csv.gz
uv run --project pipeline bankcheck validate --phase live out/live/baseline.csv.gz
uv run --project pipeline bankcheck score    --phase train out/train/baseline.csv.gz
uv run --project pipeline bankcheck verify   --file live=out/live/mi_fichero.csv.gz
uv run --project pipeline bankcheck pdf      --phase live FV25-0100122
uv run --project pipeline pytest
```

| Comando | Resultado | Código de salida |
|---|---|---|
| `ingest` | `cache/warehouse/<fase>.duckdb`, `out/<fase>/ingest_report.json`, `out/<fase>/ingest.log` | Distinto de 0 si hay errores |
| `baseline` | Salida válida mínima. **No es el matcher** | 0 |
| `validate` | Veredicto, avisos y `out/<fase>/validate__<fichero>.json` | Distinto de 0 si no es válido |
| `score` | Desglose F1, accuracy, macro-F1 y por segmento, y `out/<fase>/score__<fichero>.json`. Solo `train` y `sample` | Distinto de 0 si el scorer rechaza el fichero |
| `verify` | Todo lo anterior e informe en `out/verification/report.md`: fases con golden con nota, las demás con validación y cobertura. Sin `--file` usa el baseline. `--self-check`: el golden como envío debe dar 100,00 y pasar la validación | Distinto de 0 si algo falla |
| `pdf` | Texto de un PDF por fichero o número impreso | Distinto de 0 si no lo encuentra |

Tiempos en un portátil de 24 GB: TRAIN lite 65 s y 3 GB de RAM; LIVE 36 s y 1,4 GB.

## Garantías

- **Aislamiento:** una ingesta lee solo `<fase>/` y sus maestros, y escribe un fichero propio por fase.
- **Golden fuera:** `load_csv` rechaza cualquier ruta con `golden`. Solo lo leen `submission/score.py` y `matching/evaluate.py` (este último, copias filtradas en un temporal que se borra). Ninguna etapa de matching lo toca, y hay un test que lo comprueba.
- **Idempotencia:** reconstruye en un temporal y renombra al terminar. Dos ingestas dan las mismas huellas en `table_fingerprint`.
- **Un almacén con errores no se publica:** el anterior sigue en su sitio y el nuevo queda en `cache/warehouse/<fase>.failed.duckdb` para diagnosticar. `--allow-errors` lo publica igualmente, por ejemplo si en FINAL falla un solo aviso y hay que seguir; el código de salida sigue siendo distinto de 0 y `ingest_run.errors` lo registra.
- **Ningún fichero se ignora:** uno que no lea ningún cargador es el error `FILE_NOT_READ`, salvo los PDFs de `pdfs/`, que solo se indexan.
- **Nada en silencio:** cada fichero queda en `file_manifest` y cada problema en `ingest_issue`.
- **El validador usa el código del servidor** (`starter/bankrec_score.py`, sin copiar ni modificar, comprobado por SHA-256) y sus reglas solo pueden endurecer el veredicto: cobertura total, `item_id` existente, cabecera y separador documentados, 2 decimales, importe mayor que 0, categoría en las filas sin partida, `UNIDENTIFIED` en ambas columnas, y gzip completo (el código oficial acepta uno truncado). Lee movimientos y partidas de los CSV de la fase, no del almacén, y falla si `ingest_run.errors > 0`.
- **Partidas de fases anteriores:** una devolución o un duplicado puede tocar una partida cerrada antes de la fase, que no está en ninguna de sus tablas. Es el aviso `ITEM_FROM_PRIOR_PHASE` y el fichero sigue siendo válido. Si no está en ninguna fase es el error `ITEM_UNKNOWN`, y si falta una fase anterior en disco, el error `ITEM_UNVERIFIED`.
- **Para escribir un envío usa `submission.writer.write_submission`:** escritura atómica, formato documentado y siempre los mismos bytes.

El esquema está en [`SCHEMA.md`](SCHEMA.md). Qué ficheros entran y salen, cómo falla cada uno y el procedimiento de las 18:00 están en [`INGESTA.md`](INGESTA.md).

## Límites del scorer con TRAIN

`bankrec_score.py` rechaza ficheros de más de 60 MB o 3 M de filas, y un TRAIN real ocupa 234 MB y 5,4 M de filas. `score` y `validate` suben esas tres constantes en una copia del módulo, solo para `train` y `sample`, y lo indican en la salida. El código de puntuación no cambia. En `live` y `final` los límites se aplican sin tocar.

## TRAIN lite

La carpeta `train/` del repo es la edición lite: extractos, PDFs y avisos solo desde 2024-07. Por eso la ingesta avisa de `NO_RAW_STATEMENT` e `INDEXED_FILE_MISSING` en TRAIN, y por eso esos mismos códigos son **errores** en LIVE y FINAL.

## Trampas de los datos ya resueltas

| Trampa | Solución |
|---|---|
| `:61:` de MT940 trae valor y entrada, y en `RC`/`RD` el signo es la última letra | `statements/mt940.py` |
| Continuaciones de `:86:` que empiezan por `-` | `statements/mt940.py` |
| Campos de 38 caracteres en N43 que cortan palabras y conservan espacios | `statements/n43.py` |
| Banca Ribagorza repite días entre ficheros; hay líneas idénticas legítimas | Multiconjunto en `statements/reconcile.py` |
| El texto N43 cambia de mayúsculas respecto a la vista normalizada | Clave con texto normalizado |
| Nombres NFC en los índices y NFD en disco | `roots.nfc` |
| Avisos CSV con `,` o `;`, XLSX con dos plantillas, importes pegados en PDF | `remittance/` |
| `end_to_end_id` no es único dentro de una remesa | Clave de grupo en `aux_files.py` |
| DuckDB lee un `.csv.gz` truncado como si fuera más corto, sin error | `csvload._read_gzip_to_the_end` |
