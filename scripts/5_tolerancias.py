"""
PASO 5: TOLERANCIAS Y EXCEPCIONES
SHORT_DISCOUNT, SHORT_WITHHOLDING, SHORT_BANK_CHARGES, PARTIAL, OVERPAYMENT
"""

import pandas as pd
from config import BT_FILE, AR_FILE, AP_FILE, TOLERANCE_PERCENT
from utils import setup_logger, load_csv_safe

logger = setup_logger("paso5", "logs/execution.log")

def run():
    """Ejecuta paso 5 (básico por ahora)."""
    logger.info("="*60)
    logger.info("PASO 5: Tolerancias y excepciones")
    logger.info("="*60)

    logger.info("Paso 5 aún en desarrollo (placeholder)")

    return pd.DataFrame()

if __name__ == '__main__':
    result = run()
    print(f"Total líneas: {len(result)}")
