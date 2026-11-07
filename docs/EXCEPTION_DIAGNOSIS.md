# Diagnóstico de excepciones

Cambios acotados a `bankrec/engine.py`, con pruebas en `tests/test_engine_exceptions.py`.
Las reglas siguen POLICIES §2, §3 y §5.5/§5.10. No usan identificadores, nombres ni importes del golden.

## Qué falla en cada clase

FP y FN se cuentan por `(bank_txn_id, item_id)`, como en `bankrec.excf1`.
Una asignación a otra factura puede producir un FP y un FN de la misma excepción aunque la etiqueta sea correcta.
Por eso conviene separar las partidas ausentes de las etiquetas incorrectas. La tabla describe los errores
observados y los mecanismos del código que pueden producirlos. Cuando el recuento de FP es cero, el riesgo
descrito no implica que se haya observado ese FP.

| Excepción | Causas de FP | Causas de FN y parche |
|---|---|---|
| `PARTIAL` | Saldo incompleto; una cuota final o liberación se compara contra el total; el reparto FIFO coloca una deducción en la última factura, aunque corresponda a otra. | Referencias/pagador no resueltos; diferencias explicables tratadas como pagos normales. El parche reparte descuentos/retenciones por factura y usa el saldo abierto del abono, antes de consumirlo. Las cuotas y retenciones documentales siguen dependiendo de `pdfclauses`. |
| `OVERPAYMENT` | Referencias truncadas que recuperan solo parte de un pago múltiple; saldo anterior incorrecto. | La búsqueda por contraparte solo contemplaba importes exactos y faltantes. Se añade una búsqueda conservadora: una única factura abierta, sin abonos y con exceso dentro del margen que ya emplea el motor. No se busca una factura por importe sin identificar al pagador. |
| `SHORT_DISCOUNT` | La rama agregada original no comprobaba el plazo de las facturas. Es un defecto de código; hay que distinguirlo de los FP observados. | Un cobro mezcla facturas completas y descontadas: la rama original exigía descuento en todas y terminaba marcando la última como `PARTIAL`. El parche verifica las fórmulas individuales y la fecha contable, con los tres días de gracia. Fuera de plazo aplica solo el efectivo y conserva el saldo. |
| `SHORT_BANK_CHARGES` | Una coincidencia con otra factura puede explicar por casualidad una diferencia pequeña como comisión. | País del pagador y circuito de transferencia no equivalen: el texto puede declarar `/CHGS/`, SHA/BEN o una transferencia internacional sin `orig_currency`. Se admite esa evidencia y se mantienen simultáneamente los límites de 60 EUR y 2 %. |
| `SHORT_WITHHOLDING` | Una asignación incorrecta o una deducción por otro motivo puede coincidir con un porcentaje fiscal. | Retenciones repartidas entre varias facturas y avisos no leídos. El nuevo reparto admite exclusivamente los países y tipos de POLICIES. No reconstruye documentos que el matcher no encontró. |
| `SHORT_DISPUTE` | Un aviso asociado a otro cobro/factura puede trasladar una disputa a una partida incorrecta. | El motivo suele estar en el aviso, no en el banco. Los casos sin fila o sin aviso necesitan el parser de remesas; el motor no debe inventar una disputa a partir de una diferencia de importe. |
| `DUPLICATE_PAYMENT` | Una factura cerrada por un match anterior erróneo parece duplicada; colisiones de referencias y FIFO con importes repetidos. | Sin referencia no se buscan partidas cerradas; `emit_run_rows` no detecta duplicados y existe una excepción genérica para cobros de facturas domiciliadas. El parche impide descontar saldo otra vez al emitir un duplicado. Ampliar la detección requiere comprobar el pago previo, su divisa y las devoluciones. |
| `UNIDENTIFIED` | Es el destino común de cobros cuyo pagador, referencia o aviso no se resolvió. No demuestra que falte una regla de excepción. | Un match especulativo puede asignar un ingreso realmente desconocido. El primer intento de ampliar sobrepagos creó estos errores y se restringió a una sola factura abierta. POLICIES prohíbe forzar partidas solo por importe. |
| `RETURNED` | Entre cuotas iguales, elegir la colección más reciente puede reabrir otra factura. | El motor inicial solo trata recibos domiciliados; faltan cheques, tarjetas y transferencias rechazadas. El parche da prioridad al lote citado. Una fila futura solo sirve de enlace documental si dice `re_presentation=Y`, pues identifica un recibo anterior devuelto (§5.10); una colección futura ordinaria se descarta. Los otros circuitos necesitan su propio enlace al pedido/cheque/transferencia original. |
| `FX_DIFFERENCE` | Factura incorrecta por homónimos o FIFO; un abono reembolsado no debe recibir esta etiqueta. | Faltan partidas o referencias; el importe parcial se calculaba con el cambio de referencia aunque constara el original del banco. El parche usa el cambio bancario para los importes aplicados y mantiene vacía la excepción de los abonos. La tolerancia FX amplia aún merece una revisión separada para descuentos tardíos en otra divisa. |

