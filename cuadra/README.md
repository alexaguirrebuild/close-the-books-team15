# Cuadra

Revisión de conciliaciones bancarias en el navegador. Lee los datos de una carpeta local, indexa con DuckDB-WASM y comprueba el resultado con las reglas de `FORMAT.md`. Es para revisar, diagnosticar y hacer la demo: el envío de LIVE y FINAL sale del motor del equipo. Sin backend ni login: los datos no salen del navegador.

## Arrancar

```bash
npm install
npm run dev        # http://localhost:5173
```

Usa Chrome o Edge: permiten elegir la carpeta y reabrirla. En otros navegadores se usa el selector de carpetas clásico.

Para probarla sin datos, la pantalla inicial ofrece dos conjuntos inventados en `public/sample`, con las mismas columnas que los reales, sin comprimir porque el pre-commit del equipo no admite `.csv.gz`. Se regeneran con `npm run sample`.

## Qué carpeta elegir

La carpeta raíz, con las mismas reglas que el pipeline (`pipeline/bankrec/roots.py`). Cuadra detecta las fases y solo lee la que elijas:

```
<raíz>/
  live/  train/  final/  o  bankrec-docs-and-masters/sample/
  masters/  o  bankrec-docs-and-masters/masters/
  out/<fase>/                       lo que escribe el pipeline
```

`final/masters_final/` sustituye a los maestros generales en FINAL. También se puede elegir directamente una carpeta que sea un único conjunto de datos.

### Propuestas

Dentro de `out/<fase>/`, por este orden: `propuestas*`, el fichero fusionado (`submission*`), `baseline*` y, por último, una etapa suelta. Se puede cambiar en la pantalla de carga.

El fichero fusionado solo lleva las cinco columnas oficiales: la confianza y el origen se toman de `out/<fase>/matches/*.csv.gz`, que el pipeline escribe con `bank_txn_id,item_id,amount,category,exception,source,confidence,advice_id,line_no`. También se lee una columna `reason` si existe.

### Documentos

```bash
npm run pdf:index -- /ruta/a/live --out /ruta/a/out/live/pdfs_text.csv
```

Extrae la capa de texto de `pdfs/` y `remittance_advices/`, sin OCR. Con `--out` las carpetas de datos quedan tal como se entregaron.

## Las tres pantallas

- **Resumen**: qué queda por hacer y, con golden, la puntuación y en qué segmentos se pierden movimientos.
- **Revisión**: la lista y el caso en la misma pantalla. La vista Prioridad ordena por lo que más puede subir la puntuación, que cuenta líneas, pares y excepciones raras, no dinero.
- **Validar**: comprueba el formato oficial. Solo en TRAIN y `sample` descarga `submission_<fase>.csv.gz`; en LIVE, FINAL o una carpeta sin fase conocida no genera fichero de envío, y lo decidido se pasa con "Descargar mi revisión".

## Teclado

Pulsa `?` dentro de la app para ver todos los atajos.

| Tecla | Acción |
| --- | --- |
| `Enter` | Confirmar y seguir. Si falta excepción, la pide |
| `Shift`+`Enter` | Guardar sin excepción |
| `1`–`9`, `↑` `↓` `Espacio` | Marcar partidas |
| `A` `S` `D`, `F`, `Tab` | Combinación que cuadra, la más antigua, importes |
| `E`, `G`, `U` | Excepción, categoría, sin identificar |
| `/`, `O`, `M` | Buscar partida, abrir documento, más partidas del tercero |
| `J` `K` | Movimiento siguiente o anterior; el borrador se conserva |
| `V`, `B`, `L` | Cambiar de vista, buscar movimiento, ocultar la lista |
| `Shift`+`C` | Confirmar en lote, en la vista Listas |
| `Z` | Deshacer |
| `Alt`+`1`…`3` | Resumen, Revisión, Validar |

## Reglas que aplica por ti

Salen de `POLICIES.md` y están en `src/domain/policy.ts`.

- El importe sigue a la excepción: `SHORT_*`, `FX_DIFFERENCE` y `OVERPAYMENT` liquidan el saldo entero; `PARTIAL` y `SHORT_DISPUTE`, lo pagado.
- La excepción es de cada fila. Al aplicarla a toda la línea, los abonos quedan sin excepción.
- Una línea lleva partidas o categoría, nunca ambas. `UNIDENTIFIED` es categoría y excepción a la vez.
- No se ofrece una partida emitida después del movimiento; en las etiquetas nunca ocurre.

## Decisiones guardadas

Cada decisión se añade a un registro en IndexedDB, asociado a la carpeta, la fase y el tamaño y fecha de `bank_transactions`. Si cambia ese fichero, la revisión empieza de cero; si solo cambian las propuestas, se conserva. Para repartir trabajo, cada persona usa "Descargar mi revisión" y otra "Importar revisión"; lo ya decidido no se pisa.

## Pruebas

```bash
npm test                               # unitarias e integración con DuckDB real
PW_CHANNEL=chrome npm run test:e2e     # teclado real en Chrome

# Con los datos reales, que no están en el repositorio:
BANKREC_DATA_ROOT=/ruta/raiz npx vitest run src/data/real.test.ts
BANKREC_DATA_ROOT=/ruta/raiz BANKREC_PHASE=live PW_CHANNEL=chrome npx playwright test -g "real data"
```

`real.test.ts` pasa el envío por `starter/bankrec_score.py`: el fichero debe ser válido y la puntuación, el diagnóstico y los segmentos de Cuadra deben coincidir con los oficiales. Sin `BANKREC_DATA_ROOT` se omite.
