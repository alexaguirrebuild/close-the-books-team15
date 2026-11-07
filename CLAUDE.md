@AGENTS.md

# Flujo obligatorio para subir la nota

Esto se suma a `AGENTS.md`. En juego limpio, seguridad y envíos manda `AGENTS.md`. En cómo se mide una regla manda este fichero: `sample` sirve para buscar causas, pero decide el holdout.

## 1. Plan mode antes de tocar nada

Todo cambio en `bankrec/`, `tests/` o `pipeline/` empieza en plan mode (Shift+Tab o `/plan`). No se crea ni se edita ningún fichero hasta que una persona apruebe el plan. Leer datos y medir sí se puede.

El plan debe decir:

1. **Qué ataca:** segmento o clase de excepción, y cuántos movimientos, filas o puntos se pierden ahí.
2. **Causa medida:** vista en casos reales, no supuesta. Si aún no está medida, medirla es la primera tarea.
3. **Regla:** qué hace exactamente y qué apartado de POLICIES implementa.
4. **Evidencia que exige,** y qué pasa cuando no la hay. Lo normal es no responder.
5. **Tests,** incluidos los de lo que la regla **no** debe hacer.
6. **Medición,** y una estimación de puntos marcada como estimación.

## 2. TDD

1. Escribe el test.
2. Ejecútalo y comprueba que falla por el motivo esperado, no por un error de escritura.
3. Escribe el código mínimo para que pase.
4. Ejecuta **toda** la suite.
5. Haz commit solo en verde: `uv run --group dev python -m pytest tests && git commit ...`.

- Modelo de test de una regla: `tests/test_terceros.py` (`Engine.__new__(Engine)`, partidas pequeñas y `settle` simulado).
- Cada regla lleva casos de «no fuerza»: dos candidatos, importe que no cuadra, otra sociedad, partida ya pagada.
- Si un test falla, comprueba primero sus datos: la tolerancia de 2 céntimos ya ha convertido importes parecidos en un falso fallo.

## 3. Cómo se mide

| Medición | Para qué | Coste |
|---|---|---|
| `python -m bankrec.run --phase sample` | Encontrar causas. **No decide** | ~30 s |
| Simulación con `vendor/bankrec_score.py` sobre `out/explain_holdout.csv.gz` | Reglas que solo añaden o quitan filas | ~1 min |
| `python -m bankrec.run --phase holdout` | **Decide si una regla entra** | ~16 min y ~5 GB; uno a la vez por máquina |
| Envío a LIVE | La prueba real | Lo decide una persona; ver límites en `AGENTS.md` |

`python -m bankrec.diff --phase holdout --segment <seg> -n 5` enseña los fallos de un segmento, y `out/explain_<fase>.csv.gz` la regla y la evidencia de cada fila.

**Una regla entra solo si:**
- [ ] Sube el holdout frente a la referencia vigente.
- [ ] La precisión no cae más de 0,02.
- [ ] `unidentified` y `unidentified_lookalike` no empeoran de forma apreciable. Son la señal de que estamos forzando.
- [ ] Dos ejecuciones con `PYTHONHASHSEED=0` dan el mismo fichero.
- [ ] LIVE sigue tardando menos de 20 min, para no comprometer FINAL.

## 4. Evidencia exacta: adivinar resta

Solo se asigna con referencia o importe exactos y un único candidato. Si no, no se responde.

| Lo que hemos aprendido | Dato |
|---|---|
| Asignar sin evidencia exacta baja la nota | Holdout de 82,33 a 81,49 |
| `sample` puede engañar | La misma regla dio 85,49 en `sample` y 81,49 en holdout |
| `sample` no contiene los formatos que cambian después | Los avisos voucher y las remesas agregadas fallaban en LIVE y no se veían ahí |
| El holdout predice bien LIVE, sin ser exacto | 86,83 frente a 86,84; 89,07 frente a 88,63 |
| Una suma exacta deja de identificar con muchas candidatas | Límite usado: 18 a 26 partidas, y siempre solución única |

No pongas en el código ids, nombres, bancos ni entidades concretos: FINAL trae una sociedad nueva y clientes renombrados.

## 5. Trabajar sin pisarse

- **Antes de empezar,** lee `docs/TRASPASO.md` y di en el canal del equipo qué segmento coges. Un segmento por persona.
- **Una rama por regla,** en su propio worktree, desde la rama de integración que indique `docs/TRASPASO.md`:
  `git worktree add ../Paella-<regla> -b <regla> origin/<rama-de-integracion>`. Borra el worktree al terminar.
- **Un commit por regla,** con el segmento y la nota antes y después.
- **PR de 200 ficheros como máximo.** A `Dev` solo entra lo que ha pasado el holdout.
- **Al terminar,** actualiza `docs/TRASPASO.md` con la nota y lo que queda.

## 6. Gastar poco

- Una sesión por regla. Una sesión larga encarece cada paso; usa `/compact` si hay que seguir en ella.
- Pide los resultados agregados: cifras y unos pocos ejemplos, no volcados de ficheros.
- Los scripts que usan el motor necesitan `if __name__ == "__main__":`. El motor usa `multiprocessing`, y sin esa guarda cada proceso hijo vuelve a ejecutarlo todo.
- En los análisis con DuckDB, limita memoria e hilos: `SET memory_limit='2GB'; SET threads=2`. Un TRAIN completo en Python puro agota la máquina y frena a los demás.
