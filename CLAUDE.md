# Bank Reconciliation Competition - CLAUDE.md

## Contexto
Competencia de conciliación bancaria organizada por Talky AI y UCppM (7 Nov 2026, 09:00-21:00).
**Objetivo: GANAR maximizando el score.**
**Comunicación en español.**

## Fórmula de Score
```
Score = 100 × (0.5 · Allocation_F1 + 0.3 · Transaction_Accuracy + 0.2 · Exception_macro_F1)
```
- Tolerancia de matching: `max(0.01, 0.5% del golden amount)`
- Exception F1 es POR bank_txn_id (nivel transacción, no par)

## Score Actual (mejor confirmado): 38.5/100
```
Allocation F1:       0.4363 (P=0.5788 R=0.3501)
Transaction Acc:     0.4601 (619,905/1,347,301)
Exception macro-F1:  0.1418
SCORE: 38.5 / 100
```

### Último Run (todos los cambios combinados): 38.1/100 — PEOR
```
Allocation F1:       0.4262 (P=0.5339 R=0.3547)  ← Precisión cayó mucho
Transaction Acc:     0.4658 (627,535/1,347,301)    ← Subió un poco
Exception macro-F1:  0.1399
SCORE: 38.1 / 100
```
**Diagnóstico**: +24K TPs pero +289K FPs. Los cambios que AYUDAN:
- step_ap_multi_invoice_text: ap_payment_multi R subió 0.496→0.534 (+22.9K TPs) ✅
Los cambios que DAÑAN:
- max_items=200 en _greedy_subset_sum: Name 1:N AR 952K pares pero muchos FPs ❌ (revertir a 20 o buscar punto intermedio)
- SHORT_DISCOUNT rango [0.96, 0.995]: 16,665 predicciones vs 5,166 antes, solo +243 TPs ❌ (revertir a [0.97, 0.995])
- step_dispute_match: Solo 6 TPs de SHORT_DISPUTE, casi nulo ❌ (necesita mejora o desactivar)

## Pipeline Actual (en main() de solve.py)
1. DD Batches → 1.1M pares / 18K txns
2. Payment Runs → 435K pares / 169K txns
3. Card Settlements → 77K pares / 5.5K txns
4. Cheque Deposits → 33K pares / 15.5K txns
5. Remittance Advices → 183K pares / 18.8K txns
5b. Remittance-Info Docs → 1.3K pares
6. Invoice Regex → 273K pares / 74K txns
6b. Suffix Doc Match → 8.6K pares
6c. **AP Multi-Invoice Text** → 27K pares / 9.8K txns (NUEVO)
7. FX Match → 4K pares
7b. FX Text Match → 4.9K pares
8. Discount Match → 3.2K pares
8b. Partial Match → **DESHABILITADO** (280K FP vs 18K TP)
9. Name 1:N AR → 953K pares / 222K txns (max_items=200)
9a2. **AR Dispute Match** → 16K pares / 2.1K txns (NUEVO)
9b. Name 1:N AP → 18K pares / 13K txns
9c. Holding Company → 21K pares (solo Methods 1+2, simple)
10. Direct 1:1 Match → 336K pares
11. Nonitem → 579K (505K UNIDENTIFIED)
POST: Exception Labels

## Excepciones (F1 actual)
| Excepción | F1 | TP/Golden | Notas |
|---|---|---|---|
| DUPLICATE_PAYMENT | 0.000 | 0/4,927 | Sin detector |
| FX_DIFFERENCE | 0.577 | 6,439/15,837 | Mejor excepción |
| OVERPAYMENT | 0.005→DIS | 95/2,838 | **DESHABILITADO** (35K FP) |
| PARTIAL | 0.144 | 10,192/79,491 | |
| RETURNED | 0.472 | 8,989/28,416 | |
| SHORT_BANK_CHARGES | 0.000 | 0/1,130 | Sin detector |
| SHORT_DISCOUNT | 0.126 | 3,332/44,692 | Rango [0.96, 0.995] |
| SHORT_DISPUTE | 0.000 | 0/8,273 | Nuevo step debería mejorar |
| SHORT_WITHHOLDING | 0.001→DIS | 16/10,210 | **DESHABILITADO** (24K FP) |
| UNIDENTIFIED | 0.095 | 25,274/34,653 | |

## Segmentos con Más Potencial (recall bajo, muchas golden pairs)
| Segmento | Golden | Recall | Oportunidad |
|---|---|---|---|
| ar_short_dispute | 688K | 0.050 | step_dispute_match debería mejorar esto |
| ar_ref_noise | 677K | 0.362 | Refs ruidosas dificultan regex |
| ap_payment_multi | 598K | 0.496 | step_ap_multi_invoice_text ataca esto |
| ar_1toN_remittance | 389K | 0.140 | max_items=200 ayuda |
| ap_fx | 334K | 0.030 | FX para AP, muy bajo recall |
| ar_1toN_text | 260K | 0.170 | |
| ar_1toN_credit_note | 229K | 0.040 | Credit notes en 1:N, muy bajo |
| ap_paid_by_holding | 83K | 0.123 | Holding company cross-entity |

## Datos
- Directorio: `data/train/`
- bank_transactions.csv.gz (1.49M txns)
- ar_invoices.csv.gz (3.96M)
- ap_invoices.csv.gz (1.53M)
- golden_allocations.csv.gz (5.26M golden pairs)
- credit_notes.csv.gz (150K)
- Subdirectorios: payment_runs/ (1056 CSVs), card_settlements/, direct_debit_batches/, cheque_deposits/, remittance_advices/, bank_statements/, pdfs/
- 3 fases: TRAIN (1.49M con golden), LIVE (203K sin golden), FINAL (220K, un solo intento)

