# Entrega del proyecto (plantilla para rellenar el sábado a las 16:00)

Para el formulario «Proyecto» (descripción, de 1.500 caracteres como máximo) y el vídeo (5 min como máximo, recomendado 3 min a 720p). Las cifras se rellenan con lo medido, nunca de memoria.

## Descripción

**Qué hace.** Concilia cada movimiento bancario de Valtermia con sus facturas y explica el porqué de cada asignación. Cada fila sale con la regla que la decidió y su evidencia (referencia, contraparte, remesa, aviso de pago, cláusula del PDF). Lo que no sabe, lo marca en vez de forzarlo.

**Cómo.**
1. **Las políticas escritas pasan a ser reglas ejecutables.** Cada regla cita su apartado de POLICIES.md.
2. **Las reglas no escritas se aprenden de 4 años de TRAIN:** IBAN y alias de pagadores, patrones de texto por categoría y homónimos.
3. **Estado por partida:** saldo abierto, plazos y devoluciones que reabren.
4. **Trazabilidad:** `explain_<fase>.csv.gz`, con regla y evidencia por fila.

**Resultados.** Holdout de TRAIN 71,74 (viernes; actualizar) · LIVE __ · automático con confianza alta __ % · a revisión humana __ %.

**Stack.** Python estándar (sin dependencias), Claude Code como orquestador, Codex para variantes en paralelo y Antigravity para leer los PDF y avisos a escala.

**Qué haríamos después.** Una cola de revisión humana para las de confianza baja y que el modelo aprenda de cada corrección. Es el bucle que convierte el conocimiento de un equipo de tesorería en autonomía segura.

**Equipo.** Pablo · Alex · __ · __

## Vídeo (3 min)

| Tiempo | Qué se ve | Qué se dice |
|---|---|---|
| 0:00–0:20 | Una línea de banco real del dataset sin referencia útil | «Esto es lo que recibe un tesorero cada mañana, multiplicado por 200.000» |
| 0:20–1:00 | Pipeline: políticas → reglas → aprendido de TRAIN → estado | Cómo decide, en llano |
| 1:00–1:50 | `explain`: una fila con su regla y evidencia; un caso difícil resuelto (aviso de pago con disputa, FX) | Trazabilidad: por qué casa cada cosa |
| 1:50–2:30 | Tabla de segmentos: dónde acierta y dónde no | Damaging admission: lo que todavía falla y por qué |
| 2:30–3:00 | % automático frente a % a revisión, y lo siguiente | Cómo lo usaría un equipo de verdad |