## Parches aplicados

1. `emit`: el duplicado conserva el saldo; una devolución lo sigue reabriendo.
2. `to_item`: inversa de `to_bank` con el importe original del banco, si consta.
3. `settle`: compensación por el saldo abierto de los abonos; estos conservan su excepción vacía en FX.
4. `invoice_deductions`: combinaciones de descuentos/retenciones que cuadran con el efectivo. Solo misma divisa, facturas con saldo íntegro y hasta 14 candidatas; el límite evita una búsqueda descontrolada.
5. Descuento agregado: comprueba el plazo y que no se esté descontando una factura ya parcialmente pagada.
6. Comisiones internacionales: evidencia del extracto, sin relajar los dos límites.
7. Devoluciones SEPA: lote citado antes que recencia; respetar el mandato y usar las re-presentaciones como enlace al recibo original.
8. Referencias: fecha de emisión y entidad válidas, también en referencias truncadas y sufijos.
9. Sobrepago por contraparte: una única factura abierta y ningún abono compensable.

## Validación y entorno

Se comparan el motor de `HEAD` y una copia fija del candidato, con `PYTHONHASHSEED=0`, los mismos datos,
plugins y dependencias. El evaluador oficial `vendor/bankrec_score.py` permanece intacto.

El Python del proyecto no tenía `pypdf` ni `openpyxl`. Las mediciones reflejan ese entorno: no deben
compararse con ejecuciones que sí lean PDF/XLSX. La caché PDF de esta ejecución contenía textos vacíos.
Para recuperar el rendimiento documental hay que habilitar esas dependencias e invalidar las entradas
vacías de caché antes de volver a medir; no cambiar las reglas de excepción para compensar su ausencia.

El cargador original de holdout construye primero todo TRAIN. La validación usa
`out/v-engine/validate_holdout.py`: reconstruye los mismos saldos de `_cut_holdout`, filtra antes de crear
los objetos y después ejecuta ambos motores sobre la misma cartera, restaurando los saldos entre ellos.
El golden se usa exclusivamente para reconstruir esos saldos iniciales y evaluar, igual que en el CLI local.
No se incorpora al matcher. El cargador de producción no se modifica.

### Sample

8.499 movimientos. Nota **79,92 → 80,79**; macro-F1 **0,654794 → 0,692801**;
F1 de asignaciones **0,826578 → 0,827325**; exactitud por movimiento **0,849865 → 0,852218**.
Ningún segmento pierde aciertos. Los resultados completos están en
`out/v-engine/{baseline,candidate}_score_sample.json`.

| Clase | FP antes → después | FN antes → después | F1 antes → después |
|---|---:|---:|---:|
| PARTIAL | 280 → 266 | 155 → 151 | 0,5885 → 0,6017 |
| OVERPAYMENT | 4 → 6 | 16 → 12 | 0,4444 → 0,5714 |
| SHORT_DISCOUNT | 0 → 0 | 21 → 14 | 0,9556 → 0,9708 |
| SHORT_BANK_CHARGES | 0 → 1 | 5 → 2 | 0,6154 → 0,8235 |
| SHORT_WITHHOLDING | 0 → 0 | 5 → 5 | 0,5455 → 0,5455 |
| SHORT_DISPUTE | 0 → 0 | 144 → 144 | 0,5294 → 0,5294 |
| DUPLICATE_PAYMENT | 3 → 3 | 21 → 21 | 0,5385 → 0,5385 |
| UNIDENTIFIED | 450 → 443 | 0 → 0 | 0,4921 → 0,4960 |
| RETURNED | 6 → 3 | 34 → 32 | 0,8980 → 0,9105 |
| FX_DIFFERENCE | 2 → 2 | 66 → 66 | 0,9407 → 0,9407 |

Hay dos costes visibles: aumentan los FP de sobrepago y de comisiones, aunque mejora el F1 de ambas clases.
Las clases fiscales, disputas y FX siguen limitadas principalmente por partidas ausentes. En sample,
`SHORT_WITHHOLDING` pierde sus cinco casos como filas ausentes; `SHORT_DISPUTE` pierde 138 así y seis con
excepción vacía. Esto apunta al matcher y a los documentos, no a introducir más etiquetas en el motor.

### Holdout

Pendiente de terminar la comparación de 199.987 movimientos sobre la misma cartera inicial.

## Qué queda fuera de este parche

- Lectura fiable de remesas PDF/XLSX y de cláusulas de facturas. Es la siguiente prioridad si los FN son partidas ausentes.
- Devoluciones de cheque, contracargo y transferencia: enlazar la operación original y reabrir todas sus partidas.
- Duplicados sin referencia: buscar pagos previos de la misma contraparte con evidencia suficiente, sin confundirlos con nuevas cuotas.
- Referencias tipográficas y cobros agrupados en divisa: recuperar el conjunto completo antes de decidir la excepción.

No se han hecho envíos, commits ni push.
