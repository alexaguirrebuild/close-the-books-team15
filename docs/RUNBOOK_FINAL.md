# Procedimiento de FINAL

Solo cuenta el primer fichero válido. Este procedimiento genera el fichero; **el envío lo lanza el usuario, con su OK expreso**.

| Hora | Qué |
|---|---|
| 16:30 | Última regla nueva |
| 17:00 | Ensayo cronometrado en LIVE (`--phase live`) |
| 17:30 | Código congelado |
| 18:00 | Sale FINAL |
| 18:45 | Objetivo de envío (en un empate gana el más temprano) |
| 19:30 | **Cierran los envíos** |
| 19:45 | Entrega del proyecto |

## Antes de las 18:00

- `data/train/`, `data/live/` y `data/masters/` en disco. Sin las fases anteriores, el motor se niega a arrancar y `validate` da `ITEM_UNVERIFIED`.
- `HK_TOKEN` en el entorno o en `.env`, nunca en el repo.
- Solo una ejecución completa a la vez en cada máquina: si hay otro pipeline en marcha, todo tarda el triple.

## A las 18:00

**1. Descargar.** Desde la raíz del repo, sin descomprimir nada a mano:

```bash
uv run python scripts/download.py final
```

| Salida | Qué significa |
|---|---|
| `no disponible todavía` | `config.json` aún no lo publica. Repite en un minuto |
| `el servidor responde 403` | Está anunciado, pero aún no se puede bajar. Repite en un minuto |
| `no trae sha256` | No se puede comprobar. Usa `--sin-sha` solo si la organización lo confirma |
| `SHA256 NO COINCIDE` | El fichero se borra solo. Repite |
| `no tiene la forma esperada` | El archivo no trae lo que dice la documentación. Avisa antes de seguir |

**2. Generar.** Encadena ingesta, enlace de avisos, motor y validación estricta, y para en el primer fallo. Nunca sube nada.

```bash
uv run python scripts/final_run.py
```

Aparta el fichero anterior como `out/sub_final.anterior.csv.gz` y borra la caché de PDF.

**3. Comprobar.**

- [ ] El comando termina con `Fichero listo y validado`.
- [ ] **Sociedades nuevas:** el comando lista la filial nueva y qué le ha pasado. No debe salir casi todo `UNIDENTIFIED`, y sus facturas llevan otra numeración (`KWI…`). Mira a mano los ejemplos que imprime.
- [ ] La filial se renombra el 1 de abril: comprueba que hay cobros con partida antes y después de esa fecha.
- [ ] **Distribución parecida a LIVE:** `uv run python -m bankrec.stats final live`. Necesita `out/explain_live.csv.gz`, que deja el ensayo.
- [ ] Ninguna incidencia nueva en `out/final/ingest_report.json`, sobre todo `MASTER_NOT_UPDATED` y `FX_RATES_STALE`.

**4. Enviar.** Solo el usuario, en su terminal:

```bash
! uv run python scripts/submit.py FINAL out/sub_final.csv.gz
```

Valida otra vez, enseña el fichero con su SHA-256 y pide escribir `FINAL`. Un fichero rechazado no gasta el envío; uno válido, sí, aunque sea el equivocado. **Pedir la URL de subida ya consume un hueco**, así que no se envía por probar.

## Si la ingesta da errores

El comando para y deja `out/final/ingest_report.json` y `cache/warehouse/final.failed.duckdb`.

| Código | Qué se pierde | ¿Seguir con `--allow-ingest-errors`? |
|---|---|---|
| `REMITTANCE_*` en pocos ficheros | Esos avisos | **Sí, es razonable** |
| `BAD_HEADER` (falta una columna o está repetida) o `FILE_UNREADABLE` en una tabla de raíz o un maestro | **La tabla entera queda vacía** | **No.** Vuelve a descargar o adapta `pipeline/bankcheck/tables.py` |
| `FILE_NOT_READ` como error (`.csv`, `.gz`, extracto o aviso sin leer) | Nada todavía | Mejor no: mira el fichero y quítalo si es ruido, o adapta el cargador si son datos |
| `STATEMENT_*`, `XVAL_DIFF`, `RAW_ONLY_LINE`, `MISSING_IN_RAW` | El contraste con el extracto de esa cuenta | Solo tras mirar qué cuenta afecta |
| `BAD_VALUE`, `CSV_ROW_REJECTED` | Esas filas o valores | Según cuántos sean |

Con `--allow-ingest-errors`, la validación final dice NO VÁLIDO por `INGEST_HAD_ERRORS`, aunque el formato esté bien. Si es el único error y `official_format_ok` es `true` en `out/final/validate__sub_final.json`, enviar es una decisión del usuario.

Para enviar en ese caso, la persona que decide añade `--accept-ingest-errors`:

```bash
! uv run python scripts/submit.py FINAL out/sub_final.csv.gz --accept-ingest-errors
```

`submit.py` lee `out/final/validate__sub_final.json` y solo sigue si `official_format_ok` es `true`, `INGEST_HAD_ERRORS` es el **único** error y `file_sha256` coincide con el fichero que se envía. Avisa en pantalla, sigue pidiendo escribir `FINAL` y lo deja anotado en `out/envios.jsonl` con `accepted_ingest_errors`. Si `bankcheck validate` no llega a escribir un informe nuevo, no envía. En este camino `final_run.py` termina con código distinto de 0 y «NO validado»: la casilla de «Fichero listo y validado» no se cumple, pero sí debe mirarse el informe de sociedades nuevas que imprime.

En cualquier envío, si el fichero cambia desde que se validó, `submit.py` para antes de pedir la URL y no gasta hueco.

**No bloquean la publicación, pero hay que mirarlos:**

- `EXTRA_COLUMNS`: una columna de más en un fichero. Se ignora y las demás se leen por su nombre, también si cambia el orden.
- `FILE_NOT_READ` como aviso: un fichero que no puede ser un dato, como un `README`, o cualquier fichero de `masters_final/` que no sea un maestro conocido. Si es un maestro nuevo con datos que hagan falta, hay que adaptar el cargador.
- `MASTER_SHRANK`: un maestro de `masters_final/` trae menos de la mitad de filas que el anterior. Lo sustituye entero, no se fusiona, así que puede faltar el resto.

## Si FINAL no trae un maestro

`final/masters_final/` manda fichero a fichero. Lo que no traiga se toma de `masters/` y se avisa: `preflight` en el motor y `MASTER_NOT_UPDATED` en la ingesta. No para, pero hay que mirarlo:

- Sin `entities.csv`, los cobros nacionales de la filial nueva con un pequeño descuadre pueden salir como `SHORT_BANK_CHARGES`.
- Sin `customers.csv` o `suppliers.csv`, la filial nueva no tiene terceros y saldrá casi todo sin partida.
- Sin `fx_rates.csv`, los tipos acaban el 31 de diciembre de 2025 y salta `FX_RATES_STALE`.

## Sin comprobar

Este flujo solo se ha probado con LIVE y con datos sintéticos: **FINAL real no existe hasta las 18:00**. Qué ficheros y formatos, y qué trae `masters_final`, se confirma al descargarlo.
