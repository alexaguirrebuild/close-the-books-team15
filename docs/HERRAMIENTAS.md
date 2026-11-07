# Claude, Codex y Antigravity: quién hace qué

| Herramienta | Papel | Dónde corre |
|---|---|---|
| **Claude Code** | Orquesta: decide qué segmento atacar, integra, mide con el evaluador y decide qué se envía | PC de casa (Remote Control desde el portátil) |
| **Codex** | Ejecuta: escribe variantes de una regla en paralelo, cada una en su worktree | PC de casa |
| **Antigravity** | Volumen: lee PDF y avisos de pago a escala, escribe tests y analiza fallos por segmento | Proyecto en la nube conectado al repo de GitHub |

Los tres leen `AGENTS.md` (Claude lo hace a través de `CLAUDE.md`). Si cambias una regla del proyecto, cámbiala ahí y solo ahí.

## Codex (instalación: la hace Alex)

```
npm i -g @openai/codex
codex login
```

Perfil recomendado en `~/.codex/config.toml`:

```toml
model_reasoning_effort = "high"
approval_policy = "on-request"
sandbox_mode = "workspace-write"
```

**Variantes en paralelo** (las lanza Claude o una persona), una por worktree para no pisarse:

```
git worktree add .worktrees/v-ra -b variante/remesas
codex exec --sandbox workspace-write -C .worktrees/v-ra "Lee AGENTS.md. Mejora el segmento ar_1toN_remittance sin bajar el total en sample. Mide con python -m bankrec.run --phase sample y deja el resultado en VARIANTE.md"
```

Claude puntúa cada variante y fusiona solo la que gana.

## Antigravity (proyecto en la nube)

- Conectar el repo privado de GitHub. Subir **solo** `data/sample/` y `data/live/` (los datos son sintéticos).
- **Nunca** subir `.env` ni el token. El proyecto de Antigravity no envía nada a la plataforma.
- Tareas que le van bien:
  - Catalogar las frases de los PDF por tipo (`pdfs/index.csv`).
  - Sacar los layouts de los avisos de pago.
  - Escribir `tests/` con casos de `examples.json`.
  - Analizar un segmento entero y proponer la regla.
- El traspaso se hace por `docs/TRASPASO.md`: quien recibe lo lee antes que nada y lo sobrescribe al terminar.

## Remote Control

- El PC de casa **no se apaga ni se suspende**: Inicio → Energía → «Nunca». Si el PC tiene VPN, que sea a un servidor rápido o ninguna, porque el VPN lento ya nos costó una hora de descarga.
- Desde el portátil: Claude → la sesión de este PC. Los cálculos pesados (TRAIN, FINAL) corren allí y el portátil solo mira.
