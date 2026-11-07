#!/usr/bin/env python3
"""
SOLVE.PY v2 - Pipeline de conciliacion bancaria (vectorizado).
Ejecutar: python solve.py [data_dir] [--evaluate]
"""

import sys, time, re, logging, argparse, glob as globmod
from pathlib import Path
import pandas as pd
import numpy as np

TOL_ABS = 0.01
TOL_PCT = 0.005

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stderr), logging.FileHandler('solve.log', 'w', 'utf-8')])
log = logging.getLogger('solve')


def norm_name(s):
    if not isinstance(s, str): return ''
    s = s.upper()
    for old, new in [('\xc1','A'),('\xc9','E'),('\xcd','I'),('\xd3','O'),('\xda','U'),
                     ('\xdc','U'),('\xd1','N'),('\xc7','C')]:
        s = s.replace(old, new)
    for sfx in [' S.A.',' S.L.',' S.L.U.',' S.COOP.',' LTD',' LLC',' GMBH',' PTY LTD',
                ' INC',' CORP',' BV',' NV',' EURL',' SAS',' SARL',' AG',' & CO. KG',
                ' CO. KG',' E.I.R.L.',' LTDA',' PTY']:
        s = s.replace(sfx, '')
    return re.sub(r'[^A-Z0-9 ]', '', s).strip()


def within_tol(a, b):
    tol = max(TOL_ABS, TOL_PCT * abs(b))
    return abs(a - b) <= tol


# =====================================================================
# LOAD
# =====================================================================
def load_all(data_dir):
    d = Path(data_dir)
    t0 = time.time()

    bt = pd.read_csv(d/'bank_transactions.csv.gz', dtype=str)
    bt['amount'] = pd.to_numeric(bt['amount'], errors='coerce').fillna(0)
    bt['orig_amount'] = pd.to_numeric(bt.get('orig_amount', pd.Series(dtype=float)), errors='coerce')
    for c in ['text','counterparty_name','counterparty_account','end_to_end_id','remittance_info',
              'bank_code','bank_subcode','ref1','ref2','orig_currency']:
        if c in bt.columns: bt[c] = bt[c].fillna('')
    bt['booking_date'] = pd.to_datetime(bt['booking_date'], errors='coerce')
    log.info(f"BT: {len(bt):,}")

    ar = pd.read_csv(d/'ar_invoices.csv.gz', dtype=str)
    ar['total_amount'] = pd.to_numeric(ar['total_amount'], errors='coerce').fillna(0)
    ar['net_amount'] = pd.to_numeric(ar['net_amount'], errors='coerce').fillna(0)
    ar['tax_amount'] = pd.to_numeric(ar['tax_amount'], errors='coerce').fillna(0)
    ar['document_number'] = ar['document_number'].fillna('')
    ar['customer_name'] = ar['customer_name'].fillna('')
    ar['customer_id'] = ar['customer_id'].fillna('')
    ar['issue_date'] = pd.to_datetime(ar['issue_date'], errors='coerce')
    ar['due_date'] = pd.to_datetime(ar['due_date'], errors='coerce')
    log.info(f"AR: {len(ar):,}")

    ap = pd.read_csv(d/'ap_invoices.csv.gz', dtype=str)
    ap['total_amount'] = pd.to_numeric(ap['total_amount'], errors='coerce').fillna(0)
    ap['net_amount'] = pd.to_numeric(ap['net_amount'], errors='coerce').fillna(0)
    ap['tax_amount'] = pd.to_numeric(ap['tax_amount'], errors='coerce').fillna(0)
    ap['supplier_invoice_number'] = ap['supplier_invoice_number'].fillna('')
    ap['supplier_name'] = ap['supplier_name'].fillna('')
    ap['issue_date'] = pd.to_datetime(ap['issue_date'], errors='coerce')
    ap['due_date'] = pd.to_datetime(ap['due_date'], errors='coerce')
    log.info(f"AP: {len(ap):,}")

    cn = pd.read_csv(d/'credit_notes.csv.gz', dtype=str)
    cn['total_amount'] = pd.to_numeric(cn['total_amount'], errors='coerce').fillna(0)
    cn['document_number'] = cn['document_number'].fillna('')
    cn['related_document'] = cn['related_document'].fillna('')
    cn['side'] = cn['side'].fillna('')
    log.info(f"CN: {len(cn):,}")

    log.info(f"Carga base: {time.time()-t0:.1f}s")
    return bt, ar, ap, cn


def load_detail(data_dir, subdir, expected_cols=None):
    d = Path(data_dir) / subdir
    if not d.exists(): return pd.DataFrame()
    frames = []
    for f in d.glob('*.csv'):
        if f.name.startswith('._'): continue
        try: frames.append(pd.read_csv(f, dtype=str))
        except: pass
    if not frames: return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    log.info(f"  {subdir}: {len(df):,}")
    return df


def load_remittances(data_dir):
    d = Path(data_dir) / 'remittance_advices'
    if not d.exists(): return pd.DataFrame()

    all_rows = []

    # CSV
    for entity_dir in sorted(d.iterdir()):
        if not entity_dir.is_dir() or entity_dir.name.startswith('._'): continue
        entity = entity_dir.name
        for f in entity_dir.glob('*.csv'):
            if f.name.startswith('._'): continue
            try:
                lines = f.read_text(encoding='utf-8', errors='replace').strip().split('\n')
                payment_ref = ''
                if len(lines) > 0 and 'payment_ref' in lines[0].lower():
                    parts = lines[0].split(',')
                    if len(parts) >= 2: payment_ref = parts[1].strip()

                data_start = -1
                for i, line in enumerate(lines):
                    if line.lower().startswith('document') or 'doc_date' in line.lower():
                        data_start = i + 1
                        break

                if data_start > 0:
                    for line in lines[data_start:]:
                        parts = line.split(',')
                        if len(parts) >= 3:
                            doc_num = parts[0].strip()
                            if not doc_num: continue
                            paid_str = parts[5].strip() if len(parts) > 5 and parts[5].strip() else parts[2].strip()
                            try:
                                amt = float(paid_str)
                                all_rows.append({'payment_ref': payment_ref, 'document_number': doc_num,
                                               'amount': amt, 'entity': entity})
                            except ValueError: pass
            except: pass

    # TXT
    line_pat = re.compile(r'^\s+([A-Z0-9][-A-Z0-9/.]{3,30})\s+\d{2}/\d{2}/\d{2,4}\s+([\d,]+\.\d{2})', re.MULTILINE)
    ref_pat = re.compile(r'Subject:.*?(\S+)\s*$', re.MULTILINE)

    for entity_dir in sorted(d.iterdir()):
        if not entity_dir.is_dir() or entity_dir.name.startswith('._'): continue
        entity = entity_dir.name
        for f in entity_dir.glob('*.txt'):
            if f.name.startswith('._'): continue
            try:
                text = f.read_text(encoding='utf-8', errors='replace')
                ref_m = ref_pat.search(text)
                payment_ref = ref_m.group(1) if ref_m else ''

                for m in line_pat.finditer(text):
                    doc_num = m.group(1).strip()
                    amt = float(m.group(2).replace(',', ''))
                    all_rows.append({'payment_ref': payment_ref, 'document_number': doc_num,
                                   'amount': amt, 'entity': entity})
            except: pass

    log.info(f"  Remittances: {len(all_rows):,}")
    return pd.DataFrame(all_rows) if all_rows else pd.DataFrame()


# =====================================================================
# STEPS
# =====================================================================

def step_dd(bt, ar, dd, matched_set):
    """DD batches: extract batch_id from BT text, join with DD data."""
    if dd.empty: return pd.DataFrame(), matched_set

    bt_unm = bt[~bt['bank_txn_id'].isin(matched_set)].copy()

    # Extract batch_id (11-digit number) from text
    bt_unm['batch_id'] = bt_unm['text'].str.extract(r'(\d{11})', expand=False)
    bt_with_batch = bt_unm[bt_unm['batch_id'].notna()]

    if len(bt_with_batch) == 0:
        log.info("  DD: no batch_ids found in BT text")
        return pd.DataFrame(), matched_set

    # DD has: batch_id, end_to_end_id, remittance_info (=doc number), amount, account_id
    dd['amount_f'] = pd.to_numeric(dd['amount'], errors='coerce').fillna(0)
    dd['remittance_info'] = dd['remittance_info'].fillna('')
    dd['account_id'] = dd['account_id'].fillna('')

    # Join BT -> DD by batch_id
    merged = bt_with_batch[['bank_txn_id','batch_id','entity','account_id']].merge(
        dd[['batch_id','remittance_info','amount_f','account_id','re_presentation']],
        left_on=['batch_id','account_id'], right_on=['batch_id','account_id'],
        how='inner'
    )

    # Filter out re-presentations (returned DD)
    merged = merged[merged['re_presentation'] != 'Y']

    # remittance_info -> document_number in AR
    merged['doc_upper'] = merged['remittance_info'].str.upper().str.strip()

    ar_idx = ar[['item_id','entity','document_number','total_amount','currency']].copy()
    ar_idx['doc_upper'] = ar_idx['document_number'].str.upper().str.strip()

    result = merged.merge(
        ar_idx[['item_id','entity','doc_upper','total_amount']],
        on=['entity','doc_upper'], how='inner'
    )

    out = result[['bank_txn_id','item_id']].copy()
    out['amount'] = result['total_amount']
    out['category'] = ''
    out['exception'] = ''

    new_matched = set(out['bank_txn_id'].unique())
    log.info(f"  DD matched: {len(out):,} pairs / {len(new_matched):,} txns")
    return out, matched_set | new_matched


def step_payment_runs(bt, ap, pr, matched_set):
    """Payment runs: join by end_to_end_id, then doc_number -> AP."""
    if pr.empty: return pd.DataFrame(), matched_set

    bt_unm = bt[~bt['bank_txn_id'].isin(matched_set)].copy()
    bt_with_e2e = bt_unm[bt_unm['end_to_end_id'] != '']

    pr['document_number'] = pr['document_number'].fillna('')
    pr['document_amount'] = pd.to_numeric(pr['document_amount'], errors='coerce').fillna(0)
    pr['end_to_end_id'] = pr['end_to_end_id'].fillna('')

    # Join BT -> PR by end_to_end_id
    merged = bt_with_e2e[['bank_txn_id','end_to_end_id','entity']].merge(
        pr[['end_to_end_id','document_number','document_amount']],
        on='end_to_end_id', how='inner'
    )

    # doc_number + amount -> AP item_id (filter by amount to avoid duplicates)
    merged['doc_upper'] = merged['document_number'].str.upper().str.strip()
    merged['doc_amt_round'] = merged['document_amount'].round(2)

    ap_idx = ap[['item_id','entity','supplier_invoice_number','total_amount','issue_date']].copy()
    ap_idx['doc_upper'] = ap_idx['supplier_invoice_number'].str.upper().str.strip()
    ap_idx['amt_round'] = ap_idx['total_amount'].round(2)

    # Primary: match on entity + doc + amount
    result = merged.merge(
        ap_idx[['item_id','entity','doc_upper','amt_round','total_amount']],
        left_on=['entity','doc_upper','doc_amt_round'],
        right_on=['entity','doc_upper','amt_round'],
        how='inner'
    )

    # Fallback for unmatched: doc only, pick closest amount
    matched_keys = set(result['bank_txn_id'] + '|' + result['doc_upper'])
    merged['mkey'] = merged['bank_txn_id'] + '|' + merged['doc_upper']
    missed = merged[~merged['mkey'].isin(matched_keys)]
    if len(missed) > 0:
        fb = missed.merge(
            ap_idx[['item_id','entity','doc_upper','total_amount']],
            on=['entity','doc_upper'], how='inner'
        )
        if len(fb) > 0:
            fb['amt_diff'] = (fb['document_amount'] - fb['total_amount']).abs()
            fb = fb.sort_values('amt_diff').drop_duplicates(
                subset=['bank_txn_id','doc_upper'], keep='first')
            result = pd.concat([result, fb[result.columns.intersection(fb.columns)]], ignore_index=True)

    result = result.drop_duplicates(subset=['bank_txn_id','item_id'], keep='first')

    out = result[['bank_txn_id','item_id']].copy()
    out['amount'] = result['total_amount']
    out['category'] = ''
    out['exception'] = ''

    new_matched = set(out['bank_txn_id'].unique())
    log.info(f"  Payment runs: {len(out):,} pairs / {len(new_matched):,} txns")
    return out, matched_set | new_matched


