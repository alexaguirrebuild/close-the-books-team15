"""
EVALUATE.PY — Compara resultado contra golden localmente
Ejecutar: python scripts/evaluate.py
(Solo funciona si tienes golden_allocations.csv.gz en data/train/)
"""

import pandas as pd
from config import RESULT_CSV, GOLDEN_FILE
from utils import setup_logger, load_csv_safe, within_tolerance

logger = setup_logger("evaluate", "logs/execution.log")

def calculate_metrics(result_df, golden_df):
    """Calcula métricas contra golden."""

    # Mezclar: (bank_txn_id, item_id) = pares
    result_pairs = set(zip(result_df['bank_txn_id'], result_df['item_id']))
    golden_pairs = set(zip(golden_df['bank_txn_id'], golden_df['item_id']))

    # Conteos
    true_positives = len(result_pairs & golden_pairs)
    false_positives = len(result_pairs - golden_pairs)
    false_negatives = len(golden_pairs - result_pairs)

    # Métricas
    precision = true_positives / (true_positives + false_positives) if (true_positives + false_positives) > 0 else 0
    recall = true_positives / (true_positives + false_negatives) if (true_positives + false_negatives) > 0 else 0
    f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0

    return {
        'tp': true_positives,
        'fp': false_positives,
        'fn': false_negatives,
        'precision': precision,
        'recall': recall,
        'f1': f1,
    }

def run():
    """Ejecuta evaluación."""
    logger.info("="*60)
    logger.info("EVALUATE (LOCAL)")
    logger.info("="*60)

    print("\n📊 EVALUANDO CONTRA GOLDEN...\n")

    # Verificar archivos
    if not RESULT_CSV.exists():
        print(f"❌ result.csv no encontrado")
        print(f"   Ejecuta primero: python scripts/run_all.py")
        return False

    if not GOLDEN_FILE.exists():
        print(f"❌ golden_allocations.csv.gz no encontrado")
        print(f"   Asegúrate de descomprimir bankrec-train.tar.gz en data/train/")
        return False

    # Cargar
    try:
        result = pd.read_csv(RESULT_CSV)
        golden = load_csv_safe(GOLDEN_FILE)
    except Exception as e:
        print(f"❌ Error cargando archivos: {e}")
        return False

    # Calcular
    metrics = calculate_metrics(result, golden)

    # Mostrar
    print(f"Pares encontrados (TP+FP): {metrics['tp'] + metrics['fp']}")
    print(f"Pares esperados (TP+FN): {metrics['tp'] + metrics['fn']}")
    print()
    print(f"True Positives: {metrics['tp']}")
    print(f"False Positives: {metrics['fp']}")
    print(f"False Negatives: {metrics['fn']}")
    print()
    print(f"Precision: {metrics['precision']:.1%}")
    print(f"Recall: {metrics['recall']:.1%}")
    print(f"F1-Score: {metrics['f1']:.1%}")
    print()

    # Estimación de puntuación
    estimated_score = 100 * (0.5 * metrics['f1'] + 0.3 * (metrics['tp'] / (metrics['tp'] + metrics['fn'])))
    print(f"Puntuación estimada: {estimated_score:.0f} / 100")

    logger.info(f"F1={metrics['f1']:.3f}, Est. Score={estimated_score:.0f}")

    return True

if __name__ == '__main__':
    run()
