# Esquema del almacén

Un fichero DuckDB por fase: `cache/warehouse/<fase>.duckdb`. Cada fichero solo contiene esa fase y sus maestros. El golden nunca entra. Si la ingesta tiene errores, el resultado queda en `<fase>.failed.duckdb` y no sustituye al anterior.

```bash
uv run --project pipeline bankcheck ingest --phase train|live|final   # también sample
```

Consulta directa desde Python:

```python
import duckdb
con = duckdb.connect("cache/warehouse/live.duckdb", read_only=True)
con.execute("SELECT * FROM item WHERE printed_norm = 'FV250100122'").fetchall()
```

## Proveniencia

| Columna | Dónde | Significado |
|---|---|---|
| `src_file` | Todas las tablas de datos | Fichero de origen, relativo a la raíz |
| `src_line` | `bank_statement_line` | Línea del fichero (N43, MT940) o posición de la entrada (camt) |
| `bank`, `account_id` | Banco y auxiliares con cuenta | `payment_run_line` usa `debtor_account_id` como cuenta |
| `month` | Banco, partidas, auxiliares, avisos, PDFs | `AAAA-MM`. Ver cada tabla para saber de qué fecha sale |

Importes en la divisa de la fila, `DECIMAL(18,2)`. Las columnas `*_cents` son enteros, para comparar sin redondeos.

## Banco

### `bank_txn`: la referencia, una fila por `bank_txn_id`

Las columnas de `bank_transactions.csv.gz` tal cual (ver `DATA_DICTIONARY.md`), más:

| Columna | Significado |
|---|---|
| `amount_cents` | Importe con signo: `+` abono, `−` cargo |
| `month` | Mes de `booking_date` |
| `src_file` | `<fase>/bank_transactions.csv.gz` |
| `raw_status` | `matched`, `no_raw_file` (su extracto no está en disco, esperado en TRAIN lite) o `missing_in_raw` (error) |
| `raw_files` | Extractos que contienen la línea; más de uno si hay solape |

### `bank_statement_line`: cada línea cruda, sin borrar ninguna

Campos de `bank_txn` leídos del extracto, más `src_file`, `src_line`, `account_id`, `bank`, `month` (mes del **fichero**), `format`, `match_key`, `key_ordinal`, `is_overlap_dup` y `bank_txn_id`.

- **Enlace:** multiconjunto por cuenta, fecha, importe, `ref1`, `ref2` y texto normalizado (`match_key`), con ordinal para las líneas idénticas legítimas.
- **`is_overlap_dup`:** la misma línea ya está en otro fichero. Se marca y no se borra.
- **`bank_txn_id` nulo:** línea sin movimiento. Es error si cae dentro del periodo de la vista, e informativo si es de otro periodo repetido por el solape.

### `statement_file`

Un fichero crudo por fila: `src_file`, `file_name`, `account_id`, `bank`, `month`, `format`, `lines`, `debits`, `credits`, `opening_cents`, `closing_cents` y `control_status` (`ok` si cuadran su registro 33 o sus saldos).

### `xval_account_month`

`account_id`, `month` (de `booking_date`), `raw_lines`, `norm_lines`, `raw_cents`, `norm_cents`, `status`:

| `status` | Significado |
|---|---|
| `OK` | Líneas y suma iguales |
| `DIFF` | No cuadra: es un error de la ingesta |
| `NO_RAW` | Sin extracto de ese mes (TRAIN lite antes de 2024-07) |
| `OUTSIDE_PHASE` | Líneas crudas de un mes ajeno a la vista |

## Partidas

Tablas tal cual, con `src_file` y `month` (mes de emisión): `ar_invoice`, `ap_invoice`, `credit_note`, `open_item_at_start`.

### `item`: lo que hay que usar para casar

| Columna | Significado |
|---|---|
| `item_id` | Único |
| `item_type`, `side` | `ARI`, `ARC`, `API`, `APC`; `AR` o `AP` |
| `entity`, `party_id`, `party_name` | En partidas de apertura, el nombre viene del maestro |
| `printed_number`, `printed_norm` | Número impreso, tal cual y solo con `A-Z0-9` |
| `issue_date`, `due_date` | `due_date` es nulo en abonos |
| `currency`, `net_amount`, `tax_amount`, `total_amount` | Neto e impuesto nulos en partidas de apertura |
| `open_amount`, `open_cents` | Saldo **al inicio de la fase**: `open_amount` del fichero en apertura, total en el resto. No es un saldo vivo |
| `is_opening` | Viene de `open_items_at_start` |
| `payment_terms`, `customer_po`, `our_po`, `payment_method`, `related_document`, `reason` | Nulos en partidas de apertura, porque ese fichero no los trae |
| `src_file`, `month` | Proveniencia |

**El número impreso no identifica una partida.** En TRAIN colisiona en el 91 % de las filas AP, el 13 % de AR y el 19 % de abonos. Hay que acotar por sociedad y tercero.

## Auxiliares

Todas llevan `bank`, `entity`, `month` (del nombre del fichero) y `src_file`.

| Tabla | Origen | Normalizadas |
|---|---|---|
| `payment_run_line` | `payment_runs/` | `document_norm` |
| `dd_batch_line` | `direct_debit_batches/` | |
| `card_settlement_line` | `card_settlements/` | `order_norm` |
| `cheque_deposit_line` | `cheque_deposits/` | `stub_norm` |