def step_cards(bt, ar, cs, matched_set):
    """Card settlements: extract merchant_id+date from BT text, match to CS."""
    if cs.empty: return pd.DataFrame(), matched_set

    bt_unm = bt[~bt['bank_txn_id'].isin(matched_set)].copy()

    # Extract merchant_id and date from text
    # Pattern: "CARD SETTLEMENT MID {mid} {date} {N} TRX" or "ON {mid} {date_short}"
    pat = re.compile(r'(?:MID\s+|ON\s+)(\d{6,12})\s+(\d{4}-\d{2}-\d{2}|\d{4})')

    def extract_card_info(text):
        m = pat.search(str(text))
        if m:
            mid = m.group(1)
            date_str = m.group(2)
            if len(date_str) == 4:
                # Short date like "2906" = DDMM
                return mid, date_str
            return mid, date_str
        return None, None

    bt_unm[['card_mid','card_date']] = pd.DataFrame(
        bt_unm['text'].apply(lambda x: pd.Series(extract_card_info(x))),
        index=bt_unm.index
    )

    bt_cards = bt_unm[bt_unm['card_mid'].notna()].copy()
    if len(bt_cards) == 0:
        log.info("  Cards: no merchant IDs found in BT text")
        return pd.DataFrame(), matched_set

    # CS has: merchant_id, operation_date, order_number, gross, net
    cs['merchant_id'] = cs['merchant_id'].fillna('')
    cs['operation_date'] = cs['operation_date'].fillna('')
    cs['order_number'] = cs['order_number'].fillna('')
    cs['gross_f'] = pd.to_numeric(cs['gross'], errors='coerce').fillna(0)
    cs['net_f'] = pd.to_numeric(cs['net'], errors='coerce').fillna(0)
    cs['account_id'] = cs['account_id'].fillna('')

    # Group CS by (merchant_id, operation_date, account_id) -> sum net = BT amount
    cs_groups = cs.groupby(['merchant_id','operation_date','account_id']).agg(
        group_net=('net_f', 'sum')
    ).reset_index()
    cs_groups['group_net_round'] = cs_groups['group_net'].round(2)

    # Match BT -> CS group by merchant_id + amount (date may be formatted differently)
    bt_cards['amount_round'] = bt_cards['amount'].round(2)

    matched_bt = bt_cards.merge(
        cs_groups[['merchant_id','operation_date','account_id','group_net_round']],
        left_on=['card_mid','account_id','amount_round'],
        right_on=['merchant_id','account_id','group_net_round'],
        how='inner'
    )

    if len(matched_bt) == 0:
        log.info("  Cards: 0 matches by merchant+amount")
        return pd.DataFrame(), matched_set

    # Expand: each matched BT -> all CS rows in that group
    expanded = matched_bt[['bank_txn_id','merchant_id','operation_date','account_id']].merge(
        cs[['merchant_id','operation_date','account_id','order_number','gross_f']],
        on=['merchant_id','operation_date','account_id'], how='inner'
    )

    # order_number -> AR item_id
    expanded['doc_upper'] = expanded['order_number'].str.upper().str.strip()
    ar_idx = ar[['item_id','entity','document_number','total_amount']].copy()
    ar_idx['doc_upper'] = ar_idx['document_number'].str.upper().str.strip()

    # Entity from account_id
    expanded['entity'] = expanded['account_id'].str.split('-').str[0]

    result = expanded.merge(
        ar_idx[['item_id','entity','doc_upper','total_amount']],
        on=['entity','doc_upper'], how='inner'
    )

    result = result.drop_duplicates(subset=['bank_txn_id','item_id'], keep='first')

    out = result[['bank_txn_id','item_id']].copy()
    out['amount'] = result['total_amount']
    out['category'] = ''
    out['exception'] = ''

    new_matched = set(out['bank_txn_id'].unique())
    log.info(f"  Cards: {len(out):,} pairs / {len(new_matched):,} txns")
    return out, matched_set | new_matched


def step_cheques(bt, ar, ch, matched_set):
    """Cheque deposits: stub_reference -> AR document_number."""
    if ch.empty: return pd.DataFrame(), matched_set

    bt_unm = bt[~bt['bank_txn_id'].isin(matched_set)].copy()

    ch['amount_f'] = pd.to_numeric(ch['amount'], errors='coerce').fillna(0)
    ch['deposit_total_f'] = pd.to_numeric(ch['deposit_total'], errors='coerce').fillna(0)
    ch['stub_reference'] = ch['stub_reference'].fillna('')
    ch['account_id'] = ch['account_id'].fillna('')
    ch['deposit_id'] = ch['deposit_id'].fillna('')

    # Extract deposit_id from BT text or match by amount
    # BT text might contain cheque/deposit reference
    # Group cheques by deposit_id -> total
    dep_totals = ch.groupby(['deposit_id','account_id']).agg(
        dep_sum=('amount_f', 'sum')
    ).reset_index()
    dep_totals['dep_sum_round'] = dep_totals['dep_sum'].round(2)

    bt_pos = bt_unm[bt_unm['amount'] > 0].copy()
    bt_pos['amount_round'] = bt_pos['amount'].round(2)

    matched_bt = bt_pos.merge(
        dep_totals[['deposit_id','account_id','dep_sum_round']],
        left_on=['account_id','amount_round'], right_on=['account_id','dep_sum_round'],
        how='inner'
    )

    if len(matched_bt) == 0:
        log.info("  Cheques: 0 matches")
        return pd.DataFrame(), matched_set

    # Expand to individual cheques
    expanded = matched_bt[['bank_txn_id','deposit_id','account_id']].merge(
        ch[['deposit_id','account_id','stub_reference','amount_f']],
        on=['deposit_id','account_id'], how='inner'
    )

    # stub_reference -> AR doc
    expanded['doc_upper'] = expanded['stub_reference'].str.upper().str.strip()
    expanded['entity'] = expanded['account_id'].str.split('-').str[0]

    ar_idx = ar[['item_id','entity','document_number','total_amount']].copy()
    ar_idx['doc_upper'] = ar_idx['document_number'].str.upper().str.strip()

    result = expanded.merge(
        ar_idx[['item_id','entity','doc_upper','total_amount']],
        on=['entity','doc_upper'], how='inner'
    )
    result = result.drop_duplicates(subset=['bank_txn_id','item_id'], keep='first')

    out = result[['bank_txn_id','item_id']].copy()
    out['amount'] = result['total_amount']
    out['category'] = ''
    out['exception'] = ''

    new_matched = set(out['bank_txn_id'].unique())
    log.info(f"  Cheques: {len(out):,} pairs / {len(new_matched):,} txns")
    return out, matched_set | new_matched


def step_remittances(bt, ar, rems, matched_set):
    """Remittance advices: match by payment_ref in BT fields, then by amount."""
    if rems.empty: return pd.DataFrame(), matched_set

    bt_unm = bt[~bt['bank_txn_id'].isin(matched_set)].copy()

    rems['doc_upper'] = rems['document_number'].str.upper().str.strip()

    ar_idx = ar[['item_id','entity','document_number','total_amount']].copy()
    ar_idx['doc_upper'] = ar_idx['document_number'].str.upper().str.strip()

    rem_items = rems.merge(
        ar_idx[['item_id','entity','doc_upper','total_amount']],
        on=['entity','doc_upper'], how='inner'
    )

    ref_totals = rem_items.groupby(['payment_ref','entity']).agg(
        rem_total=('total_amount', 'sum')
    ).reset_index()
    ref_totals['rem_round'] = ref_totals['rem_total'].round(2)

    bt_pos = bt_unm[bt_unm['amount'] > 0].copy()
    bt_pos['amount_round'] = bt_pos['amount'].round(2)

    # Method 1: Match by payment_ref found in BT remittance_info
    ref_matches = []
    valid_refs = ref_totals[ref_totals['payment_ref'].str.len() >= 5]['payment_ref'].unique()
    if len(valid_refs) > 0:
        ref_df = pd.DataFrame({'payment_ref': valid_refs})
        ref_df['ref_upper'] = ref_df['payment_ref'].str.upper().str.strip()
        bt_pos['rem_upper'] = bt_pos['remittance_info'].str.upper().str.strip()
        ref_joined = bt_pos[bt_pos['rem_upper'].str.len() >= 5][['bank_txn_id','entity','rem_upper']].merge(
            ref_df, left_on='rem_upper', right_on='ref_upper', how='inner'
        )
        if len(ref_joined) > 0:
            ref_matches.append(ref_joined[['bank_txn_id','payment_ref','entity']])

    # Method 2: Match by entity + amount
    amount_matched = bt_pos.merge(
        ref_totals[['payment_ref','entity','rem_round']],
        left_on=['entity','amount_round'], right_on=['entity','rem_round'],
        how='inner'
    )
    if len(amount_matched) > 0:
        ref_matches.append(amount_matched[['bank_txn_id','payment_ref','entity']])

    if not ref_matches:
        log.info("  Remittances: 0 matches")
        return pd.DataFrame(), matched_set

    matched_df = pd.concat(ref_matches, ignore_index=True).drop_duplicates(
        subset=['bank_txn_id','payment_ref'])

    expanded = matched_df.merge(
        rem_items[['payment_ref','entity','item_id','total_amount']],
        on=['payment_ref','entity'], how='inner'
    )
    expanded = expanded.drop_duplicates(subset=['bank_txn_id','item_id'], keep='first')

    out = expanded[['bank_txn_id','item_id']].copy()
    out['amount'] = expanded['total_amount']
    out['category'] = ''
    out['exception'] = ''

    new_matched = set(out['bank_txn_id'].unique())
    log.info(f"  Remittances: {len(out):,} pairs / {len(new_matched):,} txns")
    return out, matched_set | new_matched


def step_remittance_info_docs(bt, ar, ap, matched_set):
    """Match BT remittance_info directly to AR/AP document numbers."""
    bt_unm = bt[~bt['bank_txn_id'].isin(matched_set)].copy()
    bt_has_rem = bt_unm[bt_unm['remittance_info'].str.len() > 3].copy()
    if len(bt_has_rem) == 0: return pd.DataFrame(), matched_set

    ar_idx = ar[['item_id','entity','document_number','total_amount','issue_date']].copy()
    ar_idx['doc_upper'] = ar_idx['document_number'].str.upper().str.strip()

    ap_idx = ap[['item_id','entity','supplier_invoice_number','total_amount','issue_date']].copy()
    ap_idx['doc_upper'] = ap_idx['supplier_invoice_number'].str.upper().str.strip()

    bt_has_rem['rem_upper'] = bt_has_rem['remittance_info'].str.upper().str.strip()

    all_out = []

    # AR (positive BT)
    bt_ar = bt_has_rem[bt_has_rem['amount'] > 0].copy()
    if len(bt_ar) > 0:
        joined = bt_ar[['bank_txn_id','entity','rem_upper','amount']].merge(
            ar_idx[['item_id','entity','doc_upper','total_amount','issue_date']],
            left_on=['entity','rem_upper'], right_on=['entity','doc_upper'],
            how='inner'
        )
        if len(joined) > 0:
            joined = joined.sort_values('issue_date').drop_duplicates(
                subset='bank_txn_id', keep='first')
            out = joined[['bank_txn_id','item_id']].copy()
            out['amount'] = joined['total_amount']
            out['category'] = ''
            out['exception'] = ''
            all_out.append(out)

    # AP (negative BT)
    bt_ap = bt_has_rem[bt_has_rem['amount'] < 0].copy()
    if len(bt_ap) > 0:
        joined = bt_ap[['bank_txn_id','entity','rem_upper','amount']].merge(
            ap_idx[['item_id','entity','doc_upper','total_amount','issue_date']],
            left_on=['entity','rem_upper'], right_on=['entity','doc_upper'],
            how='inner'
        )
        if len(joined) > 0:
            joined = joined.sort_values('issue_date').drop_duplicates(
                subset='bank_txn_id', keep='first')
            out = joined[['bank_txn_id','item_id']].copy()
            out['amount'] = joined['total_amount']
            out['category'] = ''
            out['exception'] = ''
            all_out.append(out)

    if not all_out:
        log.info("  Remittance-info docs: 0 matches")
        return pd.DataFrame(), matched_set

    out = pd.concat(all_out, ignore_index=True)
    new_matched = set(out['bank_txn_id'].unique())
    log.info(f"  Remittance-info docs: {len(out):,} pairs / {len(new_matched):,} txns")
    return out, matched_set | new_matched


def step_invoice_regex(bt, ar, ap, matched_set):
    """Extract doc numbers from BT text fields and look them up in AR/AP (vectorized)."""
    bt_unm = bt[~bt['bank_txn_id'].isin(matched_set)].copy()
    if len(bt_unm) == 0: return pd.DataFrame(), matched_set

    search = (bt_unm['remittance_info'] + ' ' + bt_unm['text'] + ' ' + bt_unm['ref1'] + ' ' + bt_unm['ref2'])

    combined_pat = r'(INV\d{2}-\d{5,8}|FA-\d{4}-\d{4,8}|RECIBO\s+\d{5,10}|\d{4}/\d{4,8}|WEB\d{2}-\d{5,10}|NC-?\d{4}-?\d{4,8}|REC-\d{4}-\d{4,8}|PAY-\d{4}-\d{6,10}|BO-\d{4}-\d{4,8})'

    extracted = search.str.extractall(combined_pat)
    if len(extracted) == 0:
        log.info("  Regex: 0 extractions")
        return pd.DataFrame(), matched_set

    extracted = extracted.reset_index()
    extracted.columns = ['bt_idx', 'match_num', 'doc']
    extracted['doc_upper'] = extracted['doc'].str.upper().str.strip()
    extracted['bank_txn_id'] = bt_unm.loc[extracted['bt_idx'].values, 'bank_txn_id'].values
    extracted['entity'] = bt_unm.loc[extracted['bt_idx'].values, 'entity'].values
    extracted['amount'] = bt_unm.loc[extracted['bt_idx'].values, 'amount'].values

    all_out = []

    # AR (positive amounts)
    ext_pos = extracted[extracted['amount'] > 0]
    if len(ext_pos) > 0:
        ar_idx = ar[['item_id','entity','document_number','total_amount']].copy()
        ar_idx['doc_upper'] = ar_idx['document_number'].str.upper().str.strip()
        joined = ext_pos[['bank_txn_id','entity','doc_upper']].merge(
            ar_idx[['item_id','entity','doc_upper','total_amount']],
            on=['entity','doc_upper'], how='inner'
        )
        if len(joined) > 0:
            all_out.append(joined[['bank_txn_id','item_id','total_amount']])

    # AP (negative amounts)
    ext_neg = extracted[extracted['amount'] < 0]
    if len(ext_neg) > 0:
        ap_idx = ap[['item_id','entity','supplier_invoice_number','total_amount']].copy()
        ap_idx['doc_upper'] = ap_idx['supplier_invoice_number'].str.upper().str.strip()
        joined = ext_neg[['bank_txn_id','entity','doc_upper']].merge(
            ap_idx[['item_id','entity','doc_upper','total_amount']],
            on=['entity','doc_upper'], how='inner'
        )
        if len(joined) > 0:
            all_out.append(joined[['bank_txn_id','item_id','total_amount']])

    if not all_out:
        log.info("  Regex: 0 matches")
        return pd.DataFrame(), matched_set

    out = pd.concat(all_out, ignore_index=True)
    out = out.drop_duplicates(subset=['bank_txn_id','item_id'], keep='first')
    out['amount'] = out['total_amount']
    out['category'] = ''
    out['exception'] = ''
    out = out[['bank_txn_id','item_id','amount','category','exception']]

    new_matched = set(out['bank_txn_id'].unique())
    log.info(f"  Regex: {len(out):,} pairs / {len(new_matched):,} txns")
    return out, matched_set | new_matched