## Ejecutar
```bash
python -I solve.py --evaluate
```
Output en `output/result.csv`. Evaluación automática con `--evaluate`.

## Cambios Recientes Clave
1. **max_items=200** en _greedy_subset_sum (era 20) → Name 1:N AR subió de 680K a 953K pares
2. **step_ap_multi_invoice_text**: Busca invoice numbers en texto de BTs AP (patrón "FRAS INV001 INV002")
3. **step_dispute_match**: BT ≈ 90-100.5% de TODAS las facturas del cliente → SHORT_DISPUTE
4. **OVERPAYMENT y SHORT_WITHHOLDING deshabilitados**: Demasiados FPs, destruían Exception F1
5. **Holding company simplificado**: Solo Methods 1+2 (name+amount 1:1), el 1:N añadía FPs
6. **SHORT_DISCOUNT ampliado**: Rango [0.96, 0.995] (antes [0.97, 0.995])

## Reglas Clave del POLICIES.md (documentación oficial del hackathon)

### SHORT_DISCOUNT (regla exacta)
- Payment terms con descuento: D2N30 (2% en 10 días), D1N60 (1% en 15 días), D3N45 (3% en 8 días), D2N30US (2/10 net 30)
- Condición: booking_date ≤ invoice_date + discount_days + **3 días de gracia**
- Cash = invoice_total - discount_pct × invoice_total (redondeado al céntimo)
- Factura se cierra completamente, amount = full open amount

### SHORT_BANK_CHARGES
- Transferencia cross-border fuera de SEPA, intermediario dedujo comisión (SHA/BEN)
- Shortfall ≤ **60 EUR equivalente** Y ≤ **2%** del item
- Factura se cierra completamente

### SHORT_WITHHOLDING
- Retención fiscal: solo clientes de **México, Brasil y Marruecos** (MX, BR, MA)
- Tasas: **1.25%, 1.5% o 10%** del total de la factura
- Factura se cierra completamente

### DUPLICATE_PAYMENT
- El item ya fue liquidado y se paga de nuevo
- La segunda línea lleva la excepción

### Customer Groups (parent pays for subsidiaries)
- customers.csv tiene `group_id` y `parent_customer_id`
- Una empresa madre puede pagar por sus filiales

### Payment Factory (ES00 paga AP de subsidiarias)
- ES00 (Valtermia Industrial) paga facturas AP de ES01, ES02 y PT01 desde sus propias cuentas
- Los items son de la subsidiaria

### FIFO para importes iguales
- Si hay varias facturas abiertas con el mismo importe y sin referencia útil → pagar la más antigua (por fecha factura)

### Confirming / Factor
- "CONFIRMING … ORD. <customer>" → el banco confirmador paga por el cliente, asignar a las facturas del cliente

### FX_DIFFERENCE
- BT en divisa diferente a la factura
- Spread bancario: 0.1-0.6% G10 (USD, GBP, CHF, CAD, AUD, PLN), 0.4-1.5% emergentes (MXN, BRL, CNY, ZAR, MAD)
- Tolerancia FX ≤ 3%
- Reemplaza excepciones vacías, SHORT_DISCOUNT, SHORT_WITHHOLDING, SHORT_BANK_CHARGES
- NO reemplaza PARTIAL ni SHORT_DISPUTE
- Credit notes NO llevan FX_DIFFERENCE

### Categorías (nonitem)
- BANK_FEES, INTEREST_INCOME/EXPENSE, PAYROLL, SOCIAL_SECURITY, TAX_PAYMENT/REFUND
- LOAN_DRAWDOWN/REPAYMENT, LEASING, FX_DEAL, CASH_POOL, INTERCOMPANY, INTERNAL_TRANSFER
- CUSTOMER_ADVANCE (pago de proforma), CUSTOMER_REFUND, SUPPLIER_REFUND
- CARD_EXPENSES, INSURANCE, BANK_REVERSAL, UNIDENTIFIED

### Datos masters clave disponibles
- `Paella_Hackathon/bankrec-docs-and-masters/masters/` tiene: customers.csv (con group_id, parent_customer_id), suppliers.csv, payment_terms.csv, entities.csv, fx_rates.csv, bank_accounts.csv, categories.csv

## Qué Optimizar Siguiente
1. **Precisión de Name 1:N AR** (953K pares): max_items=200 añadió muchos FPs, revertir a 20 o punto intermedio
2. **SHORT_DISCOUNT con reglas exactas**: Usar payment_terms.csv para calcular descuento exacto en vez de ratio
3. **SHORT_BANK_CHARGES** (1,130 golden, F1=0): Detector cross-border ≤60 EUR y ≤2%
4. **SHORT_WITHHOLDING con filtro país**: Solo MX/BR/MA, tasas 1.25%/1.5%/10%
5. **DUPLICATE_PAYMENT** (4,927 golden, F1=0): Detectar items pagados dos veces
6. **ar_1toN_credit_note** (229K golden, R=0.040): Muy bajo recall
7. **ap_fx** (334K golden, R=0.030): FX para AP muy bajo
8. **Mejorar categorías nonitem**: Usar keywords en texto para categorizar mejor
9. **Customer groups**: Usar parent_customer_id para matching cross-customer
