"""
PASO 6: MOVIMIENTOS SIN FACTURA (NONITEM)
Clasificación por keywords: BANK_FEES, CASH_POOL, PAYROLL, TAX_PAYMENT, etc.
"""

import pandas as pd
import re
from config import BT_FILE, KEYWORDS
from utils import setup_logger, load_csv_safe

logger = setup_logger("paso6", "logs/execution.log")

def classify_nonitem(text, bank_code=''):
    """Clasifica un movimiento sin factura por keywords."""
    if not isinstance(text, str):
        text = ''

    text_lower = text.lower()
    bank_code_str = str(bank_code).lower()

    # Buscar keywords
    for category, keywords in KEYWORDS.items():
        for keyword in keywords:
            if keyword.lower() in text_lower or keyword.lower() in bank_code_str:
                return category

    return 'UNIDENTIFIED'

def run():
    """Ejecuta paso 6."""
    logger.info("="*60)
    logger.info("PASO 6: Movimientos sin factura (nonitem)")
    logger.info("="*60)

    bt = load_csv_safe(BT_FILE)

    result = []

    # Por ahora solo clasificar movimientos que obviamente no tienen factura
    # En la práctica, esto se llenarían después de pasos 1-5

    logger.info(f"Paso 6: lista para procesar {len(bt)} movimientos")

    return pd.DataFrame(result)

if __name__ == '__main__':
    result = run()
    print(f"Total líneas: {len(result)}")