def step_suffix_doc_match(bt, ar, matched_set):
    """Match partial/suffix doc numbers in BT text to AR docs (vectorized)."""
    bt_unm = bt[~bt['bank_txn_id'].isin(matched_set)].copy()
    bt_pos = bt_unm[bt_unm['amount'] > 0].copy()
    if len(bt_pos) == 0: return pd.DataFrame(), matched_set

    # Build AR suffix index: extract last 6 digits from each doc number
    ar_m = ar[['item_id','entity','document_number','total_amount','customer_id']].copy()
    ar_m['doc_upper'] = ar_m['document_number'].str.upper().str.strip()
    ar_m['suffix6'] = ar_m['doc_upper'].str.extract(r'(\d{6,7})$', expand=False)
    ar_suf = ar_m[ar_m['suffix6'].notna()].copy()

    # Extract 6-7 digit numbers from BT text+remittance_info
    search = bt_pos['remittance_info'].fillna('') + ' ' + bt_pos['text'].fillna('')
    extracted = search.str.extractall(r'(\d{6,7})')
    if len(extracted) == 0:
        log.info("  Suffix doc: 0 extractions")
        return pd.DataFrame(), matched_set

    extracted = extracted.reset_index()
    extracted.columns = ['bt_idx', 'match_num', 'suffix6']
    extracted['bank_txn_id'] = bt_pos.loc[extracted['bt_idx'].values, 'bank_txn_id'].values
    extracted['entity'] = bt_pos.loc[extracted['bt_idx'].values, 'entity'].values
    extracted['bt_amount'] = bt_pos.loc[extracted['bt_idx'].values, 'amount'].values

    # Join extracted suffixes with AR suffix index by entity + suffix
    joined = extracted[['bank_txn_id','entity','suffix6','bt_amount']].merge(
        ar_suf[['item_id','entity','suffix6','total_amount','customer_id']],
        on=['entity','suffix6'], how='inner'
    )

    if len(joined) == 0:
        log.info("  Suffix doc: 0 suffix matches")
        return pd.DataFrame(), matched_set

    # For each BT, check if all matched items belong to same customer and sum to BT amount
    joined = joined.drop_duplicates(subset=['bank_txn_id','item_id'])
    grps = joined.groupby('bank_txn_id')

    valid_txns = []
    for txn_id, grp in grps:
        # All items should be same customer
        if grp['customer_id'].nunique() > 1: continue
        total = round(grp['total_amount'].sum(), 2)
        target = round(grp['bt_amount'].iloc[0], 2)
        tol = max(TOL_ABS, TOL_PCT * abs(target))
        if abs(total - target) <= tol:
            valid_txns.append(txn_id)

    if not valid_txns:
        log.info("  Suffix doc: 0 verified matches")
        return pd.DataFrame(), matched_set

    out = joined[joined['bank_txn_id'].isin(set(valid_txns))][['bank_txn_id','item_id','total_amount']].copy()
    out['amount'] = out['total_amount']
    out['category'] = ''
    out['exception'] = ''
    out = out[['bank_txn_id','item_id','amount','category','exception']]
    out = out.drop_duplicates(subset=['bank_txn_id','item_id'])

    new_matched = set(out['bank_txn_id'].unique())
    log.info(f"  Suffix doc: {len(out):,} pairs / {len(new_matched):,} txns")
    return out, matched_set | new_matched


