"""
PASO 4: TERCEROS PAGADORES
Detecta si el pagador es una matriz, banco o agencia
"""

import pandas as pd
from config import BT_FILE, AR_FILE, AP_FILE
from utils import setup_logger, load_csv_safe, normalize_name

logger = setup_logger("paso4", "logs/execution.log")

def run():
    """Ejecuta paso 4 (básico por ahora)."""
    logger.info("="*60)
    logger.info("PASO 4: Terceros pagadores")
    logger.info("="*60)

    logger.info("Paso 4 aún en desarrollo (placeholder)")

    return pd.DataFrame()

if __name__ == '__main__':
    result = run()
    print(f"Total líneas: {len(result)}")
