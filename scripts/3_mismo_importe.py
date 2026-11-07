"""
PASO 3: MISMO IMPORTE + CLIENTE (ar_same_amount_fifo)
Cuando hay varias facturas del mismo cliente con importe idéntico → FIFO
"""

import pandas as pd
from config import BT_FILE, AR_FILE, AP_FILE
from utils import setup_logger, load_csv_safe, normalize_name

logger = setup_logger("paso3", "logs/execution.log")

def run():
    """Ejecuta paso 3."""
    logger.info("="*60)
    logger.info("PASO 3: Mismo importe (FIFO)")
    logger.info("="*60)

    # Cargar datos
    bt = load_csv_safe(BT_FILE)
    ar = load_csv_safe(AR_FILE)
    ap = load_csv_safe(AP_FILE)

    result = []
    matched = 0

    for idx, txn in bt.iterrows():
        bank_txn_id = txn['bank_txn_id']
        amount = abs(txn['amount'])
        currency = txn['currency']
        counterparty = normalize_name(txn.get('counterparty_name', ''))
        entity = txn['entity']

        # Decidir si buscar en AR o AP
        if txn['amount'] > 0:  # Cobro
            candidates = ar[
                (ar['entity'] == entity) &
                (ar['currency'] == currency) &
                (ar['total_amount'] == amount)
            ]

            if len(candidates) > 1:
                # FIFO: seleccionar la más antigua
                candidates = candidates.sort_values('issue_date')
                first = candidates.iloc[0]

                result.append({
                    'bank_txn_id': bank_txn_id,
                    'item_id': first['item_id'],
                    'amount': amount,
                    'category': None,
                    'exception': '',
                })
                matched += 1
        else:  # Pago
            candidates = ap[
                (ap['entity'] == entity) &
                (ap['currency'] == currency) &
                (ap['total_amount'] == amount)
            ]

            if len(candidates) > 1:
                # FIFO
                candidates = candidates.sort_values('issue_date')
                first = candidates.iloc[0]

                result.append({
                    'bank_txn_id': bank_txn_id,
                    'item_id': first['item_id'],
                    'amount': amount,
                    'category': None,
                    'exception': '',
                })
                matched += 1

    logger.info(f"✅ Paso 3: {matched} casaciones FIFO")

    return pd.DataFrame(result)

if __name__ == '__main__':
    result = run()
    print(result.head(10))
    print(f"\nTotal líneas: {len(result)}")
