"""
CONFIG.PY — Parámetros centralizados del proyecto
Cambiar aquí si algo falla (tolerancias, regex, paths, etc.)
"""

import os
from pathlib import Path

# ===== PATHS =====
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "train"
OUTPUT_DIR = PROJECT_ROOT / "output"
LOGS_DIR = PROJECT_ROOT / "logs"

# Crear directorios si no existen
OUTPUT_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

# ===== ARCHIVOS DE ENTRADA =====
BT_FILE = DATA_DIR / "bank_transactions.csv.gz"
AR_FILE = DATA_DIR / "ar_invoices.csv.gz"
AP_FILE = DATA_DIR / "ap_invoices.csv.gz"
CN_FILE = DATA_DIR / "credit_notes.csv.gz"
GOLDEN_FILE = DATA_DIR / "golden_allocations.csv.gz"

# Directorios de detalle
REMITTANCE_DIR = DATA_DIR / "remittance_advices"
DD_BATCHES_DIR = DATA_DIR / "direct_debit_batches"
CARD_SETTLEMENTS_DIR = DATA_DIR / "card_settlements"
PDFS_DIR = DATA_DIR / "pdfs"

# ===== ARCHIVOS DE SALIDA =====
RESULT_CSV = OUTPUT_DIR / "result.csv"
RESULT_GZ = OUTPUT_DIR / "result.csv.gz"
EXECUTE_LOG = LOGS_DIR / "execution.log"

# ===== TOLERANCIAS =====
TOLERANCE_ABSOLUTE = 0.01  # €0.01
TOLERANCE_PERCENT = 0.005  # 0.5%

def tolerance(amount):
    """Calcula la tolerancia para un importe dado."""
    return max(TOLERANCE_ABSOLUTE, amount * TOLERANCE_PERCENT)

# ===== REGEX PATTERNS =====
INVOICE_PATTERNS = {
    'ar': r'(FV|FA|FS)\d{2}[-_]?\d{4,8}',  # FV25-0400572, FA21-0100001
    'ap': r'(API|FV|INV)[-_]?\d{4,8}',     # API-2022-7685409, INV0001573
    'dd': r'(VTM|REM|DD)[-_]?[A-Z0-9]{4,8}',  # VTM-ES-0004847
    'generic': r'\d{4,8}',                  # Número genérico de 4–8 dígitos
}

# ===== EXCEPCIONES =====
EXCEPTIONS = [
    '',  # Sin excepción (vacío)
    'PARTIAL',
    'OVERPAYMENT',
    'SHORT_DISCOUNT',
    'SHORT_BANK_CHARGES',
    'SHORT_WITHHOLDING',
    'SHORT_DISPUTE',
    'DUPLICATE_PAYMENT',
    'RETURNED',
    'FX_DIFFERENCE',
    'UNIDENTIFIED',
]

# ===== CATEGORÍAS (para nonitem) =====
CATEGORIES = [
    'BANK_FEES',
    'CASH_POOL',
    'PAYROLL',
    'TAX_PAYMENT',
    'LOAN_REPAYMENT',
    'LOAN_DRAWDOWN',
    'INTEREST_INCOME',
    'INTEREST_EXPENSE',
    'INSURANCE',
    'SOCIAL_SECURITY',
    'FX_DEAL',
    'INTERCOMPANY',
    'LEASING',
    'CARD_EXPENSES',
    'UNIDENTIFIED',
]

# ===== KEYWORDS PARA CLASIFICACIÓN =====
KEYWORDS = {
    'BANK_FEES': ['comis', 'gast', 'fee', 'cargo', 'cobranza', 'comisión'],
    'CASH_POOL': ['cash pool', 'nivelacion', 'pooling'],
    'PAYROLL': ['nomina', 'sueldo', 'salario', 'paga', 'payroll'],
    'TAX_PAYMENT': ['impuesto', 'mod 111', 'mod 130', 'hacienda', 'tax', 'aeat'],
    'LOAN_REPAYMENT': ['prestamo', 'loan', 'amortizacion'],
    'INTEREST_INCOME': ['interes', 'interest', 'rendimiento'],
    'LOAN_DRAWDOWN': ['desembolso', 'drawdown'],
    'INSURANCE': ['seguro', 'insurance'],
    'SOCIAL_SECURITY': ['seguridad social', 'cotizacion'],
    'INTERCOMPANY': ['intercompany', 'filial', 'grupo'],
    'LEASING': ['arrendamiento', 'leasing'],
}

# ===== PLATFORM API =====
PLATFORM_URL = "https://bankrec.talky.ai/submit"
# Token se lee de variable de entorno o .env
SUBMIT_TOKEN = os.getenv("BANKREC_TOKEN", "")

# ===== LOGGING =====
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
LOG_LEVEL = "INFO"

# ===== DEBUGGING =====
DEBUG = False  # Set True para verbose output
SAMPLE_SIZE = None  # Set a number (e.g., 1000) para testear rápido con muestra
