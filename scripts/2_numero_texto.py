"""
PASO 2: NÚMERO DE FACTURA EN EL TEXTO
Búsqueda de números impresos (FV25-0400572, API-2022-7685409, etc.)
"""

import pandas as pd
import re
from config import BT_FILE, AR_FILE, AP_FILE
from utils import setup_logger, load_csv_safe, normalize_name, within_tolerance

logger = setup_logger("paso2", "logs/execution.log")

def extract_invoice_numbers(text):
    """Extrae números de factura del concepto bancario."""
    if not isinstance(text, str):
        return []

    text_upper = text.upper()
    found = []

    # Patrones comunes
    patterns = [
        r'FV\d{2}-\d{7}',      # FV25-0400572
        r'API-\d{7,10}',       # API-2022-7685409
        r'FA\d{2}-\d{6}',      # FA21-0100001
        r'AB\d{2}-\d{6}',      # AB22-0200001
        r'INV\d{7}',           # INV0001573
        r'TW\d{2}-\d{7}',      # TW22-9005950 (tarjetas)
        r'\d{4}[-_]?\d{4,6}',  # Genérico: 2022-0094
    ]

    for pattern in patterns:
        matches = re.findall(pattern, text_upper)
        found.extend(matches)

    return list(set(found))  # Dedup

def match_invoice_in_candidates(invoice_num, candidates_df):
    """Busca una factura que coincida con el número en los candidatos."""
    if not invoice_num or candidates_df.empty:
        return None

    # Normalizar número buscado
    search_num = invoice_num.upper().replace('-', '').replace('_', '')

    for idx, row in candidates_df.iterrows():
        # Obtener el número impreso de la factura
        if 'document_number' in row:
            doc_num = str(row['document_number']).upper()
        elif 'supplier_invoice_number' in row:
            doc_num = str(row['supplier_invoice_number']).upper()
        elif 'order_number' in row:
            doc_num = str(row['order_number']).upper()
        else:
            continue

        doc_num = doc_num.replace('-', '').replace('_', '')

        # Buscar coincidencia (flexible)
        if search_num in doc_num or doc_num in search_num:
            return idx

    return None

def run():
    """Ejecuta paso 2."""
    logger.info("="*60)
    logger.info("PASO 2: Número de factura en texto")
    logger.info("="*60)

    # Cargar datos
    logger.info("Cargando...")
    bt = load_csv_safe(BT_FILE)
    ar = load_csv_safe(AR_FILE)
    ap = load_csv_safe(AP_FILE)

    result = []
    matched = 0

    # Procesar cada movimiento
    for idx, txn in bt.iterrows():
        bank_txn_id = txn['bank_txn_id']
        amount = txn['amount']
        currency = txn['currency']
        text = txn.get('text', '') or ''
        remittance = txn.get('remittance_info', '') or ''
        entity = txn['entity']

        # Buscar números en el concepto
        numbers = extract_invoice_numbers(text + ' ' + remittance)

        if not numbers:
            continue

        # Por cada número encontrado
        for invoice_num in numbers:
            # Decidir si es AR o AP por el signo
            if amount > 0:  # Cobro → buscar en AR
                candidates = ar[(ar['entity'] == entity) & (ar['currency'] == currency)]
            else:  # Pago → buscar en AP
                candidates = ap[(ap['entity'] == entity) & (ap['currency'] == currency)]

            if candidates.empty:
                continue

            # Buscar la factura
            match_idx = match_invoice_in_candidates(invoice_num, candidates)

            if match_idx is not None:
                matched_row = candidates.iloc[match_idx]
                item_id = matched_row['item_id']
                invoice_amount = matched_row['total_amount']

                # Verificar que el importe está dentro de tolerancia
                if within_tolerance(abs(amount), abs(invoice_amount)):
                    result.append({
                        'bank_txn_id': bank_txn_id,
                        'item_id': item_id,
                        'amount': abs(amount),
                        'category': None,
                        'exception': '',
                    })
                    matched += 1
                    break  # Una factura por movimiento (por ahora)

    logger.info(f"✅ Paso 2: {matched} coincidencias por número")

    return pd.DataFrame(result)

if __name__ == '__main__':
    result = run()
    print(result.head(10))
    print(f"\nTotal líneas: {len(result)}")
