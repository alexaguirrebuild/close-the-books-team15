"""
UTILS.PY — Funciones comunes para todos los scripts
"""

import pandas as pd
import re
import logging
from pathlib import Path
from config import (
    INVOICE_PATTERNS, EXCEPTIONS, CATEGORIES, KEYWORDS, LOG_FORMAT, LOG_LEVEL,
    TOLERANCE_ABSOLUTE, TOLERANCE_PERCENT
)

# ===== LOGGING =====
def setup_logger(name, log_file):
    """Configura logging para un módulo."""
    logger = logging.getLogger(name)
    logger.setLevel(LOG_LEVEL)

    # File handler
    fh = logging.FileHandler(log_file, mode='a')
    fh.setLevel(LOG_LEVEL)

    # Formatter
    formatter = logging.Formatter(LOG_FORMAT)
    fh.setFormatter(formatter)

    logger.addHandler(fh)
    return logger

# ===== NORMALIZACIÓN =====
def normalize_name(name):
    """Normaliza nombres de clientes/proveedores."""
    if not isinstance(name, str):
        return ""

    # Mayúsculas
    name = name.upper()

    # Quitar acentos (simple)
    replacements = {'Á': 'A', 'É': 'E', 'Í': 'I', 'Ó': 'O', 'Ú': 'U', 'Ü': 'U'}
    for old, new in replacements.items():
        name = name.replace(old, new)

    # Quitar sufijos comunes
    suffixes = ['S.A.', 'S.L.', 'S.COOP.', 'LTD', 'LLC', 'GMBH', 'BV', 'NV', 'PTY LTD', 'INC', 'CORP']
    for suffix in suffixes:
        name = re.sub(r'\b' + suffix.replace('.', r'\.') + r'\b', '', name)

    # Trim y espacios múltiples
    name = ' '.join(name.split())

    return name.strip()

# ===== BÚSQUEDA DE FACTURAS =====
def find_invoice_numbers(text):
    """Extrae números de factura del texto."""
    if not isinstance(text, str):
        return []

    text = text.upper()
    found = []

    for pattern_name, pattern in INVOICE_PATTERNS.items():
        matches = re.findall(pattern, text, re.IGNORECASE)
        found.extend(matches)

    return list(set(found))  # Dedup

def match_invoice_number(invoice_num, candidates_df):
    """Busca una factura que coincida con el número."""
    if not invoice_num:
        return None

    # Limpiar número
    invoice_num = invoice_num.upper().replace('-', '').replace('_', '')

    for idx, row in candidates_df.iterrows():
        doc_num = str(row['document_number'] if 'document_number' in row else row.get('supplier_invoice_number', ''))
        doc_num = doc_num.upper().replace('-', '').replace('_', '')

        if invoice_num in doc_num or doc_num in invoice_num:
            return row['item_id']

    return None

# ===== CÁLCULO DE TOLERANCIA =====
def within_tolerance(sent, golden, tolerance=None):
    """Verifica si una cantidad está dentro de tolerancia."""
    if tolerance is None:
        tolerance = max(TOLERANCE_ABSOLUTE, abs(golden) * TOLERANCE_PERCENT)

    return abs(sent - golden) <= tolerance

# ===== VALIDACIÓN DE OUTPUT =====
def validate_output(df):
    """Valida que el output tenga el formato correcto."""
    required_cols = ['bank_txn_id', 'item_id', 'amount', 'category', 'exception']

    # Check columns
    if not all(col in df.columns for col in required_cols):
        raise ValueError(f"Faltan columnas. Requeridas: {required_cols}")

    # Check que cada fila tiene item_id O category
    invalid = df[(df['item_id'].isna()) & (df['category'].isna())]
    if len(invalid) > 0:
        raise ValueError(f"{len(invalid)} filas sin item_id ni category")

    # Check que amount nunca es NaN
    if df['amount'].isna().any():
        raise ValueError("Hay filas con amount=NaN")

    # Check excepciones válidas
    invalid_exc = df[~df['exception'].isin(EXCEPTIONS + [None, ''])]
    if len(invalid_exc) > 0:
        print(f"⚠️  {len(invalid_exc)} excepciones inválidas:")
        print(invalid_exc['exception'].unique())

    # Check categorías válidas
    invalid_cat = df[df['category'].notna() & ~df['category'].isin(CATEGORIES)]
    if len(invalid_cat) > 0:
        print(f"⚠️  {len(invalid_cat)} categorías inválidas:")
        print(invalid_cat['category'].unique())

    return True

# ===== LECTURA DE ARCHIVOS =====
def load_csv_safe(file_path, nrows=None):
    """Carga CSV.gz de forma segura."""
    try:
        if str(file_path).endswith('.gz'):
            return pd.read_csv(file_path, compression='gzip', nrows=nrows)
        else:
            return pd.read_csv(file_path, nrows=nrows)
    except FileNotFoundError:
        raise FileNotFoundError(f"Archivo no encontrado: {file_path}")

# ===== COMBINACIÓN DE RESULTADOS =====
def combine_results(*dfs):
    """Combina múltiples DataFrames de resultados."""
    combined = pd.concat(dfs, ignore_index=True, sort=False)

    # Dedup por (bank_txn_id, item_id) — si hay, guarda el primero
    combined = combined.drop_duplicates(subset=['bank_txn_id', 'item_id'], keep='first')

    return combined

# ===== REPORTE =====
def summarize_results(df):
    """Imprime resumen de los resultados."""
    print("\n" + "="*60)
    print("📊 RESUMEN DE RESULTADOS")
    print("="*60)
    print(f"Total movimientos: {df['bank_txn_id'].nunique()}")
    print(f"Total líneas: {len(df)}")
    print(f"\nPor tipo:")
    print(f"  • Con item_id: {df['item_id'].notna().sum()}")
    print(f"  • Sin item_id (nonitem): {df['item_id'].isna().sum()}")
    print(f"\nExcepciones:")
    exc_counts = df[df['exception'].notna()]['exception'].value_counts()
    for exc, count in exc_counts.items():
        print(f"  • {exc}: {count}")
    print(f"\nCategorías (nonitem):")
    cat_counts = df[df['category'].notna()]['category'].value_counts()
    for cat, count in cat_counts.head(10).items():
        print(f"  • {cat}: {count}")
    print("="*60 + "\n")