**`end_to_end_id` no es único dentro de una remesa:** la secuencia se reinicia por divisa. Una transferencia se identifica por `run_id`, `end_to_end_id`, `beneficiary`, `currency` y `transfer_amount`.

### `remittance_advice`

Una fila por aviso, también los del índice que no están en disco: `advice_id`, `src_file`, `entity`, `payer_name`, `received_date`, `month`, `format`, `layout`, `payment_ref`, `payment_date`, `currency`, `control_amount`, `lines_sum`, `n_lines`, `n_repeated`, `control_status`, `on_disk`.

`layout`: `csv`, `txt_en`, `txt_es`, `pdf`, `xlsx_document`, `xlsx_voucher`.

| `control_status` | Significado |
|---|---|
| `ok` | Las líneas suman lo anunciado |
| `ok_without_repeats` | Solo suman sin las líneas repetidas. Es una guardia: no aparece en `sample`, TRAIN lite ni LIVE |
| `sum_mismatch` | No suman. Suele ser un dato real: el pagador anuncia menos de lo que lista |
| `no_control` | El aviso no anuncia total (XLSX sin fila `TOTAL`) |
| `no_lines`, `error` | Error de la ingesta |
| `not_on_disk` | Está en el índice, no en disco (TRAIN lite) |

### `remittance_line`

`advice_id`, `line_no`, `document`, `document_norm`, `doc_date`, `gross`, `deduction`, `reason`, `paid`, `your_order`, `voucher`, `is_repeated`, `src_file`. Los abonos van en negativo. En `xlsx_voucher`, `document` es nuestra factura sin prefijo y `voucher` el número del cliente.

## PDFs

- **`pdf_index`:** `file`, `src_file`, `document_type`, `document_number`, `document_norm`, `entity`, `party_id`, `document_date`, `month`, `on_disk`, `size_bytes`. No se parsean durante la ingesta.
- **`pdf_text_cache`:** `file`, `text`. Se llena bajo demanda.

```bash
uv run --project pipeline bankcheck pdf --phase live FV25-0100122
```

Desde Python: `bankcheck.pdf_index.pdf_text(con, layout, "FV25-0100122")`, con la conexión en escritura.

## Maestros

`entity`, `bank_account`, `customer`, `supplier`, `category`, `payment_term`, `fx_rate`, con las columnas de `DATA_DICTIONARY.md`. En FINAL, `final/masters_final/` tiene prioridad fichero a fichero.

## Control

| Tabla | Contenido |
|---|---|
| `ingest_run` | Fase, raíz, versión, horas, errores y avisos |
| `file_manifest` | Cada fichero leído: tipo de fuente, segmento, tamaño, filas, estado |
| `ingest_issue` | Cada incidencia: severidad, código, fuente, segmento, fichero, línea, registros, detalle |
| `source_summary` | Filas por tipo de fuente y segmento |
| `table_fingerprint` | Filas y hash por tabla. Dos ingestas de los mismos ficheros dan lo mismo |

```sql
SELECT severity, code, source_type, sum(count) FROM ingest_issue GROUP BY ALL ORDER BY 1, 4 DESC;
```

Códigos de error (la ingesta sale con código distinto de 0):

`BAD_HEADER`, `BAD_VALUE`, `CSV_ROW_REJECTED`, `FILE_UNREADABLE`, `FILE_NOT_READ`, `MISSING_FILE`, `MASTERS_FINAL_MISSING`, `DUPLICATE_BANK_TXN`, `DUPLICATE_ITEM`, `STATEMENT_UNKNOWN_FORMAT`, `STATEMENT_PARSE_ERROR`, `STATEMENT_PARTIAL`, `STATEMENT_CONTROL`, `RAW_ONLY_LINE`, `MISSING_IN_RAW`, `XVAL_DIFF`, `BANK_LINE_OUTSIDE_PHASE`, `REMITTANCE_PARSE_ERROR`, `REMITTANCE_PARTIAL`, `REMITTANCE_NO_LINES`, y en LIVE y FINAL también `NO_RAW_STATEMENT` e `INDEXED_FILE_MISSING`.

`FILE_NOT_READ` es error solo si el fichero puede contener datos (`.csv`, `.gz`, extracto o aviso). Si no, es un aviso. También son avisos `EXTRA_COLUMNS` (columnas de más, que se ignoran) y `MASTER_SHRANK` (un maestro de `masters_final/` con menos de la mitad de filas que el anterior).

`FILE_UNREADABLE` incluye un `.csv.gz` truncado o dañado y un `index.csv` sin columna `file` o que no se puede decodificar. `BAD_VALUE` incluye una fecha no válida en un `index.csv`, que queda vacía, y una fila de `index.csv` sin nombre de fichero, que se descarta. `REMITTANCE_PARSE_ERROR` incluye un importe `NaN` o fuera de `DECIMAL(18,2)`, y `STATEMENT_PARSE_ERROR` una fecha imposible en un extracto.

Aviso que no bloquea: `FX_RATES_STALE`, cuando los tipos de una divisa en uso acaban más de 5 días antes del final de la fase.

Qué significa cada código y qué hacer con él está en [`INGESTA.md`](INGESTA.md).
