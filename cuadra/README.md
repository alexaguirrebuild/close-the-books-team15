# Cuadra

Revisión de casaciones bancarias en el navegador: lee los CSV de una carpeta local, indexa con DuckDB-WASM y genera un `submission.csv` validado. Sin backend ni login. Los datos no salen del navegador.

## Arrancar

```bash
npm install
npm run dev        # http://localhost:5173
```

Usa Chrome o Edge: permiten elegir la carpeta y reabrirla. En otros navegadores se usa el selector de carpetas clásico.

Para probarla sin datos reales, la pantalla inicial ofrece dos conjuntos inventados en `public/sample`, uno con golden y otro sin él. Se regeneran con `npm run sample`.

## Qué poner en la carpeta

Se reconocen por nombre, en cualquier subcarpeta:

| Fichero | Uso |
| --- | --- |
| `bank_transactions.csv` | Obligatorio |
| `ar_invoices.csv`, `ap_invoices.csv`, `credit_notes.csv` | Al menos uno |
| `open_items_at_start.csv` | Si existe, marca qué partidas están abiertas |
| `propuestas.csv` | Salida del pipeline batch |
| `*golden*.csv`, `*ground_truth*.csv` | Si existe, activa la puntuación |
| `masters/categories.csv`, `customers.csv`, `suppliers.csv`, `fx_rates.csv`, `payment_terms.csv` | Lista de category, nombres de tercero, conversión y descuentos |
| `pdfs/*.pdf`, `pdfs_text.csv` | Documentos y su texto indexado |

Los nombres de columna se resuelven por alias en `src/data/schema.ts`. Si falta una obligatoria, la pantalla de carga pide elegirla a mano.

## Indexar los PDF

```bash
npm run pdf:index -- /ruta/a/la/carpeta
```

Extrae la capa de texto de `pdfs/` a `pdfs_text.csv`, sin OCR. Las páginas sin texto se listan en `pdfs_sin_texto.txt` para tratarlas una a una, por ejemplo con un LLM.

## Teclado

Pulsa `?` dentro de la app para ver todos los atajos.

| Tecla | Acción en la mesa |
| --- | --- |
| `Enter` | Confirmar y pasar al siguiente |
| `1`–`9`, `↑` `↓` `Espacio` | Marcar partidas |
| `A` `S` `D` | Aplicar una combinación que cuadra |
| `F` | Cambiar a la partida más antigua (FIFO) |
| `Tab` | Editar importes |
| `E`, `G`, `U` | Excepción, category, UNIDENTIFIED |
| `/`, `O` | Buscar partida, abrir PDF |
| `J` `K`, `Z` | Saltar, deshacer |
| `Alt`+`1`…`4` | Panel, cola, mesa, exportar |

## Decisiones guardadas

Cada decisión se añade a un registro en IndexedDB, asociado a la carpeta y al tamaño y fecha de `bank_transactions.csv`. Si cambia ese fichero, la revisión empieza de cero. Para repartir trabajo, cada persona usa "Descargar mi revisión" y otra "Importar revisión"; lo ya decidido no se pisa.

## Supuestos que hay que contrastar

El formato oficial no estaba disponible al construir esto, así que estos puntos son supuestos:

**Puntuación (`src/domain/score.ts`).** Es un cálculo local de la fórmula publicada, no el scorer oficial. Contrasta `submission.csv` con él.

- Allocation F1 compara pares (movimiento, partida) con importe dentro de max(0,01; 0,5 % del importe golden).
- Una transacción es correcta si coinciden todas sus filas, su excepción y, sin partida, su category.
- Exception macro-F1 se calcula por movimiento, sobre las clases que aparecen en golden o en el envío.

**Exportación (`src/data/exportSubmission.ts`).**

- Las filas sin partida llevan en `amount` el valor absoluto del movimiento.
- `amount` conserva el signo que la partida tiene en su fichero.
- En un pago corto, `amount` es lo pagado, no el saldo de la partida.
- Las propuestas sin revisar se exportan por defecto; se puede cambiar en Exportar.

**Otros.**

- `fx_rates.csv`: 1 unidad de base equivale a `rate` unidades de quote; se usa el último tipo hasta la fecha del movimiento.
- El tercero de un movimiento se deduce de la propuesta, del número impreso o del nombre en el concepto, porque el banco no lo trae como columna.

## Pruebas

```bash
npm test                          # unitarias e integración con DuckDB real
PW_CHANNEL=chrome npm run test:e2e   # teclado real en Chrome
```

Para medir a escala: `node scripts/gen-sample.mjs --out public/sample/big --txns 1500000 --items 5500000 --golden`, y abre `/?demo=big&autoload=1`. Bórralo antes de compilar.