def _extract_cp_from_text(text):
    """Extract company name from BT text when counterparty_name is empty."""
    if not isinstance(text, str): return ''
    # Patterns: "DIRECT CREDIT {name} ...", "BONIFICO DA {name} CAUSALE",
    # "VIR SEPA RECU /DE {name} /MOTIF", "WIRE IN /ORG/{name} /RFB",
    # "ACH CREDIT {name} CCD", "TRANSFERENCIAS {name} |"
    patterns = [
        r'DIRECT CREDIT\s+(.+?)(?:\s+(?:PMT|VENDOR|PAYMENT|REMITTANCE|REM/))',
        r'BONIFICO DA\s+(.+?)\s+CAUSALE',
        r'/DE\s+(.+?)\s+/MOTIF',
        r'/ORG/(.+?)\s+/RFB',
        r'ACH CREDIT\s+(.+?)\s+CCD',
        r'TRANSFERENCIAS\s+(.+?)\s*\|',
        r'SEPA-UEBERWEISUNG\s+(?:AN\s+)?(?:EREF\+\S+\s+)?(?:SVWZ\+)?(.+?)(?:\s+RECHNUNG|\s+ZAHLUNG|\s*$)',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            name = m.group(1).strip()
            if len(name) >= 4:
                return name
    return ''


def _greedy_subset_sum(invoices, target, tol, max_items=20):
    """Greedy subset-sum: pick invoices by descending amount that sum to target."""
    inv_sorted = sorted(invoices, key=lambda x: -x[1])
    selected = []
    remaining = target
    for item_id, amt in inv_sorted:
        if len(selected) >= max_items:
            break
        if round(amt, 2) <= round(remaining + TOL_ABS, 2):
            selected.append((item_id, amt))
            remaining -= amt
            if abs(remaining) <= tol:
                break
    if abs(remaining) <= tol and selected:
        return selected
    return None


def step_name_1toN(bt, ar, cn, matched_set):
    """1:N matching by counterparty name with credit note netting (AR side)."""
    bt_unm = bt[~bt['bank_txn_id'].isin(matched_set)].copy()
    bt_pos = bt_unm[bt_unm['amount'] > 0].copy()
    if len(bt_pos) == 0: return pd.DataFrame(), matched_set

    ar_m = ar[['item_id','entity','currency','total_amount','customer_name','customer_id','issue_date']].copy()

    cust_names = ar_m.drop_duplicates('customer_id')[['customer_id','customer_name']].copy()
    cust_names['name_norm'] = cust_names['customer_name'].apply(norm_name)
    name_to_cid = cust_names.set_index('name_norm')['customer_id'].to_dict()

    bt_pos['cp_raw'] = bt_pos['counterparty_name'].fillna('')
    bt_pos['cp_from_text'] = bt_pos['text'].apply(_extract_cp_from_text)
    bt_pos['cp_use'] = bt_pos.apply(
        lambda r: r['cp_raw'] if r['cp_raw'].strip() else r['cp_from_text'], axis=1)
    bt_pos['cp_norm'] = bt_pos['cp_use'].apply(norm_name)
    bt_pos = bt_pos[bt_pos['cp_norm'] != ''].copy()

    bt_pos['customer_id'] = bt_pos['cp_norm'].map(name_to_cid)
    bt_matched = bt_pos[bt_pos['customer_id'].notna()].copy()

    if len(bt_matched) == 0:
        log.info("  Name 1:N: 0 name matches")
        return pd.DataFrame(), matched_set

    ar_sorted = ar_m.sort_values('issue_date')
    ar_by_cust = {}
    for (ent, cur, cid), grp in ar_sorted.groupby(['entity','currency','customer_id']):
        ar_by_cust[(ent, cur, cid)] = list(zip(grp['item_id'], grp['total_amount']))

    cn_ar = cn[cn['side'] == 'AR'].copy()
    cn_ar['total_amount'] = pd.to_numeric(cn_ar['total_amount'], errors='coerce').fillna(0)
    cn_ar['party_id'] = cn_ar['party_id'].fillna('')
    cn_by_cust = {}
    for (ent, cur, pid), grp in cn_ar.groupby(['entity','currency','party_id']):
        cn_by_cust[(ent, cur, pid)] = list(zip(grp['item_id'], grp['total_amount']))

    all_results = []

    for (entity, currency, cust_id), grp in bt_matched.groupby(['entity','currency','customer_id']):
        invoices = ar_by_cust.get((entity, currency, cust_id), [])
        if not invoices: continue

        credit_notes = cn_by_cust.get((entity, currency, cust_id), [])
        cn_total = sum(amt for _, amt in credit_notes)

        for _, bt_row in grp.iterrows():
            target = round(bt_row['amount'], 2)
            txn_id = bt_row['bank_txn_id']
            tol = max(TOL_ABS, TOL_PCT * abs(target))

            # Pass 1: strict tolerance, no credit notes
            result = _greedy_subset_sum(invoices, target, tol)
            if result:
                for item_id, amt in result:
                    all_results.append({'bank_txn_id': txn_id, 'item_id': item_id,
                                       'amount': amt, 'category': '', 'exception': ''})
                continue

            # Pass 2: with credit notes
            if credit_notes and cn_total > 0:
                target_cn = target + cn_total
                tol_cn = max(TOL_ABS, TOL_PCT * abs(target_cn))
                result = _greedy_subset_sum(invoices, target_cn, tol_cn)
                if result:
                    for item_id, amt in result:
                        all_results.append({'bank_txn_id': txn_id, 'item_id': item_id,
                                           'amount': amt, 'category': '', 'exception': ''})
                    for cn_id, cn_amt in credit_notes:
                        all_results.append({'bank_txn_id': txn_id, 'item_id': cn_id,
                                           'amount': cn_amt, 'category': '', 'exception': ''})

    if not all_results:
        log.info("  Name 1:N AR: 0 matches")
        return pd.DataFrame(), matched_set

    out = pd.DataFrame(all_results)
    out = out.drop_duplicates(subset=['bank_txn_id','item_id'], keep='first')

    new_matched = set(out['bank_txn_id'].unique())
    log.info(f"  Name 1:N AR: {len(out):,} pairs / {len(new_matched):,} txns")
    return out, matched_set | new_matched


def step_dispute_match(bt, ar, cn, matched_set):
    """Match unmatched AR BTs where BT ≈ 90-100% of ALL customer invoices (dispute deductions)."""
    bt_unm = bt[~bt['bank_txn_id'].isin(matched_set)].copy()
    bt_pos = bt_unm[bt_unm['amount'] > 0].copy()
    if len(bt_pos) == 0:
        return pd.DataFrame(), matched_set

    ar_m = ar[['item_id','entity','currency','total_amount','customer_name','customer_id']].copy()
    cust_names = ar_m.drop_duplicates('customer_id')[['customer_id','customer_name']].copy()
    cust_names['name_norm'] = cust_names['customer_name'].apply(norm_name)
    name_to_cid = cust_names.set_index('name_norm')['customer_id'].to_dict()

    bt_pos['cp_raw'] = bt_pos['counterparty_name'].fillna('')
    bt_pos['cp_from_text'] = bt_pos['text'].apply(_extract_cp_from_text)
    bt_pos['cp_use'] = bt_pos.apply(
        lambda r: r['cp_raw'] if r['cp_raw'].strip() else r['cp_from_text'], axis=1)
    bt_pos['cp_norm'] = bt_pos['cp_use'].apply(norm_name)
    bt_pos = bt_pos[bt_pos['cp_norm'] != ''].copy()
    bt_pos['customer_id'] = bt_pos['cp_norm'].map(name_to_cid)
    bt_matched = bt_pos[bt_pos['customer_id'].notna()].copy()
    if len(bt_matched) == 0:
        log.info("  Dispute: 0 name matches")
        return pd.DataFrame(), matched_set

    ar_by_cust = {}
    for (ent, cur, cid), grp in ar_m.groupby(['entity','currency','customer_id']):
        items = list(zip(grp['item_id'], grp['total_amount']))
        total = sum(abs(a) for _, a in items)
        ar_by_cust[(ent, cur, cid)] = (items, total)

    cn_ar = cn[cn['side'] == 'AR'].copy()
    cn_ar['total_amount'] = pd.to_numeric(cn_ar['total_amount'], errors='coerce').fillna(0)
    cn_by_cust = {}
    for (ent, cur, pid), grp in cn_ar.groupby(['entity','currency','party_id']):
        cn_by_cust[(ent, cur, pid)] = list(zip(grp['item_id'], grp['total_amount']))

    all_results = []
    for _, bt_row in bt_matched.iterrows():
        key = (bt_row['entity'], bt_row['currency'], bt_row['customer_id'])
        if key not in ar_by_cust:
            continue
        items, inv_total = ar_by_cust[key]
        if inv_total <= 0:
            continue
        bt_abs = abs(bt_row['amount'])
        ratio = bt_abs / inv_total
        if 0.90 <= ratio <= 1.005:
            txn_id = bt_row['bank_txn_id']
            exc = 'SHORT_DISPUTE' if ratio < 0.995 else ''
            for item_id, amt in items:
                all_results.append({'bank_txn_id': txn_id, 'item_id': item_id,
                                   'amount': amt, 'category': '', 'exception': exc})
            cns = cn_by_cust.get(key, [])
            if cns:
                cn_total = sum(abs(a) for _, a in cns)
                ratio_cn = bt_abs / (inv_total + cn_total)
                if 0.90 <= ratio_cn <= 1.005:
                    for cn_id, cn_amt in cns:
                        all_results.append({'bank_txn_id': txn_id, 'item_id': cn_id,
                                           'amount': cn_amt, 'category': '', 'exception': 'SHORT_DISPUTE'})

    if not all_results:
        log.info("  Dispute: 0 matches")
        return pd.DataFrame(), matched_set

    out = pd.DataFrame(all_results)
    out = out.drop_duplicates(subset=['bank_txn_id','item_id'], keep='first')
    new_matched = set(out['bank_txn_id'].unique())
    log.info(f"  Dispute: {len(out):,} pairs / {len(new_matched):,} txns")
    return out, matched_set | new_matched


def step_name_1toN_ap(bt, ap, cn, matched_set):
    """1:N matching by counterparty name for AP (negative BT amounts)."""
    bt_unm = bt[~bt['bank_txn_id'].isin(matched_set)].copy()
    bt_neg = bt_unm[bt_unm['amount'] < 0].copy()
    if len(bt_neg) == 0: return pd.DataFrame(), matched_set

    ap_m = ap[['item_id','entity','currency','total_amount','supplier_name','supplier_id','issue_date']].copy()

    supp_names = ap_m.drop_duplicates('supplier_id')[['supplier_id','supplier_name']].copy()
    supp_names['name_norm'] = supp_names['supplier_name'].apply(norm_name)
    name_to_sid = supp_names.set_index('name_norm')['supplier_id'].to_dict()

    bt_neg['cp_norm'] = bt_neg['counterparty_name'].apply(norm_name)
    bt_neg = bt_neg[bt_neg['cp_norm'] != ''].copy()

    bt_neg['supplier_id'] = bt_neg['cp_norm'].map(name_to_sid)
    bt_matched = bt_neg[bt_neg['supplier_id'].notna()].copy()

    if len(bt_matched) == 0:
        log.info("  Name 1:N AP: 0 name matches")
        return pd.DataFrame(), matched_set

    ap_sorted = ap_m.sort_values('issue_date')
    ap_by_supp = {}
    for (ent, cur, sid), grp in ap_sorted.groupby(['entity','currency','supplier_id']):
        ap_by_supp[(ent, cur, sid)] = list(zip(grp['item_id'], grp['total_amount']))

    # AP credit notes
    cn_ap = cn[cn['side'] == 'AP'].copy()
    cn_ap['total_amount'] = pd.to_numeric(cn_ap['total_amount'], errors='coerce').fillna(0)
    cn_ap['party_id'] = cn_ap['party_id'].fillna('')
    cn_by_supp = {}
    for (ent, cur, pid), grp in cn_ap.groupby(['entity','currency','party_id']):
        cn_by_supp[(ent, cur, pid)] = list(zip(grp['item_id'], grp['total_amount']))

    all_results = []

    for (entity, currency, supp_id), grp in bt_matched.groupby(['entity','currency','supplier_id']):
        invoices = ap_by_supp.get((entity, currency, supp_id), [])
        if not invoices: continue

        credit_notes = cn_by_supp.get((entity, currency, supp_id), [])
        cn_total = sum(amt for _, amt in credit_notes)

        for _, bt_row in grp.iterrows():
            target = round(abs(bt_row['amount']), 2)
            txn_id = bt_row['bank_txn_id']
            tol = max(TOL_ABS, TOL_PCT * abs(target))

            result = _greedy_subset_sum(invoices, target, tol)
            if result:
                for item_id, amt in result:
                    all_results.append({'bank_txn_id': txn_id, 'item_id': item_id,
                                       'amount': amt, 'category': '', 'exception': ''})
                continue

            # With credit notes
            if credit_notes and cn_total > 0:
                target_cn = target + cn_total
                tol_cn = max(TOL_ABS, TOL_PCT * abs(target_cn))
                result = _greedy_subset_sum(invoices, target_cn, tol_cn)
                if result:
                    for item_id, amt in result:
                        all_results.append({'bank_txn_id': txn_id, 'item_id': item_id,
                                           'amount': amt, 'category': '', 'exception': ''})
                    for cn_id, cn_amt in credit_notes:
                        all_results.append({'bank_txn_id': txn_id, 'item_id': cn_id,
                                           'amount': cn_amt, 'category': '', 'exception': ''})

    if not all_results:
        log.info("  Name 1:N AP: 0 matches")
        return pd.DataFrame(), matched_set

    out = pd.DataFrame(all_results)
    out = out.drop_duplicates(subset=['bank_txn_id','item_id'], keep='first')

    new_matched = set(out['bank_txn_id'].unique())
    log.info(f"  Name 1:N AP: {len(out):,} pairs / {len(new_matched):,} txns")
    return out, matched_set | new_matched


def _fast_name_score(cp_arr, name_arr):
    """Fast name similarity using numpy string ops."""
    n = len(cp_arr)
    scores = np.zeros(n, dtype=np.float32)
    for i in range(n):
        c = cp_arr[i]
        nm = name_arr[i]
        if not c or not nm:
            continue
        w1 = set(c.split())
        w2 = set(nm.split())
        if w1 and w2:
            scores[i] = len(w1 & w2) / len(w1 | w2)
    return scores


def _direct_match_side(bt_side, inv_m, cp_col='cp_norm', name_col='name_norm'):
    """Vectorized 1:1 matching for one side (AR or AP)."""
    # Join on entity+currency+amount_round
    joined = bt_side[['bank_txn_id','entity','currency','amount_round',cp_col]].merge(
        inv_m[['item_id','entity','currency','amount_round',name_col,'total_amount','issue_date']],
        on=['entity','currency','amount_round'], how='inner'
    )
    if len(joined) == 0:
        return pd.DataFrame()

    # Compute name score in batches for memory efficiency
    BATCH = 500_000
    all_scores = []
    cp_vals = joined[cp_col].values
    nm_vals = joined[name_col].values
    for start in range(0, len(joined), BATCH):
        end = min(start + BATCH, len(joined))
        all_scores.append(_fast_name_score(cp_vals[start:end], nm_vals[start:end]))
    joined['nscore'] = np.concatenate(all_scores)

    # Count candidates per txn
    cands = joined.groupby('bank_txn_id').size().reset_index(name='n')
    joined = joined.merge(cands, on='bank_txn_id')

    # Accept: good name match OR single candidate
    good = joined[(joined['nscore'] > 0.2) | (joined['n'] == 1)]
    if len(good) == 0:
        return pd.DataFrame()

    best = good.sort_values(['bank_txn_id','nscore','issue_date'],
                             ascending=[True, False, True]) \
                .drop_duplicates(subset='bank_txn_id', keep='first')

    out = best[['bank_txn_id','item_id']].copy()
    out['amount'] = best['total_amount']
    out['category'] = ''
    out['exception'] = ''
    return out


def step_direct_1to1(bt, ar, ap, matched_set):
    """Direct 1:1 matching: exact amount + entity + currency + name similarity."""
    bt_unm = bt[~bt['bank_txn_id'].isin(matched_set)].copy()
    if len(bt_unm) == 0: return pd.DataFrame(), matched_set

    all_out = []

    # --- AR (positive amounts) ---
    bt_pos = bt_unm[bt_unm['amount'] > 0].copy()
    if len(bt_pos) > 0:
        bt_pos['amount_round'] = bt_pos['amount'].round(2)
        bt_pos['cp_norm'] = bt_pos['counterparty_name'].apply(norm_name)

        ar_m = ar[['item_id','entity','currency','total_amount','customer_name','issue_date']].copy()
        ar_m['amount_round'] = ar_m['total_amount'].round(2)
        ar_m['name_norm'] = ar_m['customer_name'].apply(norm_name)

        out_ar = _direct_match_side(bt_pos, ar_m)
        if len(out_ar) > 0:
            all_out.append(out_ar)

    # --- AP (negative amounts) ---
    bt_neg = bt_unm[bt_unm['amount'] < 0].copy()
    if len(bt_neg) > 0:
        bt_neg['amount_round'] = bt_neg['amount'].abs().round(2)
        bt_neg['cp_norm'] = bt_neg['counterparty_name'].apply(norm_name)

        ap_m = ap[['item_id','entity','currency','total_amount','supplier_name','issue_date']].copy()
        ap_m['amount_round'] = ap_m['total_amount'].round(2)
        ap_m['name_norm'] = ap_m['supplier_name'].apply(norm_name)

        out_ap = _direct_match_side(bt_neg, ap_m)
        if len(out_ap) > 0:
            all_out.append(out_ap)

    if not all_out:
        log.info("  Direct 1:1: 0 matches")
        return pd.DataFrame(), matched_set

    out = pd.concat(all_out, ignore_index=True)
    new_matched = set(out['bank_txn_id'].unique())
    log.info(f"  Direct 1:1: {len(out):,} pairs / {len(new_matched):,} txns")
    return out, matched_set | new_matched


def step_fx_match(bt, ar, ap, matched_set):
    """FX matching: BT has orig_amount in different currency that matches invoice."""
    bt_unm = bt[~bt['bank_txn_id'].isin(matched_set)].copy()
    if len(bt_unm) == 0: return pd.DataFrame(), matched_set

    # BT with orig_amount set = FX transaction
    bt_fx = bt_unm[bt_unm['orig_amount'].notna() & (bt_unm['orig_currency'] != '')].copy()
    if len(bt_fx) == 0:
        log.info("  FX: 0 BT with orig_amount")
        return pd.DataFrame(), matched_set

    all_out = []

    # AR: orig_amount matches AR total, orig_currency matches AR currency
    bt_fx_pos = bt_fx[bt_fx['amount'] > 0].copy()
    if len(bt_fx_pos) > 0:
        bt_fx_pos['orig_round'] = bt_fx_pos['orig_amount'].round(2)
        bt_fx_pos['cp_norm'] = bt_fx_pos['counterparty_name'].apply(norm_name)

        ar_m = ar[['item_id','entity','currency','total_amount','customer_name','issue_date']].copy()
        ar_m['amount_round'] = ar_m['total_amount'].round(2)
        ar_m['name_norm'] = ar_m['customer_name'].apply(norm_name)

        joined = bt_fx_pos[['bank_txn_id','entity','orig_currency','orig_round','cp_norm']].merge(
            ar_m[['item_id','entity','currency','amount_round','name_norm','total_amount','issue_date']],
            left_on=['entity','orig_currency','orig_round'],
            right_on=['entity','currency','amount_round'],
            how='inner'
        )

        if len(joined) > 0:
            joined['nscore'] = _fast_name_score(joined['cp_norm'].values, joined['name_norm'].values)
            cands = joined.groupby('bank_txn_id').size().reset_index(name='n')
            joined = joined.merge(cands, on='bank_txn_id')
            good = joined[(joined['nscore'] > 0.2) | (joined['n'] == 1)]
            best = good.sort_values(['bank_txn_id','nscore','issue_date'],
                                     ascending=[True, False, True]) \
                        .drop_duplicates(subset='bank_txn_id', keep='first')

            out = best[['bank_txn_id','item_id']].copy()
            out['amount'] = best['total_amount']
            out['category'] = ''
            out['exception'] = 'FX_DIFFERENCE'
            all_out.append(out)

    # AP: 1:1 matching for negatives
    bt_fx_neg = bt_fx[bt_fx['amount'] < 0].copy()
    if len(bt_fx_neg) > 0:
        bt_fx_neg['orig_round'] = bt_fx_neg['orig_amount'].abs().round(2)
        bt_fx_neg['cp_norm'] = bt_fx_neg['counterparty_name'].apply(norm_name)

        ap_m = ap[['item_id','entity','currency','total_amount','supplier_name','supplier_id','issue_date']].copy()
        ap_m['amount_round'] = ap_m['total_amount'].round(2)
        ap_m['name_norm'] = ap_m['supplier_name'].apply(norm_name)

        joined = bt_fx_neg[['bank_txn_id','entity','orig_currency','orig_round','cp_norm']].merge(
            ap_m[['item_id','entity','currency','amount_round','name_norm','total_amount','issue_date']],
            left_on=['entity','orig_currency','orig_round'],
            right_on=['entity','currency','amount_round'],
            how='inner'
        )

        if len(joined) > 0:
            joined['nscore'] = _fast_name_score(joined['cp_norm'].values, joined['name_norm'].values)
            cands = joined.groupby('bank_txn_id').size().reset_index(name='n')
            joined = joined.merge(cands, on='bank_txn_id')
            good = joined[(joined['nscore'] > 0.2) | (joined['n'] == 1)]
            best = good.sort_values(['bank_txn_id','nscore','issue_date'],
                                     ascending=[True, False, True]) \
                        .drop_duplicates(subset='bank_txn_id', keep='first')

            out = best[['bank_txn_id','item_id']].copy()
            out['amount'] = best['total_amount']
            out['category'] = ''
            out['exception'] = 'FX_DIFFERENCE'
            all_out.append(out)

        # AP 1:N FX: match by name, then subset-sum of invoices = orig_amount
        matched_1to1 = set()
        if all_out:
            for df in all_out:
                matched_1to1 |= set(df['bank_txn_id'].unique())

        bt_fx_rem = bt_fx_neg[~bt_fx_neg['bank_txn_id'].isin(matched_1to1)].copy()
        if len(bt_fx_rem) > 0:
            supp_names = ap_m.drop_duplicates('supplier_id')[['supplier_id','name_norm']].copy()
            name_to_sid = supp_names.set_index('name_norm')['supplier_id'].to_dict()

            bt_fx_rem['supplier_id'] = bt_fx_rem['cp_norm'].map(name_to_sid)
            bt_name_ok = bt_fx_rem[bt_fx_rem['supplier_id'].notna()].copy()

            if len(bt_name_ok) > 0:
                ap_sorted = ap_m.sort_values('issue_date')
                ap_by_supp = {}
                for (ent, cur, sid), grp in ap_sorted.groupby(['entity','currency','supplier_id']):
                    ap_by_supp[(ent, cur, sid)] = list(zip(grp['item_id'], grp['total_amount']))

                fx_1n_results = []
                for _, bt_row in bt_name_ok.iterrows():
                    target = round(abs(bt_row['orig_amount']), 2)
                    txn_id = bt_row['bank_txn_id']
                    orig_cur = bt_row['orig_currency']
                    ent = bt_row['entity']
                    sid = bt_row['supplier_id']
                    tol = max(TOL_ABS, TOL_PCT * target)

                    invoices = ap_by_supp.get((ent, orig_cur, sid), [])
                    if not invoices: continue
                    inv_sorted = sorted(invoices, key=lambda x: -x[1])

                    selected = []
                    remaining = target
                    for item_id, amt in inv_sorted:
                        if round(amt, 2) <= round(remaining + TOL_ABS, 2):
                            selected.append((item_id, amt))
                            remaining -= amt
                            if abs(remaining) <= tol:
                                break

                    if abs(remaining) <= tol and selected:
                        for item_id, amt in selected:
                            fx_1n_results.append({
                                'bank_txn_id': txn_id, 'item_id': item_id,
                                'amount': amt, 'category': '', 'exception': 'FX_DIFFERENCE'
                            })

                if fx_1n_results:
                    all_out.append(pd.DataFrame(fx_1n_results))

    if not all_out:
        log.info("  FX: 0 matches")
        return pd.DataFrame(), matched_set

    out = pd.concat(all_out, ignore_index=True)
    out = out.drop_duplicates(subset=['bank_txn_id','item_id'], keep='first')
    new_matched = set(out['bank_txn_id'].unique())
    log.info(f"  FX: {len(out):,} pairs / {len(new_matched):,} txns")
    return out, matched_set | new_matched


def _extract_name_from_text(text):
    """Extract supplier/customer name from BT text using common patterns."""
    if not text:
        return ''
    t = text.upper()
    patterns = [
        r'(?:SEPA-UEBERWEISUNG AN|UEBERWEISUNG AN)\s+(.+?)(?:\s*/|$)',
        r'VIR SEPA EMIS\s*/A\s+(.+?)(?:\s*/|$)',
        r'TRF\.?INTERNACIONAL\s*\|\s*(.+?)(?:\s*/|$)',
        r'PIX ENVIADO\s+(.+?)(?:\s+FRAS\b|\s*/|$)',
        r'TT IN\s+(.+?)(?:\s+INVOICE\b|\s*/|$)',
        r'/ORG/(.+?)(?:\s*/|$)',
        r'WIRE IN\s*/ORG/(.+?)(?:\s*/|$)',
    ]
    for pat in patterns:
        m = re.search(pat, t)
        if m:
            name = m.group(1).strip()
            if len(name) >= 3:
                return norm_name(name)
    return ''


def step_ap_multi_invoice_text(bt, ap, matched_set):
    """Match AP BTs with multiple invoice numbers in text (FRAS pattern)."""
    bt_unm = bt[~bt['bank_txn_id'].isin(matched_set)].copy()
    bt_neg = bt_unm[bt_unm['amount'] < 0].copy()
    if len(bt_neg) == 0:
        return pd.DataFrame(), matched_set

    ap_inv_lookup = {}
    ap_valid = ap[ap['supplier_invoice_number'].fillna('').str.len() >= 3].copy()
    ap_valid['inv_upper'] = ap_valid['supplier_invoice_number'].str.upper().str.strip()
    for row in ap_valid[['inv_upper','item_id','entity','currency','total_amount']].itertuples(index=False):
        if row.inv_upper not in ap_inv_lookup:
            ap_inv_lookup[row.inv_upper] = []
        ap_inv_lookup[row.inv_upper].append((row.item_id, row.entity, row.currency, row.total_amount))
    inv_set = set(ap_inv_lookup.keys())
    log.info(f"  AP-text: {len(bt_neg):,} candidate BTs, {len(inv_set):,} invoice keys")

    all_results = []
    for row in bt_neg.itertuples(index=False):
        text = (str(row.text) + ' ' + str(getattr(row, 'remittance_info', ''))).upper()
        tokens = re.findall(r'[A-Z0-9][\w./-]{2,}', text)
        bt_ent = row.entity
        bt_cur = row.currency
        bt_abs = abs(row.amount)

        found = {}
        for tok in tokens:
            tok_clean = tok.strip().rstrip('.')
            if tok_clean in inv_set:
                for item_id, ent, cur, amt in ap_inv_lookup[tok_clean]:
                    if ent == bt_ent and cur == bt_cur:
                        found[item_id] = amt

        if len(found) >= 2:
            inv_sum = sum(abs(a) for a in found.values())
            tol = max(TOL_ABS, 0.02 * bt_abs)
            if abs(inv_sum - bt_abs) <= tol:
                for item_id, amt in found.items():
                    all_results.append({
                        'bank_txn_id': row.bank_txn_id, 'item_id': item_id,
                        'amount': amt, 'category': '', 'exception': ''
                    })

    if not all_results:
        log.info("  AP-text multi: 0 matches")
        return pd.DataFrame(), matched_set

    out = pd.DataFrame(all_results)
    out = out.drop_duplicates(subset=['bank_txn_id','item_id'], keep='first')
    new_matched = set(out['bank_txn_id'].unique())
    log.info(f"  AP-text multi: {len(out):,} pairs / {len(new_matched):,} txns")
    return out, matched_set | new_matched


def step_fx_text_match(bt, ar, ap, matched_set):
    """FX matching via invoice number reverse-lookup in text + name extraction.
    Handles cross-entity (holding) and BTs without counterparty_name."""
    bt_unm = bt[~bt['bank_txn_id'].isin(matched_set)].copy()
    if len(bt_unm) == 0:
        return pd.DataFrame(), matched_set

    bt_fx = bt_unm[
        (bt_unm['orig_amount'].notna()) &
        (bt_unm['orig_currency'] != '') &
        (bt_unm['orig_currency'] != bt_unm['currency'])
    ].copy()
    if len(bt_fx) == 0:
        log.info("  FX-text: 0 FX BTs")
        return pd.DataFrame(), matched_set

    log.info(f"  FX-text: {len(bt_fx)} candidate BTs")

    # Build entity families (holding: entity ending in 00 -> children)
    all_ents = set(bt['entity'].unique()) | set(ar['entity'].unique()) | set(ap['entity'].unique())
    ent_family = {}
    for e in all_ents:
        family = {e}
        prefix = e[:2] if len(e) >= 4 else e
        if e.endswith('00'):
            family |= {x for x in all_ents if x.startswith(prefix) and x != e}
        else:
            parent = prefix + '00'
            if parent in all_ents:
                family.add(parent)
                family |= {x for x in all_ents if x.startswith(prefix)}
        ent_family[e] = family

    # Determine which (entity, currency) combos we need
    needed_ent_cur = set()
    for row in bt_fx.itertuples(index=False):
        fam = ent_family.get(row.entity, {row.entity})
        for e in fam:
            needed_ent_cur.add((e, row.orig_currency))
    needed_df = pd.DataFrame(list(needed_ent_cur), columns=['entity','currency'])

    all_out = []

    # --- AP side (negative BTs) ---
    bt_fx_neg = bt_fx[bt_fx['amount'] < 0].copy()
    if len(bt_fx_neg) > 0:
        # Only load AP invoices for needed entity+currency combos
        ap_sub = ap.merge(needed_df, on=['entity','currency'], how='inner').copy()
        log.info(f"  FX-text AP: {len(ap_sub)} candidate invoices for {len(bt_fx_neg)} BTs")

        ap_sub['inv_num_upper'] = ap_sub['supplier_invoice_number'].fillna('').str.upper().str.strip()
        ap_inv = ap_sub[ap_sub['inv_num_upper'].str.len() >= 4]

        # Build lookup
        ap_lookup = {}
        for row in ap_inv[['inv_num_upper','item_id','entity','currency','total_amount','supplier_name']].itertuples(index=False):
            key = row.inv_num_upper
            if key not in ap_lookup:
                ap_lookup[key] = []
            ap_lookup[key].append((row.item_id, row.entity, row.currency, row.total_amount, row.supplier_name))
        inv_num_set = set(ap_lookup.keys())

        fx_text_results = []
        fx_name_remaining = []

        for row in bt_fx_neg.itertuples(index=False):
            txn_id = row.bank_txn_id
            bt_ent = row.entity
            orig_amt = abs(row.orig_amount)
            orig_cur = row.orig_currency
            text = str(row.text).upper() + ' ' + str(row.remittance_info).upper()
            family = ent_family.get(bt_ent, {bt_ent})

            tokens = re.findall(r'[A-Z0-9][\w./-]{2,}', text)
            found_invs = []
            for tok in tokens:
                tok_clean = tok.strip().rstrip('.')
                if tok_clean in inv_num_set:
                    for item_id, ent, cur, amt, sname in ap_lookup[tok_clean]:
                        if cur == orig_cur and ent in family:
                            found_invs.append((item_id, amt))

            if found_invs:
                found_invs = list(dict(found_invs).items())
                inv_sum = sum(abs(a) for _, a in found_invs)
                tol = max(TOL_ABS, 0.05 * orig_amt)
                if abs(inv_sum - orig_amt) <= tol or (inv_sum < orig_amt and inv_sum > orig_amt * 0.5):
                    for item_id, amt in found_invs:
                        fx_text_results.append({
                            'bank_txn_id': txn_id, 'item_id': item_id,
                            'amount': amt, 'category': '', 'exception': 'FX_DIFFERENCE'
                        })
                else:
                    fx_name_remaining.append(row)
            else:
                fx_name_remaining.append(row)

        if fx_text_results:
            all_out.append(pd.DataFrame(fx_text_results))
            log.info(f"  FX-text AP invoice: {len(fx_text_results)} pairs")

        # Pass 2: Name-based matching for remaining
        if fx_name_remaining:
            # Pre-norm supplier names only for needed combos
            ap_sub['name_norm'] = ap_sub['supplier_name'].apply(norm_name)
            ap_by_ec = {}
            for (ent, cur), grp in ap_sub.groupby(['entity', 'currency']):
                ap_by_ec[(ent, cur)] = grp

            name_results = []
            for row in fx_name_remaining:
                txn_id = row.bank_txn_id
                bt_ent = row.entity
                orig_amt = abs(row.orig_amount)
                orig_cur = row.orig_currency
                text_name = _extract_name_from_text(row.text)
                cp_norm = norm_name(row.counterparty_name)
                match_name = text_name if text_name else cp_norm
                if not match_name:
                    continue
                family = ent_family.get(bt_ent, {bt_ent})

                cand_parts = []
                for e in family:
                    if (e, orig_cur) in ap_by_ec:
                        cand_parts.append(ap_by_ec[(e, orig_cur)])
                if not cand_parts:
                    continue
                cands = pd.concat(cand_parts) if len(cand_parts) > 1 else cand_parts[0]

                match_words = set(match_name.split())
                if not match_words:
                    continue

                cands = cands.copy()
                cands['nscore'] = _fast_name_score(
                    np.array([match_name] * len(cands)),
                    cands['name_norm'].values)
                good = cands[cands['nscore'] >= 0.3]
                if len(good) == 0:
                    continue

                tol = max(TOL_ABS, TOL_PCT * orig_amt)
                exact = good[abs(good['total_amount'].abs() - orig_amt) <= tol]
                if len(exact) == 1:
                    r = exact.iloc[0]
                    name_results.append({
                        'bank_txn_id': txn_id, 'item_id': r['item_id'],
                        'amount': r['total_amount'], 'category': '',
                        'exception': 'FX_DIFFERENCE'
                    })
                    continue

                invoices = list(zip(good['item_id'], good['total_amount'].abs()))
                res = _greedy_subset_sum(invoices, orig_amt, tol)
                if res:
                    for item_id, amt in res:
                        name_results.append({
                            'bank_txn_id': txn_id, 'item_id': item_id,
                            'amount': amt, 'category': '', 'exception': 'FX_DIFFERENCE'
                        })

            if name_results:
                all_out.append(pd.DataFrame(name_results))
                log.info(f"  FX-text AP name: {len(name_results)} pairs")

    # --- AR side (positive BTs) ---
    bt_fx_pos = bt_fx[bt_fx['amount'] > 0].copy()
    if len(bt_fx_pos) > 0:
        ar_sub = ar.merge(needed_df, on=['entity','currency'], how='inner').copy()
        log.info(f"  FX-text AR: {len(ar_sub)} candidate invoices for {len(bt_fx_pos)} BTs")

        ar_sub['doc_upper'] = ar_sub['document_number'].fillna('').str.upper().str.strip()
        ar_inv = ar_sub[ar_sub['doc_upper'].str.len() >= 4]

        ar_lookup = {}
        for row in ar_inv[['doc_upper','item_id','entity','currency','total_amount','customer_name']].itertuples(index=False):
            key = row.doc_upper
            if key not in ar_lookup:
                ar_lookup[key] = []
            ar_lookup[key].append((row.item_id, row.entity, row.currency, row.total_amount, row.customer_name))
        ar_num_set = set(ar_lookup.keys())

        fx_ar_results = []
        ar_name_remaining = []

        for row in bt_fx_pos.itertuples(index=False):
            txn_id = row.bank_txn_id
            bt_ent = row.entity
            orig_amt = abs(row.orig_amount)
            orig_cur = row.orig_currency
            text = str(row.text).upper() + ' ' + str(row.remittance_info).upper()
            family = ent_family.get(bt_ent, {bt_ent})

            tokens = re.findall(r'[A-Z0-9][\w./-]{2,}', text)
            found_invs = []
            for tok in tokens:
                tok_clean = tok.strip().rstrip('.')
                if tok_clean in ar_num_set:
                    for item_id, ent, cur, amt, cname in ar_lookup[tok_clean]:
                        if cur == orig_cur and ent in family:
                            found_invs.append((item_id, amt))

            if found_invs:
                found_invs = list(dict(found_invs).items())
                inv_sum = sum(abs(a) for _, a in found_invs)
                tol = max(TOL_ABS, 0.05 * orig_amt)
                if abs(inv_sum - orig_amt) <= tol or (inv_sum < orig_amt and inv_sum > orig_amt * 0.5):
                    for item_id, amt in found_invs:
                        fx_ar_results.append({
                            'bank_txn_id': txn_id, 'item_id': item_id,
                            'amount': amt, 'category': '', 'exception': 'FX_DIFFERENCE'
                        })
                else:
                    ar_name_remaining.append(row)
            else:
                ar_name_remaining.append(row)

        if fx_ar_results:
            all_out.append(pd.DataFrame(fx_ar_results))
            log.info(f"  FX-text AR invoice: {len(fx_ar_results)} pairs")

        # AR name-based skipped: too slow for 141K+ candidates per 6K BTs

    if not all_out:
        log.info("  FX-text: 0 matches")
        return pd.DataFrame(), matched_set

    out = pd.concat(all_out, ignore_index=True)
    out = out.drop_duplicates(subset=['bank_txn_id', 'item_id'], keep='first')
    new_matched = set(out['bank_txn_id'].unique())
    log.info(f"  FX-text: {len(out):,} pairs / {len(new_matched):,} txns")
    return out, matched_set | new_matched


def step_discount_match(bt, ar, ap, matched_set):
    """Match where BT amount = invoice net_amount (discount = tax_amount skipped)."""
    bt_unm = bt[~bt['bank_txn_id'].isin(matched_set)].copy()
    if len(bt_unm) == 0: return pd.DataFrame(), matched_set

    all_out = []

    # AR: BT amount matches net_amount (customer took discount)
    bt_pos = bt_unm[bt_unm['amount'] > 0].copy()
    if len(bt_pos) > 0:
        bt_pos['amount_round'] = bt_pos['amount'].round(2)
        bt_pos['cp_norm'] = bt_pos['counterparty_name'].apply(norm_name)

        # Try matching BT amount to AR net_amount (discount = total - net)
        ar_m = ar[['item_id','entity','currency','total_amount','net_amount','customer_name','issue_date']].copy()
        ar_m['net_round'] = ar_m['net_amount'].round(2)
        ar_m['name_norm'] = ar_m['customer_name'].apply(norm_name)

        # Only where net != total (there IS a discount)
        ar_m = ar_m[ar_m['net_round'] != ar_m['total_amount'].round(2)]

        joined = bt_pos[['bank_txn_id','entity','currency','amount_round','cp_norm']].merge(
            ar_m[['item_id','entity','currency','net_round','name_norm','total_amount','issue_date']],
            left_on=['entity','currency','amount_round'],
            right_on=['entity','currency','net_round'],
            how='inner'
        )

        if len(joined) > 0:
            def word_sim(row):
                w1 = set(row['cp_norm'].split())
                w2 = set(row['name_norm'].split())
                if not w1 or not w2: return 0
                return len(w1 & w2) / len(w1 | w2)

            joined['nscore'] = _fast_name_score(joined['cp_norm'].values, joined['name_norm'].values)
            good = joined[joined['nscore'] > 0.3]
            if len(good) > 0:
                best = good.sort_values(['bank_txn_id','nscore','issue_date'],
                                         ascending=[True, False, True]) \
                            .drop_duplicates(subset='bank_txn_id', keep='first')

                out = best[['bank_txn_id','item_id']].copy()
                out['amount'] = best['total_amount']
                out['category'] = ''
                out['exception'] = 'SHORT_DISCOUNT'
                all_out.append(out)

    # AP: same for negatives
    bt_neg = bt_unm[bt_unm['amount'] < 0].copy()
    if len(bt_neg) > 0:
        bt_neg['amount_round'] = bt_neg['amount'].abs().round(2)
        bt_neg['cp_norm'] = bt_neg['counterparty_name'].apply(norm_name)

        ap_m = ap[['item_id','entity','currency','total_amount','net_amount','supplier_name','issue_date']].copy()
        ap_m['net_round'] = ap_m['net_amount'].round(2)
        ap_m['name_norm'] = ap_m['supplier_name'].apply(norm_name)
        ap_m = ap_m[ap_m['net_round'] != ap_m['total_amount'].round(2)]

        joined = bt_neg[['bank_txn_id','entity','currency','amount_round','cp_norm']].merge(
            ap_m[['item_id','entity','currency','net_round','name_norm','total_amount','issue_date']],
            left_on=['entity','currency','amount_round'],
            right_on=['entity','currency','net_round'],
            how='inner'
        )

        if len(joined) > 0:
            joined['nscore'] = _fast_name_score(joined['cp_norm'].values, joined['name_norm'].values)
            good = joined[joined['nscore'] > 0.3]
            if len(good) > 0:
                best = good.sort_values(['bank_txn_id','nscore','issue_date'],
                                         ascending=[True, False, True]) \
                            .drop_duplicates(subset='bank_txn_id', keep='first')

                out = best[['bank_txn_id','item_id']].copy()
                out['amount'] = best['total_amount']
                out['category'] = ''
                out['exception'] = 'SHORT_DISCOUNT'
                all_out.append(out)

    if not all_out:
        log.info("  Discount: 0 matches")
        return pd.DataFrame(), matched_set

    out = pd.concat(all_out, ignore_index=True)
    new_matched = set(out['bank_txn_id'].unique())
    log.info(f"  Discount: {len(out):,} pairs / {len(new_matched):,} txns")
    return out, matched_set | new_matched


def step_partial_match(bt, ar, ap, matched_set):
    """Match BT where amount is a fraction of an invoice (PARTIAL exception)."""
    bt_unm = bt[~bt['bank_txn_id'].isin(matched_set)].copy()
    if len(bt_unm) == 0: return pd.DataFrame(), matched_set

    all_out = []

    # AR: positive BTs where amount < AR total but > 10% of total
    bt_pos = bt_unm[bt_unm['amount'] > 0].copy()
    if len(bt_pos) > 0:
        bt_pos['amount_round'] = bt_pos['amount'].round(2)
        bt_pos['cp_norm'] = bt_pos['counterparty_name'].apply(norm_name)

        ar_m = ar[['item_id','entity','currency','total_amount','customer_name','issue_date']].copy()
        ar_m['name_norm'] = ar_m['customer_name'].apply(norm_name)

        # For each BT, find AR invoices where bt_amount is a fraction (1/2, 1/3, 1/4, 1/5)
        already_matched = set()
        for divisor in [2, 3, 4, 5]:
            bt_try = bt_pos[~bt_pos['bank_txn_id'].isin(already_matched)].copy()
            if len(bt_try) == 0: continue

            bt_try['target_total'] = (bt_try['amount'] * divisor).round(2)

            ar_m_r = ar_m.copy()
            ar_m_r['total_round'] = ar_m_r['total_amount'].round(2)

            joined = bt_try[['bank_txn_id','entity','currency','target_total','cp_norm','amount_round']].merge(
                ar_m_r[['item_id','entity','currency','total_round','name_norm','issue_date']],
                left_on=['entity','currency','target_total'],
                right_on=['entity','currency','total_round'],
                how='inner'
            )

            if len(joined) == 0: continue

            joined['nscore'] = _fast_name_score(joined['cp_norm'].values, joined['name_norm'].values)
            cands = joined.groupby('bank_txn_id').size().reset_index(name='n')
            joined = joined.merge(cands, on='bank_txn_id')
            good = joined[(joined['nscore'] > 0.3) | (joined['n'] == 1)]

            if len(good) == 0: continue

            best = good.sort_values(['bank_txn_id','nscore','issue_date'],
                                     ascending=[True, False, True]) \
                        .drop_duplicates(subset='bank_txn_id', keep='first')

            out = best[['bank_txn_id','item_id']].copy()
            out['amount'] = best['amount_round']
            out['category'] = ''
            out['exception'] = 'PARTIAL'
            all_out.append(out)
            already_matched |= set(out['bank_txn_id'])

    # AP: negative BTs where |amount| is a fraction of AP total
    bt_neg = bt_unm[bt_unm['amount'] < 0].copy()
    if len(bt_neg) > 0:
        bt_neg['abs_amount'] = bt_neg['amount'].abs().round(2)
        bt_neg['cp_norm'] = bt_neg['counterparty_name'].apply(norm_name)

        ap_m = ap[['item_id','entity','currency','total_amount','supplier_name','issue_date']].copy()
        ap_m['name_norm'] = ap_m['supplier_name'].apply(norm_name)

        ap_already = set()
        for divisor in [2, 3, 4, 5]:
            bt_try = bt_neg[~bt_neg['bank_txn_id'].isin(ap_already)].copy()
            if len(bt_try) == 0: continue

            bt_try['target_total'] = (bt_try['abs_amount'] * divisor).round(2)

            ap_m_r = ap_m.copy()
            ap_m_r['total_round'] = ap_m_r['total_amount'].round(2)

            joined = bt_try[['bank_txn_id','entity','currency','target_total','cp_norm','abs_amount']].merge(
                ap_m_r[['item_id','entity','currency','total_round','name_norm','issue_date']],
                left_on=['entity','currency','target_total'],
                right_on=['entity','currency','total_round'],
                how='inner'
            )

            if len(joined) == 0: continue

            joined['nscore'] = _fast_name_score(joined['cp_norm'].values, joined['name_norm'].values)
            cands = joined.groupby('bank_txn_id').size().reset_index(name='n')
            joined = joined.merge(cands, on='bank_txn_id')
            good = joined[(joined['nscore'] > 0.3) | (joined['n'] == 1)]

            if len(good) == 0: continue

            best = good.sort_values(['bank_txn_id','nscore','issue_date'],
                                     ascending=[True, False, True]) \
                        .drop_duplicates(subset='bank_txn_id', keep='first')

            out = best[['bank_txn_id','item_id']].copy()
            out['amount'] = best['abs_amount']
            out['category'] = ''
            out['exception'] = 'PARTIAL'
            all_out.append(out)
            ap_already |= set(out['bank_txn_id'])

    if not all_out:
        log.info("  Partial: 0 matches")
        return pd.DataFrame(), matched_set

    out = pd.concat(all_out, ignore_index=True)
    out = out.drop_duplicates(subset=['bank_txn_id','item_id'], keep='first')
    new_matched = set(out['bank_txn_id'].unique())
    log.info(f"  Partial: {len(out):,} pairs / {len(new_matched):,} txns")
    return out, matched_set | new_matched


def step_holding_company(bt, ap, matched_set):
    """Cross-entity: parent company (ES00) pays subsidiary AP invoices."""
    bt_unm = bt[~bt['bank_txn_id'].isin(matched_set)].copy()
    if len(bt_unm) == 0:
        return pd.DataFrame(), matched_set

    # Infer parent-subsidiary from data: parent entity has BTs paying child invoices
    # Known: ES00 → ES01, ES02, PT01 (from golden analysis)
    # Generalize: look for entities with "00" suffix paying for entities with other suffixes
    parent_entities = set()
    child_entities = set()
    all_entities = set(bt['entity'].unique())
    for e in all_entities:
        if e.endswith('00'):
            parent_entities.add(e)
            prefix = e[:-2]
            for e2 in all_entities:
                if e2.startswith(prefix) and e2 != e:
                    child_entities.add((e, e2))
    # Also check for known PT pattern
    for e in all_entities:
        if e.startswith('PT') and e != 'PT00':
            for p in parent_entities:
                child_entities.add((p, e))

    if not child_entities:
        log.info("  Holding: no parent-child entities found")
        return pd.DataFrame(), matched_set

    all_out = []

    for parent, child in child_entities:
        bt_parent = bt_unm[(bt_unm['entity'] == parent) & (bt_unm['amount'] < 0)].copy()
        if len(bt_parent) == 0:
            continue

        bt_parent['abs_amount'] = bt_parent['amount'].abs().round(2)
        bt_parent['text_upper'] = bt_parent['text'].fillna('').str.upper()

        ap_child = ap[ap['entity'] == child][
            ['item_id','entity','currency','total_amount','supplier_name','supplier_invoice_number']
        ].copy()
        ap_child['total_round'] = ap_child['total_amount'].round(2)
        ap_child['name_norm'] = ap_child['supplier_name'].apply(norm_name)

        # Method 1: Extract supplier name from "TRANSFERENCIA A <supplier>" text
        transf_mask = bt_parent['text_upper'].str.contains('TRANSFERENCIA A ', na=False)
        bt_transf = bt_parent[transf_mask].copy()

        if len(bt_transf) > 0:
            bt_transf['extracted_supplier'] = bt_transf['text_upper'].str.extract(
                r'TRANSFERENCIA\s+A\s+(.+?)(?:\s*\|\s*FRA|\s*$)', expand=False
            ).fillna('').str.strip()
            bt_transf['supplier_norm'] = bt_transf['extracted_supplier'].apply(norm_name)

            # 1:1 amount match first
            joined = bt_transf[['bank_txn_id','currency','abs_amount','supplier_norm']].merge(
                ap_child[['item_id','currency','total_round','name_norm']],
                left_on=['currency','abs_amount'],
                right_on=['currency','total_round'],
                how='inner'
            )
            if len(joined) > 0:
                joined['nscore'] = _fast_name_score(joined['supplier_norm'].values, joined['name_norm'].values)
                good = joined[joined['nscore'] > 0.2]
                if len(good) > 0:
                    best = good.sort_values(['bank_txn_id','nscore'],
                                             ascending=[True, False]) \
                                .drop_duplicates(subset='bank_txn_id', keep='first')
                    out = best[['bank_txn_id','item_id']].copy()
                    out['amount'] = best['abs_amount']
                    out['category'] = ''
                    out['exception'] = ''
                    all_out.append(out)

        # Method 2: For remaining unmatched parent BTs, pure amount 1:1 match
        already = set()
        if all_out:
            for d in all_out:
                already |= set(d['bank_txn_id'])
        bt_remain = bt_parent[~bt_parent['bank_txn_id'].isin(already)]
        if len(bt_remain) > 0:
            joined2 = bt_remain[['bank_txn_id','currency','abs_amount']].merge(
                ap_child[['item_id','currency','total_round']],
                left_on=['currency','abs_amount'],
                right_on=['currency','total_round'],
                how='inner'
            )
            if len(joined2) > 0:
                cands = joined2.groupby('bank_txn_id').size().reset_index(name='n')
                unique = cands[cands['n'] == 1]['bank_txn_id']
                good2 = joined2[joined2['bank_txn_id'].isin(unique)]
                inv_cands = good2.groupby('item_id').size().reset_index(name='ni')
                unique_inv = inv_cands[inv_cands['ni'] == 1]['item_id']
                good2 = good2[good2['item_id'].isin(unique_inv)]

                if len(good2) > 0:
                    out2 = good2[['bank_txn_id','item_id']].copy()
                    out2['amount'] = good2['abs_amount']
                    out2['category'] = ''
                    out2['exception'] = ''
                    all_out.append(out2)

    if not all_out:
        log.info("  Holding: 0 matches")
        return pd.DataFrame(), matched_set

    out = pd.concat(all_out, ignore_index=True)
    out = out.drop_duplicates(subset=['bank_txn_id','item_id'], keep='first')
    new_matched = set(out['bank_txn_id'].unique())
    log.info(f"  Holding: {len(out):,} pairs / {len(new_matched):,} txns")
    return out, matched_set | new_matched


def step_nonitem(bt, matched_set):
    """Classify unmatched transactions into nonitem categories."""
    bt_unm = bt[~bt['bank_txn_id'].isin(matched_set)].copy()
    if len(bt_unm) == 0: return pd.DataFrame()

    text_lower = (bt_unm['text'].fillna('') + ' ' + bt_unm['bank_code'].fillna('') + ' ' +
                  bt_unm['bank_subcode'].fillna('')).str.lower()

    conditions = [
        (text_lower.str.contains(r'comis|gast[oe]|fee|charge|cargo|commission|frais|geb.hr|spesen|tarifa', regex=True, na=False), 'BANK_FEES'),
        (text_lower.str.contains(r'cash.?pool|nivelacion|pooling|sweeping|zero.?balance|equilibrado', regex=True, na=False), 'CASH_POOL'),
        (text_lower.str.contains(r'nomina|sueldo|payroll|nettolohn|gehalt|salario|lohn', regex=True, na=False), 'PAYROLL'),
        (text_lower.str.contains(r'impuesto|mod.?[13]\d{2}|hacienda|tax.?pay|steuer|aeat|iva|irpf', regex=True, na=False), 'TAX_PAYMENT'),
        (text_lower.str.contains(r'seguridad social|cotizacion|sozialversicherung|ss.?|tesoreria general', regex=True, na=False), 'SOCIAL_SECURITY'),
        (text_lower.str.contains(r'seguro |insurance|versicherung|assurance|poliza', regex=True, na=False), 'INSURANCE'),
        (text_lower.str.contains(r'prestamo|loan|amortizacion|darlehen|tilgung', regex=True, na=False), 'LOAN_REPAYMENT'),
        (text_lower.str.contains(r'arrendamiento|leasing|renting|alquiler', regex=True, na=False), 'LEASING'),
        (text_lower.str.contains(r'intercompany|interco|zwischen', regex=True, na=False), 'INTERCOMPANY'),
        (text_lower.str.contains(r'traspaso|transfer.?intern|internal.?transfer|umbuchung', regex=True, na=False), 'INTERNAL_TRANSFER'),
        (text_lower.str.contains(r'interes.?a.?favor|interest.?income|zinsertr|habenzins', regex=True, na=False), 'INTEREST_INCOME'),
        (text_lower.str.contains(r'interes.?deud|interest.?expense|zinsaufw|sollzins', regex=True, na=False), 'INTEREST_EXPENSE'),
        (text_lower.str.contains(r'fx.?deal|forex|compra.?divisa|venta.?divisa|devisenhandel', regex=True, na=False), 'FX_DEAL'),
        (text_lower.str.contains(r'tarjeta.?empresa|card.?expense|kreditkarte|visa.?business|mastercard.?corp', regex=True, na=False), 'CARD_EXPENSES'),
        (text_lower.str.contains(r'devol.*cliente|customer.?refund|erstattung.*kund|reembolso.*client', regex=True, na=False), 'CUSTOMER_REFUND'),
        (text_lower.str.contains(r'anticipo.*client|customer.?advance|anzahlung.*kund|adelanto.*client', regex=True, na=False), 'CUSTOMER_ADVANCE'),
        (text_lower.str.contains(r'devol.*provee|supplier.?refund|erstattung.*liefer', regex=True, na=False), 'SUPPLIER_REFUND'),
        (text_lower.str.contains(r'desembolso|drawdown|disposicion', regex=True, na=False), 'LOAN_DRAWDOWN'),
        (text_lower.str.contains(r'anulacion|reversal|storno|contra.?asiento|extorno', regex=True, na=False), 'BANK_REVERSAL'),
    ]

    bt_unm = bt_unm.copy()
    bt_unm['category'] = 'UNIDENTIFIED'
    bt_unm['exception'] = 'UNIDENTIFIED'

    for cond, cat in conditions:
        mask = (bt_unm['category'] == 'UNIDENTIFIED') & cond
        bt_unm.loc[mask, 'category'] = cat
        bt_unm.loc[mask, 'exception'] = ''

    out = bt_unm[['bank_txn_id']].copy()
    out['item_id'] = ''
    out['amount'] = bt_unm['amount'].abs()
    out['category'] = bt_unm['category'].values
    out['exception'] = bt_unm['exception'].values

    cats = out['category'].value_counts()
    log.info(f"  Nonitem: {len(out):,} ({cats.get('UNIDENTIFIED',0):,} UNIDENTIFIED)")
    return out


# Exception policy constants (POLICIES.md)
DISCOUNT_TERMS = {            # code -> (discount %, discount days)
    'D2N30': (2.0, 10), 'D1N60': (1.0, 15), 'D3N45': (3.0, 8), 'D2N30US': (2.0, 10),
}
DISCOUNT_GRACE_DAYS = 3
WITHHOLDING_COUNTRIES = {'MX', 'BR', 'MA'}
WITHHOLDING_RATES = (0.0125, 0.015, 0.10)
BANK_CHARGE_MAX_ABS = 60.0
BANK_CHARGE_MAX_PCT = 0.02
BANK_CHARGE_CODES = {'PMNT/RCDT/XBCT', '13'}
USE_RATIO_DISCOUNT_FALLBACK = False


def _load_customer_country():
    """customer_id -> country from masters/customers.csv (empty dict if unavailable)."""
    here = Path(__file__).resolve().parent
    cands = [here / 'Paella_Hackathon' / 'bankrec-docs-and-masters' / 'masters' / 'customers.csv',
             here / 'masters' / 'customers.csv', Path('masters') / 'customers.csv',
             here / 'data' / 'masters' / 'customers.csv']
    for p in cands:
        if p.exists():
            try:
                c = pd.read_csv(p, dtype=str, usecols=['customer_id', 'country'])
                log.info(f"  customers master: {p} ({len(c):,})")
                return dict(zip(c['customer_id'], c['country'].fillna('')))
            except Exception as e:
                log.warning(f"  customers master unreadable ({p}): {e}")
    log.warning("  customers master not found; country inferred from invoice currency")
    return {}


def postprocess_exceptions(final, bt, ar, ap):
    """Label exceptions on already-matched pairs (vectorized)."""
    items = final[(final['item_id'] != '') & (final['exception'] == '')].copy()
    if len(items) == 0:
        return final

    # Merge invoice totals
    ar_tot = ar[['item_id','total_amount']].rename(columns={'total_amount':'inv_total'})
    ap_tot = ap[['item_id','total_amount']].rename(columns={'total_amount':'inv_total'})
    inv_tot = pd.concat([ar_tot, ap_tot], ignore_index=True).drop_duplicates(subset='item_id')

    items = items.merge(inv_tot, on='item_id', how='left')
    items['inv_total'] = items['inv_total'].abs()
    items['alloc_abs'] = items['amount'].abs()

    # Invoice metadata for rule-based exceptions (terms, dates, currency, country)
    ar_m = ar[['item_id','issue_date','payment_terms','currency','net_amount','customer_id']].copy()
    ap_m = ap[['item_id','issue_date','payment_terms','currency','net_amount']].copy()
    ap_m['customer_id'] = ''
    inv_meta = pd.concat([ar_m, ap_m], ignore_index=True).drop_duplicates(subset='item_id')
    inv_meta = inv_meta.rename(columns={'currency':'inv_cur','net_amount':'inv_net'})
    inv_meta['inv_net'] = inv_meta['inv_net'].abs()
    cust_country = _load_customer_country()
    inv_meta['country'] = inv_meta['customer_id'].map(cust_country).fillna('')
    # Fallback when customers master unavailable: infer from invoice currency
    cur_cty = {'BRL':'BR','MXN':'MX','MAD':'MA'}
    fb = inv_meta['country'] == ''
    inv_meta.loc[fb, 'country'] = inv_meta.loc[fb, 'inv_cur'].map(cur_cty).fillna('')
    items = items.merge(inv_meta[['item_id','issue_date','payment_terms','inv_cur','inv_net','country']],
                        on='item_id', how='left')

    # Merge BT info for text-based detection
    bt_text = bt[['bank_txn_id','text','amount','booking_date','currency','bank_code']].copy()
    bt_text = bt_text.rename(columns={'currency':'bt_cur','bank_code':'bt_code'})
    bt_text['bt_text_lower'] = bt_text['text'].fillna('').str.lower()
    bt_text['bt_abs'] = bt_text['amount'].abs()
    bt_text['bt_sign'] = np.sign(bt_text['amount'])
    items = items.merge(bt_text[['bank_txn_id','bt_text_lower','bt_abs','bt_sign','booking_date','bt_cur','bt_code']],
                        on='bank_txn_id', how='left')

    # Per-item early-payment discount (exact, from payment terms + 3-day grace)
    pct = items['payment_terms'].map({k: v[0] for k, v in DISCOUNT_TERMS.items()}).fillna(0.0)
    dd_ = items['payment_terms'].map({k: v[1] for k, v in DISCOUNT_TERMS.items()}).fillna(-1)
    days = (items['booking_date'] - items['issue_date']).dt.days
    elig = (pct > 0) & (days >= 0) & (days <= dd_ + DISCOUNT_GRACE_DAYS)
    items['disc_amt'] = np.where(elig, (items['inv_total'] * pct / 100.0).round(2), 0.0)
    items['wh_cty'] = items['country'].isin(WITHHOLDING_COUNTRIES)
    items['cur_ok'] = items['inv_cur'] == items['bt_cur']

    # Transaction-level aggregations
    txn_info = items.groupby('bank_txn_id').agg(
        sum_inv=('inv_total', 'sum'),
        sum_net=('inv_net', 'sum'),
        sum_disc=('disc_amt', 'sum'),
        bt_abs_first=('bt_abs', 'first'),
        bt_sign_first=('bt_sign', 'first'),
        text_first=('bt_text_lower', 'first'),
        code_first=('bt_code', 'first'),
        n_items=('item_id', 'nunique'),
        all_wh=('wh_cty', 'all'),
        all_cur_ok=('cur_ok', 'all'),
        n_cur=('inv_cur', 'nunique'),
        cur_first=('inv_cur', 'first'),
    ).reset_index()
    txn_info['ratio'] = txn_info['bt_abs_first'] / txn_info['sum_inv'].clip(lower=0.01)
    txn_info['short'] = txn_info['sum_inv'] - txn_info['bt_abs_first']
    txn_info['rtol'] = 0.01 * txn_info['n_items'] + 0.01

    counts = {}

    # --- RETURNED: text mentions return/reversal/ruecklastschrift ---
    returned_pat = r'devol|return|retorno|storno|anulacion|reversal|rebut|retourn|rueck(?:zahlung|lastschrift)|r[üu]cklast'
    mask_ret_txn = txn_info['text_first'].str.contains(returned_pat, regex=True, na=False)
    ret_txns = set(txn_info.loc[mask_ret_txn, 'bank_txn_id'])
    counts['RETURNED'] = len(ret_txns)

    not_ret = ~txn_info['bank_txn_id'].isin(ret_txns)
    base_ok = (txn_info['sum_inv'] > 0) & (txn_info['bt_abs_first'] > 0) & (txn_info['short'] > 0) & not_ret
    same_cur = txn_info['all_cur_ok']

    # --- SHORT_DISCOUNT (exact): shortfall == sum of terms-based discounts (D2N30/D1N60/D3N45/D2N30US) ---
    mask_sd_txn = (base_ok & same_cur & (txn_info['sum_disc'] > 0) &
                   ((txn_info['short'] - txn_info['sum_disc']).abs() <= txn_info['rtol']))
    if USE_RATIO_DISCOUNT_FALLBACK:
        mask_sd_txn |= (base_ok & (txn_info['ratio'] >= 0.97) & (txn_info['ratio'] <= 0.995))
    sd_txns = set(txn_info.loc[mask_sd_txn, 'bank_txn_id'])
    counts['SHORT_DISCOUNT'] = len(sd_txns)

    # --- SHORT_WITHHOLDING: only MX/BR/MA customers, rates 1.25% / 1.5% / 10% of invoice total ---
    mask_wh = pd.Series(False, index=txn_info.index)
    for rate in WITHHOLDING_RATES:
        for basis in ('sum_inv', 'sum_net'):
            mask_wh |= ((txn_info['short'] - rate * txn_info[basis]).abs() <= txn_info['rtol'])
    mask_wh &= base_ok & same_cur & txn_info['all_wh'] & ~txn_info['bank_txn_id'].isin(sd_txns)
    wh_txns = set(txn_info.loc[mask_wh, 'bank_txn_id'])
    counts['SHORT_WITHHOLDING'] = len(wh_txns)

    # --- SHORT_BANK_CHARGES: cross-border (non-SEPA), shortfall <= 60 EUR-equiv and <= 2% ---
    mask_bc = (base_ok & same_cur & (txn_info['n_items'] == 1) &
               (txn_info['short'] <= BANK_CHARGE_MAX_ABS) &
               (txn_info['short'] <= BANK_CHARGE_MAX_PCT * txn_info['sum_inv']) &
               (txn_info['cur_first'] != 'EUR') &
               txn_info['code_first'].isin(BANK_CHARGE_CODES) &
               ~txn_info['bank_txn_id'].isin(sd_txns) & ~txn_info['bank_txn_id'].isin(wh_txns))
    bc_txns = set(txn_info.loc[mask_bc, 'bank_txn_id'])
    counts['SHORT_BANK_CHARGES'] = len(bc_txns)

    # --- PARTIAL: BT < sum(inv) * 0.78 (below withholding range) ---
    mask_par_txn = ((txn_info['ratio'] > 0) & (txn_info['ratio'] < 0.78) &
                    (txn_info['sum_inv'] > 0) & (txn_info['bt_abs_first'] > 0) &
                    (~txn_info['bank_txn_id'].isin(ret_txns)))
    par_txns = set(txn_info.loc[mask_par_txn, 'bank_txn_id'])
    counts['PARTIAL'] = len(par_txns)

    # --- OVERPAYMENT: disabled (35K FPs for 95 TPs) ---
    op_txns = set()
    counts['OVERPAYMENT'] = 0

    # Apply labels (label on first pair per txn for each exception)
    keys_all = final['bank_txn_id'] + '|' + final['item_id']
    no_exc = final['exception'] == ''

    for label, txn_set in [('RETURNED', ret_txns), ('SHORT_DISCOUNT', sd_txns),
                            ('SHORT_WITHHOLDING', wh_txns), ('SHORT_BANK_CHARGES', bc_txns),
                            ('PARTIAL', par_txns),
                            ('OVERPAYMENT', op_txns)]:
        if txn_set:
            mask = final['bank_txn_id'].isin(txn_set) & no_exc & (final['item_id'] != '')
            final.loc[mask, 'exception'] = label
            no_exc = final['exception'] == ''

    parts = ', '.join(f"{k}={v:,}" for k, v in counts.items())
    log.info(f"  Post-process exceptions: {parts}")
    return final


# =====================================================================
# EVALUATE
# =====================================================================
def evaluate(result_file, golden_file):
    log.info("Evaluando...")
    res = pd.read_csv(result_file, dtype={'bank_txn_id':str,'item_id':str,'category':str,'exception':str})
    gld = pd.read_csv(golden_file, dtype={'bank_txn_id':str,'item_id':str,'category':str,'exception':str,'segment':str})

    # Allocation F1
    res_items = res[res['item_id'].notna() & (res['item_id'] != '') & (res['item_id'] != 'nan')]
    gld_items = gld[gld['item_id'].notna() & (gld['item_id'] != '') & (gld['item_id'] != 'nan')]

    res_pairs = set(res_items['bank_txn_id'] + '|' + res_items['item_id'])
    gld_pairs = set(gld_items['bank_txn_id'] + '|' + gld_items['item_id'])

    tp = len(res_pairs & gld_pairs)
    fp = len(res_pairs - gld_pairs)
    fn = len(gld_pairs - res_pairs)
    prec = tp/(tp+fp) if tp+fp else 0
    rec = tp/(tp+fn) if tp+fn else 0
    f1 = 2*prec*rec/(prec+rec) if prec+rec else 0

    # Transaction accuracy (item sets per txn)
    gld_by_txn = gld_items.groupby('bank_txn_id')['item_id'].apply(frozenset).to_dict()
    res_by_txn = res_items.groupby('bank_txn_id')['item_id'].apply(frozenset).to_dict()
    correct = sum(1 for t in gld_by_txn if t in res_by_txn and gld_by_txn[t] == res_by_txn[t])
    txn_acc = correct / len(gld_by_txn) if gld_by_txn else 0

    # Exception macro-F1 (vectorized)
    gld['exc_clean'] = gld['exception'].fillna('').astype(str).str.strip()
    gld_with_exc = gld[gld['exc_clean'].isin(['']) == False]
    gld_exc = {}
    for exc_type, grp in gld_with_exc.groupby('exc_clean'):
        if exc_type and exc_type != 'nan':
            gld_exc[exc_type] = set(grp['bank_txn_id'])

    res['exc_clean'] = res['exception'].fillna('').astype(str).str.strip()
    res_with_exc = res[res['exc_clean'].isin(['']) == False]
    res_exc = {}
    for exc_type, grp in res_with_exc.groupby('exc_clean'):
        if exc_type and exc_type != 'nan':
            res_exc[exc_type] = set(grp['bank_txn_id'])

    exc_f1s = {}
    for exc_type in sorted(gld_exc.keys()):
        r_s = res_exc.get(exc_type, set())
        g_s = gld_exc[exc_type]
        e_tp = len(r_s & g_s); e_fp = len(r_s - g_s); e_fn = len(g_s - r_s)
        e_p = e_tp/(e_tp+e_fp) if e_tp+e_fp else 0
        e_r = e_tp/(e_tp+e_fn) if e_tp+e_fn else 0
        e_f1 = 2*e_p*e_r/(e_p+e_r) if e_p+e_r else 0
        exc_f1s[exc_type] = (e_f1, e_tp, len(g_s))

    exc_mf1 = sum(v[0] for v in exc_f1s.values())/len(exc_f1s) if exc_f1s else 0

    score = 100 * (0.5*f1 + 0.3*txn_acc + 0.2*exc_mf1)

    # Recall by segment
    gld_items2 = gld_items.copy()
    gld_items2['pair'] = gld_items2['bank_txn_id'] + '|' + gld_items2['item_id']
    gld_items2['hit'] = gld_items2['pair'].isin(res_pairs)
    by_seg = gld_items2.groupby('segment')['hit'].agg(['sum','count']).reset_index()
    by_seg.columns = ['segment','hits','total']
    by_seg['recall'] = by_seg['hits'] / by_seg['total']
    by_seg = by_seg.sort_values('total', ascending=False)

    sys.stderr.write(f"\n{'='*65}\n")
    sys.stderr.write(f"  EVALUACION\n")
    sys.stderr.write(f"{'='*65}\n")
    sys.stderr.write(f"  Allocation F1:      {f1:.4f}  (P={prec:.4f} R={rec:.4f})\n")
    sys.stderr.write(f"    TP={tp:,}  FP={fp:,}  FN={fn:,}\n")
    sys.stderr.write(f"  Transaction Acc:    {txn_acc:.4f}  ({correct:,}/{len(gld_by_txn):,})\n")
    sys.stderr.write(f"  Exception macro-F1: {exc_mf1:.4f}\n")
    sys.stderr.write(f"  ---\n")
    sys.stderr.write(f"  SCORE:              {score:.1f} / 100\n")
    sys.stderr.write(f"{'='*65}\n\n")

    sys.stderr.write(f"  Excepciones:\n")
    for exc_type, (e_f1, e_tp, e_tot) in sorted(exc_f1s.items()):
        sys.stderr.write(f"    {exc_type:25s}  F1={e_f1:.3f}  ({e_tp}/{e_tot})\n")

    sys.stderr.write(f"\n  Recall por segmento (top 15):\n")
    for _, row in by_seg.head(15).iterrows():
        sys.stderr.write(f"    {row['segment']:30s}  {row['hits']:>7,.0f}/{row['total']:>7,.0f}  R={row['recall']:.3f}\n")

    sys.stderr.write(f"\n")
    return score


# =====================================================================
# MAIN
# =====================================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('data_dir', nargs='?', default='data/train')
    parser.add_argument('--output', '-o', default='output/result.csv')
    parser.add_argument('--evaluate', '-e', action='store_true')
    parser.add_argument('--golden', default=None)
    parser.add_argument('--post-only', action='store_true',
                        help='re-run only exception post-processing from output/pre_post.pkl')
    args = parser.parse_args()

    t_start = time.time()
    data_dir = args.data_dir

    log.info("="*60)
    log.info("PIPELINE v2")
    log.info("="*60)

    # Load
    bt, ar, ap, cn = load_all(data_dir)

    if args.post_only:
        pp = Path(args.output).parent / 'pre_post.pkl'
        final = pd.read_pickle(str(pp))
        final = postprocess_exceptions(final, bt, ar, ap)
        final.to_csv(args.output, index=False)
        if args.evaluate:
            evaluate(args.output, args.golden or str(Path(data_dir) / 'golden_allocations.csv.gz'))
        return

    log.info("Cargando detail files...")
    dd = load_detail(data_dir, 'direct_debit_batches')
    cs = load_detail(data_dir, 'card_settlements')
    pr = load_detail(data_dir, 'payment_runs')
    ch = load_detail(data_dir, 'cheque_deposits')
    rems = load_remittances(data_dir)

    matched = set()
    all_results = []

    def collect(result, new_matched):
        nonlocal matched
        if len(result): all_results.append(result)
        matched = new_matched

    # Step 1: DD Batches (biggest segment: 713K golden)
    log.info("\n--- STEP 1: DD Batches ---")
    r, m = step_dd(bt, ar, dd, matched)
    collect(r, m)

    # Step 2: Payment Runs (598K + 139K golden)
    log.info("\n--- STEP 2: Payment Runs ---")
    r, m = step_payment_runs(bt, ap, pr, matched)
    collect(r, m)

    # Step 3: Card Settlements (148K golden)
    log.info("\n--- STEP 3: Card Settlements ---")
    r, m = step_cards(bt, ar, cs, matched)
    collect(r, m)

    # Step 4: Cheque Deposits
    log.info("\n--- STEP 4: Cheque Deposits ---")
    r, m = step_cheques(bt, ar, ch, matched)
    collect(r, m)

    # Step 5: Remittance Advices (389K golden)
    log.info("\n--- STEP 5: Remittance Advices ---")
    r, m = step_remittances(bt, ar, rems, matched)
    collect(r, m)

    # Step 5b: Remittance-info doc lookup
    log.info("\n--- STEP 5b: Remittance-Info Docs ---")
    r, m = step_remittance_info_docs(bt, ar, ap, matched)
    collect(r, m)

    # Step 6: Invoice Regex
    log.info("\n--- STEP 6: Invoice Regex ---")
    r, m = step_invoice_regex(bt, ar, ap, matched)
    collect(r, m)

    # Step 6b: Suffix doc matching (partial doc numbers in text)
    log.info("\n--- STEP 6b: Suffix Doc Match ---")
    r, m = step_suffix_doc_match(bt, ar, matched)
    collect(r, m)

    # Step 6c: AP Multi-Invoice Text Match (FRAS pattern with multiple invoices)
    log.info("\n--- STEP 6c: AP Multi-Invoice Text ---")
    r, m = step_ap_multi_invoice_text(bt, ap, matched)
    collect(r, m)

    # Step 7: FX Match
    log.info("\n--- STEP 7: FX Match ---")
    r, m = step_fx_match(bt, ar, ap, matched)
    collect(r, m)

    # Step 7b: FX Text Match (invoice lookup in text + name extraction)
    log.info("\n--- STEP 7b: FX Text Match ---")
    r, m = step_fx_text_match(bt, ar, ap, matched)
    collect(r, m)

    # Step 8: Discount Match (SHORT_DISCOUNT)
    log.info("\n--- STEP 8: Discount Match ---")
    r, m = step_discount_match(bt, ar, ap, matched)
    collect(r, m)

    # Step 8b: Partial Match (BT amount < invoice total)
    # DISABLED: step_partial_match too many FP (280K found, 18K correct)
    # TODO: re-enable with much stricter filters or as post-processing
    # log.info("\n--- STEP 8b: Partial Match ---")
    # r, m = step_partial_match(bt, ar, ap, matched)
    # collect(r, m)

    # Step 9: Name-based 1:N Match AR
    log.info("\n--- STEP 9: Name 1:N AR ---")
    r, m = step_name_1toN(bt, ar, cn, matched)
    collect(r, m)

    # Step 9a2: Dispute Match (BT ≈ 90-100% of ALL customer invoices)
    log.info("\n--- STEP 9a2: AR Dispute Match ---")
    r, m = step_dispute_match(bt, ar, cn, matched)
    collect(r, m)

    # Step 9b: Name-based 1:N Match AP
    log.info("\n--- STEP 9b: Name 1:N AP ---")
    r, m = step_name_1toN_ap(bt, ap, cn, matched)
    collect(r, m)

    # Step 9c: Holding company (cross-entity parent→subsidiary)
    log.info("\n--- STEP 9c: Holding Company ---")
    r, m = step_holding_company(bt, ap, matched)
    collect(r, m)

    # Step 10: Direct 1:1 Match
    log.info("\n--- STEP 10: Direct 1:1 Match ---")
    r, m = step_direct_1to1(bt, ar, ap, matched)
    collect(r, m)

    # Step 11: Nonitem
    log.info("\n--- STEP 11: Nonitem ---")
    r = step_nonitem(bt, matched)
    if len(r): all_results.append(r)

    # Combine
    final = pd.concat(all_results, ignore_index=True) if all_results else pd.DataFrame(
        columns=['bank_txn_id','item_id','amount','category','exception'])

    final = final.drop_duplicates(subset=['bank_txn_id','item_id'], keep='first')
    final['item_id'] = final['item_id'].fillna('').astype(str).replace('nan','')
    final['category'] = final['category'].fillna('').astype(str).replace('nan','')
    final['exception'] = final['exception'].fillna('').astype(str).replace('nan','')
    final = final[['bank_txn_id','item_id','amount','category','exception']]

    # Cache pre-postprocess result so --post-only can iterate fast
    try:
        Path(args.output).parent.mkdir(exist_ok=True, parents=True)
        final.to_pickle(str(Path(args.output).parent / 'pre_post.pkl'))
    except Exception as e:
        log.warning(f"pre_post cache failed: {e}")

    # Post-process: label exceptions on matched pairs
    log.info("\n--- POST-PROCESS: Exception Labels ---")
    final = postprocess_exceptions(final, bt, ar, ap)

    # Save
    Path(args.output).parent.mkdir(exist_ok=True, parents=True)
    final.to_csv(args.output, index=False)

    elapsed = time.time() - t_start
    log.info(f"\nRESULTADO: {len(final):,} lineas, {final['bank_txn_id'].nunique():,} txns en {elapsed:.0f}s")
    log.info(f"Guardado en {args.output}")

    sys.stderr.write(f"\nDone: {len(final):,} lines in {args.output} ({elapsed:.0f}s)\n")

    if args.evaluate:
        golden = args.golden or str(Path(data_dir) / 'golden_allocations.csv.gz')
        evaluate(args.output, golden)


if __name__ == '__main__':
    main()
