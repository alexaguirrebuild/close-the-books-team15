# Guion de la demo

Unos 75 segundos con los datos reales de `sample`, que tiene golden, y la salida del motor. Todo se hace con el teclado salvo donde se indica.

## Antes de grabar

1. Genera la salida con el código que se entrega, desde la raíz del repositorio:

   ```sh
   python -m bankrec.run --phase sample --no-score
   ```

   Escribe `out/explain_sample.csv.gz`. Cuadra calcula la nota por su cuenta, así que `--no-score` no le quita nada.
2. Arranca Cuadra con `cd cuadra && npm run dev` y ábrela en Chrome.
3. Elige la carpeta raíz, la fase Sample y «Cargar datos». Si `data/` son enlaces simbólicos, el navegador no los ve: elige la carpeta con los datos de verdad, la fase Sample y, con «Elegir su fichero explain», el `out/explain_sample.csv.gz` del motor.
4. Espera a que Resumen termine de calcular la puntuación.

Para repetirla desde cero, borra los datos del sitio en el navegador: las decisiones se guardan entre recargas.

## Guion

| Tiempo | Qué hacer | Qué decir |
| --- | --- | --- |
| 0:00 | Resumen, sin tocar nada | «Tesorería recibe 200.000 movimientos en un semestre. El motor propone; las personas revisan lo que importa. Aquí se ve cuánto queda y qué hacer ahora». |
| 0:12 | Señalar la tabla «Dónde se pierden movimientos» | «La misma puntuación que el servidor, comprobada cifra a cifra, y en qué tipo de caso falla el motor». |
| 0:22 | `Enter` para entrar en Revisión | «Lista y caso juntos. La cola no va por importe: la nota cuenta líneas y pares, así que primero van las líneas con más partidas y las excepciones raras». |
| 0:32 | `V` hasta Con excepción; moverse con `J` hasta un caso con dos partidas | «El por qué de cada propuesta, de un vistazo: la regla del motor y su evidencia. Y una barra: es el movimiento, y se cierra cuando las partidas cuadran». |
| 0:42 | `Tab`, escribir un importe menor, `Esc` | «Si no cuadra, lo dice en palabras». |
| 0:47 | `E`, elegir Corto por descuento | «Elijo la excepción y el importe se ajusta solo a la política: un descuento aceptado liquida la factura entera». |
| 0:55 | `Enter`, y luego `Z` | «Confirmar es una tecla; deshacer, otra». |
| 1:02 | Clic en un segmento desde Resumen (`Alt`+`1`) | «Y un segmento abre justo esos errores, con lo que esperaba el golden. Es como afinamos el motor». |
| 1:10 | `Alt`+`3` | «Comprueba el formato con las reglas del servidor, y nunca bloquea lo que el servidor acepta. El envío real sale del motor; Cuadra es donde las personas revisan». |

## Sin el motor a mano

`/?demo=train&autoload=1` abre datos inventados. Traen confianza, así que además se puede enseñar `V` hasta Listas, `Shift`+`C` y `Enter`: lo que tiene confianza alta se confirma en lote.
