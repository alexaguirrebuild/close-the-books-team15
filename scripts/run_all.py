"""
RUN_ALL.PY — Ejecuta TODOS los pasos (1-6) y combina resultado
Ejecutar esto cada vez que cambien scripts: python scripts/run_all.py
"""

import sys
import pandas as pd
from pathlib import Path

# Añadir scripts a path
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from config import OUTPUT_DIR, RESULT_CSV, EXECUTE_LOG, SAMPLE_SIZE
from utils import setup_logger, combine_results, validate_output, summarize_results

# Importar los pasos
import paso1_remesas as paso1
import paso2_numero_texto as paso2
import paso3_mismo_importe as paso3
import paso4_terceros as paso4
import paso5_tolerancias as paso5
import paso6_nonitem as paso6

# Crear logger
logger = setup_logger("run_all", str(EXECUTE_LOG))

def main():
    """Ejecuta todos los pasos."""

    logger.info("\n" + "="*60)
    logger.info("INICIANDO EJECUCIÓN COMPLETA")
    logger.info("="*60 + "\n")

    print("\n🚀 EJECUTANDO TODOS LOS PASOS...\n")

    results = []

    # PASO 1
    try:
        print("⏳ Paso 1: Remesas...")
        paso1_result = paso1.run()
        results.append(paso1_result)
        print(f"  ✅ {len(paso1_result)} líneas")
    except Exception as e:
        logger.error(f"Error en paso 1: {e}")
        print(f"  ❌ Error: {e}")

    # PASO 2
    try:
        print("⏳ Paso 2: Número en texto...")
        paso2_result = paso2.run()
        results.append(paso2_result)
        print(f"  ✅ {len(paso2_result)} líneas")
    except Exception as e:
        logger.error(f"Error en paso 2: {e}")
        print(f"  ❌ Error: {e}")

    # PASO 3
    try:
        print("⏳ Paso 3: Mismo importe (FIFO)...")
        paso3_result = paso3.run()
        results.append(paso3_result)
        print(f"  ✅ {len(paso3_result)} líneas")
    except Exception as e:
        logger.error(f"Error en paso 3: {e}")
        print(f"  ❌ Error: {e}")

    # PASO 4
    try:
        print("⏳ Paso 4: Terceros pagadores...")
        paso4_result = paso4.run()
        results.append(paso4_result)
        print(f"  ✅ {len(paso4_result)} líneas")
    except Exception as e:
        logger.error(f"Error en paso 4: {e}")
        print(f"  ❌ Error: {e}")

    # PASO 5
    try:
        print("⏳ Paso 5: Tolerancias...")
        paso5_result = paso5.run()
        results.append(paso5_result)
        print(f"  ✅ {len(paso5_result)} líneas")
    except Exception as e:
        logger.error(f"Error en paso 5: {e}")
        print(f"  ❌ Error: {e}")

    # PASO 6
    try:
        print("⏳ Paso 6: Nonitem...")
        paso6_result = paso6.run()
        results.append(paso6_result)
        print(f"  ✅ {len(paso6_result)} líneas")
    except Exception as e:
        logger.error(f"Error en paso 6: {e}")
        print(f"  ❌ Error: {e}")

    # COMBINAR
    print("\n⏳ Combinando resultados...")
    final_result = combine_results(*results)

    # VALIDAR
    try:
        validate_output(final_result)
        print("  ✅ Validación OK")
    except Exception as e:
        logger.error(f"Validación fallida: {e}")
        print(f"  ❌ Validación fallida: {e}")
        return False

    # GUARDAR
    try:
        OUTPUT_DIR.mkdir(exist_ok=True)
        final_result.to_csv(RESULT_CSV, index=False)
        print(f"  ✅ Guardado en {RESULT_CSV}")
    except Exception as e:
        logger.error(f"Error al guardar: {e}")
        print(f"  ❌ Error al guardar: {e}")
        return False

    # RESUMEN
    summarize_results(final_result)

    logger.info("✅ EJECUCIÓN COMPLETADA")
    print("✅ LISTO PARA ENVIAR\n")

    return True

if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
