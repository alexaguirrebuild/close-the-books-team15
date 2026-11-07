"""
PASO 1: REMESAS CON AVISO Y DD BATCHES
Detección de 1:N mediante avisos de pago y remesas SEPA/tarjeta
"""

import pandas as pd
import os
from pathlib import Path
from config import BT_FILE, AR_FILE, REMITTANCE_DIR, DD_BATCHES_DIR, CARD_SETTLEMENTS_DIR
from utils import setup_logger, load_csv_safe, summarize_results

logger = setup_logger("paso1", "logs/execution.log")

def process_remittance_advices():
    """Procesa avisos de remesa (remittance_advices/*.csv)"""
    result = []

    if not REMITTANCE_DIR.exists():
        logger.warning(f"Carpeta {REMITTANCE_DIR} no existe")
        return pd.DataFrame()

    # Busca todos los archivos .csv en remittance_advices
    for file in REMITTANCE_DIR.rglob("*.csv"):
        try:
            df = pd.read_csv(file)

            # Estructura esperada: end_to_end_id, customer_name o debtor_name, amount, remittance_info
            for idx, row in df.iterrows():
                end_to_end = row.get('end_to_end_id', '')
                amount = row.get('amount', 0)
                remittance = row.get('remittance_info', '')

                # Cada línea del aviso es una factura pagada
                result.append({
                    'end_to_end_id': end_to_end,
                    'item_id': None,  # Se llena después en buscar_factura
                    'amount': amount,
                    'remittance_info': remittance,
                    'source': 'remittance_advice',
                    'file': file.name
                })
        except Exception as e:
            logger.error(f"Error leyendo {file}: {e}")

    return pd.DataFrame(result)

def process_dd_batches():
    """Procesa remesas SEPA (direct_debit_batches/*.csv)"""
    result = []

    if not DD_BATCHES_DIR.exists():
        logger.warning(f"Carpeta {DD_BATCHES_DIR} no existe")
        return pd.DataFrame()

    for file in DD_BATCHES_DIR.glob("*.csv"):
        try:
            df = load_csv_safe(file)

            # Estructura: end_to_end_id, debtor_name, amount, remittance_info
            for idx, row in df.iterrows():
                result.append({
                    'end_to_end_id': row.get('end_to_end_id', ''),
                    'debtor_name': row.get('debtor_name', ''),
                    'amount': row.get('amount', 0),
                    'remittance_info': row.get('remittance_info', ''),
                    'source': 'dd_batch',
                    'file': file.name
                })
        except Exception as e:
            logger.error(f"Error leyendo {file}: {e}")

    return pd.DataFrame(result)

def process_card_settlements():
    """Procesa liquidaciones de tarjeta (card_settlements/*.csv)"""
    result = []

    if not CARD_SETTLEMENTS_DIR.exists():
        logger.warning(f"Carpeta {CARD_SETTLEMENTS_DIR} no existe")
        return pd.DataFrame()

    for file in CARD_SETTLEMENTS_DIR.glob("*.csv"):
        try:
            df = load_csv_safe(file)

            # Estructura: order_number, operation_date, gross, fee, net
            for idx, row in df.iterrows():
                result.append({
                    'order_number': row.get('order_number', ''),
                    'operation_date': row.get('operation_date', ''),
                    'gross': row.get('gross', 0),  # Usar GROSS, no NET
                    'fee': row.get('fee', 0),
                    'source': 'card_settlement',
                    'file': file.name
                })
        except Exception as e:
            logger.error(f"Error leyendo {file}: {e}")

    return pd.DataFrame(result)

def match_remittances_to_transactions(bt, remittances):
    """Busca movimientos bancarios que correspondan a remesas."""
    result = []

    for idx, remit in remittances.iterrows():
        end_to_end = remit.get('end_to_end_id', '')

        # Busca movimiento con este end_to_end_id
        matches = bt[bt['end_to_end_id'] == end_to_end]

        if len(matches) > 0:
            for _, txn in matches.iterrows():
                # Una fila por factura en la remesa
                result.append({
                    'bank_txn_id': txn['bank_txn_id'],
                    'item_id': None,  # Llenar después
                    'amount': remit['amount'],
                    'category': None,
                    'exception': '',
                })

    return pd.DataFrame(result)

def run():
    """Ejecuta paso 1."""
    logger.info("="*60)
    logger.info("PASO 1: Remesas y DD batches")
    logger.info("="*60)

    # Cargar datos
    logger.info("Cargando transacciones bancarias...")
    bt = load_csv_safe(BT_FILE)

    logger.info("Procesando remittance_advices...")
    remittances = process_remittance_advices()

    logger.info("Procesando DD batches...")
    dd_batches = process_dd_batches()

    logger.info("Procesando card settlements...")
    cards = process_card_settlements()

    # Procesar
    logger.info("Buscando coincidencias...")
    result = match_remittances_to_transactions(bt, remittances)

    logger.info(f"✅ Paso 1: {len(result)} líneas generadas")

    return result

if __name__ == '__main__':
    result = run()
    print(result.head(10))
    print(f"\nTotal líneas: {len(result)}")
