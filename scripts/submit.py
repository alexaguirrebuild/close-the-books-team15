"""
SUBMIT.PY — Valida y sube el resultado a la plataforma
Ejecutar: python scripts/submit.py
"""

import pandas as pd
import gzip
import shutil
import os
from pathlib import Path
from config import RESULT_CSV, RESULT_GZ, SUBMIT_TOKEN, PLATFORM_URL
from utils import setup_logger, validate_output

logger = setup_logger("submit", "logs/execution.log")

def compress_result(csv_file, gz_file):
    """Comprime el CSV a .gz"""
    with open(csv_file, 'rb') as f_in:
        with gzip.open(gz_file, 'wb') as f_out:
            shutil.copyfileobj(f_in, f_out)

    return gz_file

def check_file_size(file_path):
    """Verifica que el archivo sea <60 MB"""
    size_mb = os.path.getsize(file_path) / (1024 * 1024)
    if size_mb > 60:
        raise ValueError(f"Archivo muy grande: {size_mb:.1f} MB (máx 60)")
    return size_mb

def submit_to_platform(gz_file, token):
    """Sube el archivo a la plataforma (stub por ahora)."""
    print(f"""

🚀 LISTO PARA ENVIAR:
  Archivo: {gz_file}
  Tamaño: {os.path.getsize(gz_file) / (1024*1024):.1f} MB

📋 Pasos para enviar:

  1. Ve a: {PLATFORM_URL}
  2. Introduce tu token: {token if token else '(SET BANKREC_TOKEN env var)'}
  3. Sube el archivo: {gz_file}
  4. Espera resultado (~2 min)

💾 Alternativa: cURL directo (si tienes token):
  curl -X POST {PLATFORM_URL} \\
    -H "Authorization: Bearer {token if token else 'YOUR_TOKEN'}" \\
    -F "file=@{gz_file}"
    """)

def run():
    """Ejecuta validación y preparación para envío."""
    logger.info("="*60)
    logger.info("SUBMIT")
    logger.info("="*60)

    print("\n📋 VALIDANDO Y PREPARANDO PARA ENVÍO...\n")

    # 1. Verificar que result.csv existe
    if not RESULT_CSV.exists():
        print(f"❌ Archivo no encontrado: {RESULT_CSV}")
        print(f"   Primero ejecuta: python scripts/run_all.py")
        return False

    # 2. Cargar y validar
    print("⏳ Cargando y validando result.csv...")
    try:
        df = pd.read_csv(RESULT_CSV)
        validate_output(df)
        print(f"  ✅ {len(df)} líneas válidas")
    except Exception as e:
        print(f"  ❌ Validación fallida: {e}")
        logger.error(f"Validación fallida: {e}")
        return False

    # 3. Comprimir
    print("⏳ Comprimiendo...")
    try:
        gz_file = compress_result(RESULT_CSV, RESULT_GZ)
        size_mb = check_file_size(gz_file)
        print(f"  ✅ Comprimido a {size_mb:.1f} MB")
    except Exception as e:
        print(f"  ❌ Error: {e}")
        logger.error(f"Error de compresión: {e}")
        return False

    # 4. Mostrar instrucciones
    submit_to_platform(gz_file, SUBMIT_TOKEN)

    logger.info("✅ LISTO PARA ENVIAR")

    return True

if __name__ == '__main__':
    success = run()
    if success:
        print("\n✅ Sigue los pasos arriba para enviar.\n")
    else:
        print("\n❌ No se puede enviar. Revisa los errores.\n")
