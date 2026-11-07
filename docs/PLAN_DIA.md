# Plan del sábado 7-nov · equipo 15

## Horario

| Hora | Qué | Quién |
|---|---|---|
| 08:45 | Quedada en la puerta de La Nave (Calle Cifuentes 5, Villaverde) | Todos |
| 09:00–09:30 | Acreditación. Portátiles en red `LA NAVE`. Comprobar que cada uno ejecuta `python -m bankrec.run --phase sample` | Todos |
| 09:30 | Presentación del reto: apuntar cualquier pista que den sobre el golden o el FINAL | Alex |
| 10:00 | **Primer envío LIVE** con el motor actual (valida el flujo y nos pone en la tabla) | Alex |
| 10:00–14:00 | Bucle de mejora (abajo) | Todos |
| 14:00 | Comida: **la mejor ventana de networking** | Alex |
| 14:45–17:30 | Bucle de mejora. Un envío LIVE solo cuando el holdout sube 2 puntos o más | Todos |
| 16:00–17:00 | Grabar el vídeo (3 min, 720p) y escribir la descripción | Alex + quien grabe |
| **17:30** | **Código congelado.** Desde aquí solo cambios para FINAL | Pablo decide |
| 18:00 | Sale FINAL: descarga + sha + ejecución (checklist de abajo) | Pablo + Alex |
| **18:45** | **Envío FINAL** (45 min de margen; en un empate gana el más temprano) | Alex lo pulsa, Pablo lo revisa |
| 19:30 | Cierre de envíos | — |
| 19:45 | Entrega del proyecto (vídeo, repo, descripción) y presentaciones | Alex |
| 20:30 | Revelación | — |
| 20:30–21:00 | Networking con todo el mundo relajado | Todos |

## Bucle de mejora (repetir)

1. `python -m bankrec.run --phase holdout` (o `--phase sample` si TRAIN no está) → mirar la tabla de segmentos con más fallos.
2. Escoger **un** segmento. `python -m bankrec.diff --phase holdout --segment <seg> -n 5` (o `--phase sample`, la misma fase del paso 1) → ver el patrón.
3. Arreglarlo citando POLICIES. Volver a medir. Si sube y no rompe nada, commit con «segmento X: antes → después».

## Reparto (propuesto, se ajusta en la puerta)

- **Pablo**: líder técnico del motor (`engine.py`) y del FINAL.
- **Alex**: orquesta Claude, Codex y Antigravity; pasa POLICIES a reglas; envíos; vídeo y presentación; networking.
- **Compañero de la Asociación + cuarto miembro**: QA por segmento con `bankrec.diff` y Claude. Cada uno se queda dos segmentos y propone la regla con 5 ejemplos.

## Protocolo de envíos LIVE

- Solo con `python scripts/submit.py LIVE out/sub_live.csv.gz`. Valida en local antes de gastar el hueco.
- Hay 20 al día, uno cada 180 s, y pedir la URL ya gasta el hueco.
- Cada envío se apunta solo en `out/envios.jsonl`.

## Checklist FINAL (18:00–18:45)

1. `python scripts/download.py final` → sha256 OK. Si la red de la sala va lenta, se descarga en el PC de casa.
2. Comprobar que existe `data/final/masters_final/`. El motor lo usa solo.
3. `python -m bankrec.run --phase final --no-score`, que debería tardar menos de 5 min.
4. Comprobaciones antes de enviar:
   - [ ] Cobertura 100 %: el validador local lo comprueba.
   - [ ] Distribución de categorías y excepciones parecida a la de LIVE: `python -m bankrec.stats final live`.
   - [ ] ZA01 (Sudáfrica, ZAR, facturas «KWI…») **no** sale todo `UNIDENTIFIED`.
   - [ ] El cambio de nombre de ZA01 del 1-abr: el maestro final trae el nombre nuevo. Los dos nombres casan.
5. `python scripts/submit.py FINAL out/sub_final.csv.gz` y escribir `FINAL`. **Solo cuenta el primer fichero válido.**
