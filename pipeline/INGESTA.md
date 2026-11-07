# Documentos de ingesta

Qué ficheros recibimos, cuáles entregamos y cómo falla cada uno. Las cifras son de LIVE, medidas el 2026-11-07.

**No se sube ningún `.zip`:**

| Sentido | Qué | Formato |
|---|---|---|
| Descarga | Documentación, maestros, muestra y starter | `.zip` |
| Descarga | Datos de TRAIN, LIVE y FINAL | `.tar.gz` |
| Subida | Envío | `.csv` o `.csv.gz` |
| Subida | Vídeo del proyecto | `.mp4`, `.mov` o `.webm`, o un enlace |

Ni `pipeline/` ni `cuadra/` descomprimen `.zip` o `.tar.gz`: hay que extraerlos antes.

Si tienes prisa, ve al [procedimiento de las 18:00](#7-procedimiento-de-las-1800).

## 1. Qué se descarga

La lista real, con sus sha256, está en `https://hackathon.usetalky.com/config.json`.

| Fichero en `/data/v1/` | Tamaño | Raíz dentro del archivo |
|---|---|---|
| `bankrec-docs-and-masters.zip` | 10 MB | **Ninguna**: suelta `README.md`, `masters/`, `sample/` y `starter/` |
| `bankrec-train-lite.tar.gz` | 460 MB | `train/`. Es la copia que usa el equipo |
| `bankrec-train.tar.gz` | 657 MB | Sin comprobar |
| `bankrec-live.tar.gz` | 87 MB | Sin comprobar; la carpeta local es `live/` |
| `bankrec-final.tar.gz` | 100 MB | Se espera `final/`. Da 403 hasta las 18:00 |

Cuidado con:
- **Las sumas sha256:** las que van dentro del JavaScript de la web están desfasadas. Valen las de `config.json`.
- **El `.zip` sin raíz:** extraído en la raíz del repo sobrescribe `README.md` y crea un `masters/` que tiene prioridad sobre `bankrec-docs-and-masters/masters`. Hay que extraerlo dentro de `bankrec-docs-and-masters/`.
- **Extraer dos veces:** crea `train 2/`, y el pipeline no lo encuentra.
- **Extraer FINAL dentro de una carpeta `final/`:** crea `final/final/` y falla con «No encuentro la fase».

## 2. Qué hay dentro de una fase

La web habla de `.txt` para Norma 43 y de `.csv` para las tablas. Los ficheros reales son `.n43` y `.csv.gz`.

| Ruta | Formato real | Cantidad | A tener en cuenta |
|---|---|---|---|
| `bank_transactions.csv.gz` | gzip, UTF-8, coma | 203.083 líneas | Es la referencia y lleva los `bank_txn_id`. 5 líneas sin texto |
| `ar_invoices`, `ap_invoices`, `credit_notes`, `open_items_at_start` `.csv.gz` | Igual | 1.031.576 partidas, 325.419 de apertura | El número impreso no identifica una partida |
| `bank_statements/<cuenta>/<cuenta>_<AAAAMM>.n43` | 80 bytes por registro, CRLF | 84 ficheros, 39.441 movimientos | Documentado como latin-1, pero en LIVE todo es ASCII |
| `….sta` (MT940) | CRLF, UTF-8 | 109 ficheros, 59.565 movimientos | Empieza por `{1:F01`. 89 ficheros con acentos |
| `….xml` (camt.053.001.08) | LF, UTF-8 | 94 ficheros, 105.538 movimientos | Solo este formato trae contraparte e IBAN en columnas propias |
| `remittance_advices/<sociedad>/RA_<fecha>_<PAGADOR>_<n>.csv` | CRLF, `,` (1.997) o `;` (384) | 2.381 | Fila 1 clave/valor, fila 2 cabecera, al final `TOTAL` |
| `….txt` | Correo, LF | 1.815 (1.520 inglés, 295 español) | Frase en inglés con fecha MM/DD/AAAA y tabla con DD/MM/AA |
| `….xlsx` | 1 hoja | 497 (319 por documento, 178 por voucher) | Sin divisa. En voucher, la factura va sin prefijo (`100974`) |
| `….pdf` | Con capa de texto | 2.196 | Inglés o español. Importes pegados como `26.425,09-2.642,50` |
| `remittance_advices/index.csv` | UTF-8 | 6.889 | 612 nombres están en NFD en disco y en NFC en el índice (resuelto) |
| `pdfs/<sociedad>/*.pdf` + `index.csv` | 2-3 KB | 16.688 | Ver abajo |
| `payment_runs/<soc>_<AAAAMM>_<n>.csv` | CSV | 132 ficheros, 205.713 filas | `end_to_end_id` no es único dentro de una remesa |
| `direct_debit_batches/<soc>_<sucursal>_<AAAAMM>.csv` | CSV | 180 ficheros, 93.786 filas | `re_presentation=Y` marca un recibo que se vuelve a cobrar |
| `card_settlements/<soc>_<AAAAMM>.csv` | CSV | 30 ficheros, 19.110 filas | Se liquida por el bruto |
| `cheque_deposits/<soc>_<sucursal>_<AAAAMM>.csv` | CSV | 102 ficheros, 6.083 filas | `stub_reference` suele venir incompleto |
| `masters/` (`final/masters_final/` en FINAL) | CSV | 17 sociedades, 47 cuentas, 55.672 clientes, 12.831 proveedores, 21 categorías, 13 condiciones, 13.706 tipos de cambio | Foto a 2025-12-31; los tipos acaban ese día |

**PDFs de `pdfs/`:**
- Hay 9.499 facturas, 3.578 de proveedor, 1.841 abonos, 1.394 abonos de proveedor, 297 proformas y 79 avisos de cambio de cuenta.
- En una muestra de 1.200, todos tenían una página y texto, y ninguno estaba ilegible. No hace falta OCR, al menos en LIVE.
- Hay varios idiomas y formatos de número y fecha (`1.445,55`, `1,653.86`, `07/21/2025`).
- 1.134 números de documento llevan `/` o espacios. Los 79 avisos de cuenta no tienen número.
- **La ingesta solo los indexa.** Las cláusulas (plazos, retención, anticipo, «anula y sustituye», mes de servicio, IBAN nuevo) no están en ninguna tabla: las tiene que leer el matching.

**El dato se adelanta a la documentación:**
- Hay 49 cuentas en extractos y 47 en el maestro. Faltan `IT01-VALD-EUR-2` (1.689 líneas) y `ZA01-UMVC-ZAR` (2 líneas).
- ZA01 ya aparece en diciembre de 2025, aunque se adquiere el 2026-01-01, y aún no está en `entities.csv`.
- Las 4 cuentas MONC tienen 3 ficheros N43 y 3 XML por la migración de octubre.

## 3. Qué se sube

| Qué | Formato y límite | Cómo |
|---|---|---|
| **Envío LIVE o FINAL** | `.csv` o `.csv.gz`, 60 MB sin comprimir, 3 M de filas | Pasos de abajo |
| **Vídeo** | `.mp4`, `.mov` o `.webm`, 1.024 MB, o enlace | `POST /api/project/video-upload-url`, subida y `POST /api/project/video-complete` |
| **Proyecto** | Vídeo, repositorio y descripción, hasta las 19:45 | `POST /api/project` |

Pasos de un envío, según el código de la web:
1. `POST /api/submissions/upload-url` con `{phase, filename}` y el token. **Esto ya consume un hueco.**
2. Subida a S3 por `POST` multipart: los `fields` recibidos más `file`. No es un `PUT`.
3. `POST /api/submissions/{id}/score`.
4. `GET /api/submissions/{id}`, como mucho cada 5 s.

**Límites:** el servidor permite 1 envío cada 10 s y 100.000 al día (`GET /api/state`). La regla del equipo es 1 cada 3 min y 20 al día, y es la que manda.

**FINAL:** cuenta el primer fichero válido. Un fichero rechazado no gasta el envío. Sin envío FINAL no se entra en la clasificación final.

**Tamaño estimado:** LIVE tiene unas 750.000 asignaciones de unos 45 bytes, unos 34 MB. Cabe sin problema.

## 4. Qué pasa con cada tipo de fichero

| Fichero | Web de envío | `bankcheck ingest` | Cuadra («Elegir carpeta») |
|---|---|---|---|
| `.csv` | Acepta | Sí en maestros, auxiliares y avisos. **No en tablas de raíz:** esperan `.csv.gz` | Sí, es lo único que lee |
| `.csv.gz` | Acepta | Solo tablas de raíz. En auxiliares da `MISSING_FILE` y `FILE_NOT_READ` | **Lo ignora** |
| `.zip`, `.tar.gz` | Rechaza | Dentro de la fase da `FILE_NOT_READ`; entre avisos, `REMITTANCE_PARSE_ERROR` | Lo ignora |
| `.xlsx` | Rechaza | Solo avisos, con 2 plantillas fijas | Lo ignora |
| `.txt` | La web lo rechaza; el scorer lo aceptaría | Avisos, y extractos si el contenido es N43 | Lo ignora |
| `.pdf` | Rechaza | Avisos: los lee. `pdfs/`: los indexa | Los lista; necesita `pdfs_text.csv` |
| `.n43`, `.sta`, `.xml` | Rechaza | Detecta el formato por el contenido | Los ignora |
| Otro fichero en la fase (`README`, `__MACOSX/`) | No aplica | **`FILE_NOT_READ`, que bloquea la publicación** | Lo ignora |

## 5. Errores posibles

### 5.1 Al enviar: scorer oficial

Cada caso se ha pasado por `starter/bankrec_score.py`.

| Caso | Mensaje |
|---|---|
| `.zip` o `.xlsx` real | `unsupported file type: upload .csv or .csv.gz` |
| ZIP renombrado a `.csv`, latin-1 o UTF-16 | `file is not valid UTF-8` |
| `.tar.gz` | `file looks binary (NUL bytes)…` |
| `.gz` que no es gzip | `file name ends with .gz but the content is not gzip` |
| Vacío / solo cabecera | `empty file` / `no data rows` |
| Columna extra / columna ausente | `unexpected column(s)` / `missing column(s)` |
| Más de 60 MB / gzip que infla más de 80 MB | `file too large` / `decompressed file exceeds 80 MB` |
| Movimiento de otra fase | `… is not a bank transaction of this phase` |
| `item_id` mal formado | `item_id '…' is not a valid id` |
| Importe en fila sin partida | `amount must be empty when item_id is empty` |
| Importe negativo o `1,000.00` | `invalid amount '…'` |
| Categoría junto a partida | `category must be empty when item_id is given` |
| Categoría o excepción desconocida | `unknown category` / `unknown exception` |
| Par repetido | `duplicate row for (…)` |
| Partida y categoría en la misma línea | `… has both item rows and a no-item (category) row` |
| Número de campos incorrecto | `expected 5 fields, found N` |

**Tolera sin problema:** BOM, CRLF, `;` con coma decimal, columnas en otro orden, cabecera en mayúsculas, excepción en minúsculas, líneas en blanco y gzip con nombre `.csv`.

**Lo peligroso es lo que acepta y luego puntúa mal, y en FINAL gasta el único envío:**
- movimientos omitidos;
- un `item_id` bien formado que no existe;
- una fila sin partida y sin categoría;
- importes con 3 decimales;
- un gzip truncado justo entre dos filas (el scorer lee lo que hay; si corta una fila, sí lo rechaza).

`bankcheck validate` detecta todos estos casos. **Hay que pasarlo siempre antes de enviar.**

### 5.2 Al enviar: API

| Código | Qué hacer |
|---|---|
| `RATE_LIMITED`, `DAILY_LIMIT` | Esperar. Pedir URL ya consumió el hueco |
| `PHASE_NOT_OPEN`, `SUBMISSIONS_FROZEN`, `SUBMISSIONS_CLOSED` | Mirar `GET /api/state` |
| `FINAL_USED` | Ya no hay más envíos |
| `NOT_UPLOADED`, `UPLOAD_FAILED` | La subida falló: hay que crear un envío nuevo |
| `UNAUTHORIZED`, `FORBIDDEN`, `TEAM_DISABLED` | Token o equipo: hablar con la organización |
| `PAYLOAD_TOO_LARGE`, `INTERNAL`, `NETWORK` | Revisar el tamaño o reintentar |

### 5.3 En la ingesta: errores que bloquean

Con cualquiera de estos, el almacén nuevo **no se publica**: queda en `cache/warehouse/<fase>.failed.duckdb` y sigue valiendo el anterior.

| Grupo | Códigos |
|---|---|
| Faltan ficheros | `MISSING_FILE`, `MASTERS_FINAL_MISSING`, y en LIVE y FINAL `NO_RAW_STATEMENT` e `INDEXED_FILE_MISSING` |
| Fichero inesperado o ilegible | `FILE_NOT_READ`, `FILE_UNREADABLE`, `BAD_HEADER` |
| Valores | `BAD_VALUE`, `CSV_ROW_REJECTED`, `DUPLICATE_BANK_TXN`, `DUPLICATE_ITEM` |
| Extractos | `STATEMENT_UNKNOWN_FORMAT`, `STATEMENT_PARSE_ERROR`, `STATEMENT_PARTIAL`, `STATEMENT_CONTROL` |
| Cruce con la vista normalizada | `RAW_ONLY_LINE`, `MISSING_IN_RAW`, `XVAL_DIFF`, `BANK_LINE_OUTSIDE_PHASE` |
| Avisos de remesa | `REMITTANCE_PARSE_ERROR`, `REMITTANCE_PARTIAL`, `REMITTANCE_NO_LINES` |

### 5.4 En la ingesta: avisos que no bloquean

Último informe de LIVE: **0 errores, 5.830 avisos**, 287 de 287 cuenta/mes cuadradas y 203.083 de 203.083 movimientos enlazados con su extracto.

| Código | LIVE | Lectura |
|---|---|---|
| `UNKNOWN_ACCOUNT` | 5.685 | Maestro desfasado: `IT01-VALD-EUR-2` y `ZA01-UMVC-ZAR` |
| `REMITTANCE_SUM_MISMATCH` | 139 | **Es señal, no ruido:** el pagador anuncia menos de lo que lista (10.158,27 frente a 12.438,27). Todos están en avisos `.txt` |
| `AUX_DUPLICATE_ROW` | 3 | Filas repetidas en remesas |
| `UNKNOWN_ENTITY` | 2 | ZA01 |
| `CHEQUE_TOTAL_MISMATCH` | 1 | Un depósito no cuadra |
| Sin aparecer en LIVE | 0 | `FX_RATES_STALE`, `MASTER_NOT_UPDATED`, `EMPTY_FILE`, `STATEMENT_EXTENSION_MISMATCH`, `FILE_NOT_IN_INDEX`, `INDEX_REPEATED_FILE`, `AUX_DATE_OUTSIDE_PHASE`, `CURRENCY_WITHOUT_RATE`, `PAYMENT_RUN_TOTAL_MISMATCH`, `DD_BATCH_TOTAL_MISMATCH`, `CARD_NET_MISMATCH` |

`FX_RATES_STALE` avisa si los tipos de una divisa en uso acaban más de 5 días antes del final de la fase. `fx_rates.csv` llega a 2025-12-31: en FINAL saltaría si `masters_final/` no trae tipos nuevos.

### 5.5 Ensayo con ficheros rotos

Hecho con la CLI real sobre una copia de `sample`. Ningún caso da traza de error y todos dejan un informe nuevo, salvo el de `bank_transactions.csv`.

| Caso | Salida | Publica | Errores |
|---|---|---|---|
| Sin tocar nada, o ruido oculto de macOS (`.DS_Store`, `._x.pdf`) | 0 | Sí | Ninguno |
| BOM en los índices | 0 | Sí | Ninguno |
| Aviso PDF corrupto o vacío | 1 | No | `REMITTANCE_PARSE_ERROR` |
| El mismo con `--allow-errors` | 1 | Sí | `REMITTANCE_PARSE_ERROR` |
| Aviso XLSX con la hoja rota | 1 | No | `REMITTANCE_PARSE_ERROR` |
| Aviso CSV con plantilla nueva | 1 | No | `REMITTANCE_PARSE_ERROR` |
| Importe `NaN` o enorme en un aviso | 1 | No | `REMITTANCE_PARSE_ERROR` |
| `index.csv` sin columna `file` | 1 | No | `FILE_UNREADABLE` |
| Fila de `index.csv` sin nombre de fichero | 1 | No | `BAD_VALUE`; esa fila se descarta |
| Fecha imposible en `index.csv` | 1 | No | `BAD_VALUE`; la fecha queda vacía |
| Columna nueva en un maestro | 1 | No | `BAD_HEADER`; la tabla queda vacía |
| `ar_invoices.csv` sin comprimir | 1 | No | `MISSING_FILE`, `FILE_NOT_READ` |
| `bank_transactions.csv` sin comprimir | 2 | No | «No encuentro la fase»; sin informe |
| `ar_invoices.csv.gz` truncado | 1 | No | `FILE_UNREADABLE`; la tabla queda vacía |
| `bank_transactions.csv.gz` truncado | 1 | No | `FILE_UNREADABLE`, `XVAL_DIFF` |
| `LEEME.txt`, `.zip` o `__MACOSX/` en la fase | 1 | No | `FILE_NOT_READ` |
| Extracto en formato desconocido | 1 | No | `STATEMENT_UNKNOWN_FORMAT` |
| Extracto XML truncado, o fecha imposible en un extracto | 1 | No | `STATEMENT_PARSE_ERROR` |
| Extracto N43 cortado | 1 | No | `XVAL_DIFF`, `RAW_ONLY_LINE`, `MISSING_IN_RAW` |
| UTF-8 inválido en una remesa | 1 | No | `CSV_ROW_REJECTED` |
| Falta una carpeta auxiliar | 1 | No | `MISSING_FILE` |

**Un gzip truncado era el peor caso.** DuckDB lo lee como si el fichero fuera más corto, sin error: en una prueba devolvió 119.801 filas de 200.000. Por eso ahora cada `.csv.gz` se lee entero antes de cargarlo (1,2 s en TRAIN, 0,2 s en LIVE).

Si la ingesta se para por algo no previsto, deja la traza en `out/<fase>/ingest.log`, no deja `ingest_report.json` y el almacén anterior sigue en su sitio.

## 6. Qué puede traer FINAL distinto

No se puede comprobar hasta las 18:00.

| Esperado (según `POLICIES.md` y `DATA_DICTIONARY.md`) | Efecto en la ingesta |
|---|---|
| `final/masters_final/` | Prioridad fichero a fichero. Si no está: `MASTERS_FINAL_MISSING` |
| `open_items_at_start.csv.gz` | Obligatorio; si falta: `MISSING_FILE` |
| ZA01 con ZAR, numeración `KWI…`, renombrada el 2026-04-01 | ZAR ya está en N43 y en tipos de cambio. Su banco ya aparece como MT940 en LIVE |
| Cuenta de cobros de IT01 cambia el 2026-02-01 | Ambas son válidas |
| Una columna o plantilla nueva | `BAD_HEADER` o `REMITTANCE_PARSE_ERROR` |
| Un formato de extracto nuevo | `STATEMENT_UNKNOWN_FORMAT` |

## 7. Procedimiento de las 18:00

Desde la raíz del repo, sin hacer doble clic en el archivo:

```bash
curl -fO https://hackathon.usetalky.com/data/v1/bankrec-final.tar.gz
shasum -a 256 bankrec-final.tar.gz     # comparar con /config.json
tar -tzf bankrec-final.tar.gz | head   # debe empezar por final/
tar -xzf bankrec-final.tar.gz
uv run --project pipeline bankcheck ingest --phase final
```

Cuenta con 40 s de ingesta si la máquina está libre. Con otros dos pipelines en marcha, LIVE tardó 117 s.

Si la ingesta da errores, mira `out/final/ingest_report.json` y `cache/warehouse/final.failed.duckdb`, y decide según el código:

| Código | Qué se pierde | ¿`--allow-errors`? |
|---|---|---|
| `REMITTANCE_*` en pocos ficheros | Esos avisos | **Sí, es razonable** |
| `BAD_HEADER` o `FILE_UNREADABLE` en una tabla de raíz o un maestro | **La tabla entera queda vacía** | **No.** Volver a descargar o adaptar `tables.py` |
| `FILE_NOT_READ` | Nada todavía | Mejor no: mira el fichero y quítalo si es ruido, o adapta el cargador si son datos |
| `STATEMENT_*`, `XVAL_DIFF`, `RAW_ONLY_LINE`, `MISSING_IN_RAW` | El contraste con el extracto de esa cuenta; los `bank_txn_id` salen de la vista normalizada | Solo tras mirar qué cuenta afecta |
| `BAD_VALUE`, `CSV_ROW_REJECTED` | Esas filas o valores | Según cuántos sean |

**Con `--allow-errors`, `bankcheck validate` dirá NO VÁLIDO por `INGEST_HAD_ERRORS`, aunque el formato esté bien.** Si es el único error y `Formato oficial` dice OK, el fichero es enviable. Esa decisión es del equipo, no del script.

**Nunca se pide URL de subida sin el OK expreso del usuario en esa conversación:** pedirla ya consume un hueco, y el envío FINAL es irreversible.

## 8. Cuadra

`cuadra/` hoy no puede cargar la carpeta oficial ni exportar un envío válido. `PLAN.md` la marca como opcional.

- No lee `.csv.gz` (`src/data/sources.ts:16`, `src/data/schema.ts:164`).
- `fx_rates.csv` no encaja con su esquema: pide `quote` y `rate`, y eso bloquea la carga.
- Para el texto del banco elige `remittance_info` antes que `text`, y esa columna solo existe en camt.
- No reconoce `early_payment_discount_pct` ni `supplier_invoice_number`, y no avisa.
- Su exportación pone importe en las filas sin partida (`src/data/exportSubmission.ts:70`), y el scorer lo rechaza.

## 9. Qué está comprobado y qué no

- **Con datos reales:** estructura, cabeceras, codificación y nombres de LIVE; una muestra de 1.200 PDFs; las ingestas de `sample`, `live` y `train`; el sha256 de 2 archivos.
- **Con el código oficial:** la tabla 5.1.
- **Con la CLI real:** la tabla 5.5.
- **Solo leyendo el código de la web:** el flujo de subida y los códigos de la API. No se ha hecho ninguna llamada autenticada ni se ha pedido ninguna URL de subida.
- **Sin comprobar:** el contenido real de FINAL, que no existe hasta las 18:00, y el comportamiento de la API ante errores.
