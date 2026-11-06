# TRASPASO

Quien recibe este repo (Claude, Codex, Antigravity o una persona) lee esto antes que nada y lo sobrescribe al terminar su parte. Si algo falla dos veces, se escribe aquí y se para.

- **Último que escribió:** Claude (PC de Alex), viernes 6-nov por la noche
- **Pedía Alex:** dejar listo para mañana el motor v0, el evaluador, los scripts de descarga y envío, y los docs del día.
- **Hecho:**
  - Motor en `bankrec/`: 64,28 en sample sin plugins.
  - Plugins `remittance` y `pdfclauses` en curso.
  - `scripts/download.py` y `scripts/submit.py`.
  - `docs/PLAN_DIA.md` y `docs/HERRAMIENTAS.md`.
- **Trabado en:** la descarga de TRAIN y LIVE va lenta por la red de casa (VPN).
- **Necesito:** TRAIN lite descargado, para medir en el holdout 2025-01..06 y entrenar `learned` (IBAN, alias y categorías aprendidas de 4 años).
- **Siguiente paso sugerido:** cuando esté TRAIN, `python -m bankrec.run --phase train --since 2025-01-01` y atacar el segmento con más fallos.
