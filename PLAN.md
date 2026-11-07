# Team plan: bank reconciliation hackathon (Sat 7 Nov 2026)

Challenge site: https://hackathon.usetalky.com · Docs: `bankrec-docs-and-masters/` (README_EN, POLICIES, DATA_DICTIONARY, FORMAT).
**Read POLICIES.md and FORMAT.md first**: the labels follow those rules exactly.

## 1. The task in one paragraph

For every bank line, say which open items (invoices ARI/API, credit notes ARC/APC) it settles and for how much. If it settles no item, give a `category` (fee, payroll, tax, cash pool...). Add an `exception` when the cash differs or something is special (PARTIAL, SHORT_DISCOUNT, FX_DIFFERENCE, RETURNED, UNIDENTIFIED...). Output: one CSV row per allocation: `bank_txn_id,item_id,amount,category,exception`.

Score = 100 × (0.5 · allocation F1 + 0.3 · transaction accuracy + 0.2 · exception macro-F1). Amount tolerance: max(0.01, 0.5 % of golden).
Baselines: naive ≈ 19, well-tuned rules ≈ 61, perfect = 100.

## 2. Key facts and rules

| Item | Detail |
|---|---|
| Data | TRAIN 2021-07→2025-06 (1.49M lines, **with golden**), LIVE 2025-07→12 (203k lines, 16.7k PDFs, no golden), FINAL 2026-01→06 (220k lines) |
| Schedule | 10:00 hacking + LIVE open · 14:00 lunch · **18:00 FINAL data released** · **19:30 submissions close** · **19:45 project submission** (video + repo + write-up) · 20:30 reveal |
| LIVE | 1 submission/min, 100/day, scored within minutes, **best score counts**. Use it as the real test loop |
| FINAL | **One submission only: the first valid file counts, no retry.** A file rejected by validation does not count (fix and re-upload). Score hidden until reveal |
| File | `.csv` or `.csv.gz`, ≤ 60 MB, ≤ 3M rows, every bank line of the phase must appear |
| Upload | `https://hackathon.usetalky.com/api`, token in env var `HK_TOKEN` (never in code, repo or chat). Poll at most every 5 s (whole room shares one IP). Requesting an upload URL already uses a slot |
| Allowed | Any tool, language or model, including Claude. Golden exists only for TRAIN: do not try to get LIVE/FINAL answers any other way |
| Drift | Bank migrates Norma 43 → camt.053 (Oct 2025), a big customer changes remittance layout, a subsidiary is acquired in FINAL (South Africa, ZAR), customers renamed. **Never hard-code entity/bank lists; read them from the data** |

Modelling rules worth remembering:
- Bank text carries the **printed document number** (often truncated at 35 chars or misspelled), never the `item_id`. Payer name + exact amount is often enough.
- Short payment for an accepted reason (discount, charges, withholding): item is **fully cleared** (`amount` = open amount) and `exception` explains the gap. A genuine partial: `amount` = what was paid, `PARTIAL`.
- `amount` is in the item currency, positive, 2 decimals. `category` only on lines with no item.
- Do not force a match: if nothing fits, flag `UNIDENTIFIED`.

## 3. What we are building

One Python pipeline: `python run.py --phase train|live|final`, producing a validated submission file.

1. **Loader + ledger**: load all tables and masters; compute which items are open at each bank line's date (issue dates, credit notes, earlier allocations).
2. **Party resolution**: map bank text to a customer/supplier (truncated name, IBAN).
3. **Structured batches** (deterministic, high value): payment runs, direct-debit batches, card settlements, CSV/XLSX remittance advices, returns.
4. **Solver per line** over the party's open items: printed number + amount, exact amount, FIFO, subset-sum for 1:N, credit-note netting, tolerance cases (discount / charges / withholding / partial), FX via `fx_rates`.
5. **No-item lines**: text classifier trained on TRAIN golden (TF-IDF + logistic regression or LightGBM) for `category` and `exception`; plus UNIDENTIFIED, duplicates, overpayments.
6. **Claude layer (residual cases only)**: PDF-only context ("instalment 2 of 3", "cancels and replaces", "5 % retention") and ambiguous cases. Haiku/Sonnet for bulk, parallel calls, **cache every result on disk**. Pre-compute for LIVE; budget for FINAL.
7. **Merger + validator**: highest-confidence result per bank line; item rows and category row are mutually exclusive; enforce FORMAT.md rules; `submit.py` wraps the site's upload script.

**Shared contract (agree first):** every module returns rows in the submission schema plus `confidence` and `source`, keyed by `bank_txn_id`. The merger picks per line.

## 4. Split of work

| Person | Owns |
|---|---|
| **A: Infra & evaluation** | Loader, open-item ledger, merger, validator, TRAIN scoring harness (`starter/bankrec_score.py`), `submit.py`, FINAL runbook, drift checks, timing |
| **B: Matching core** | Party resolution, candidate generation, solver for 1:1, 1:N, N:1, short payments, credit notes, FX |
| **C: Documents & batches** | Remittance advices, payment runs, direct debits, card settlements, returns, PDF extraction and Claude calls |
| **D: No-item lines & deliverables** | Category/exception classifier, UNIDENTIFIED/duplicates/overpayments; then video, README, write-up. `cuadra/` is optional (error inspection / demo) |

Names: A = ____ · B = ____ · C = ____ · D = ____

## 5. Timeline

- **~11:30**: contract agreed, loader + scorer working; submit the starter baseline to LIVE once to confirm token and upload flow.
- **~13:30**: first rules-based version on LIVE (target 50-60).
- **14:00-16:30**: add Claude layer; iterate using TRAIN error analysis + LIVE scores.
- **16:30**: feature freeze.
- **17:00**: timed full dry run on LIVE (check ≤ 60 MB, ≤ 3M rows, runtime).
- **18:00-19:00**: run on FINAL, validate locally, **submit well before 19:30**.
- **Before 19:45**: video, repo, write-up submitted on the Project page.

## 6. Housekeeping

- Datasets (`train/`, `train 2/`, `live/`, `*.tar.gz`, `*.zip`) must **not** be committed (about 3 GB). Add them to `.gitignore`.
- API key: env var `ANTHROPIC_API_KEY`. Team token: `HK_TOKEN`. Neither goes in the repo.
- Existing code: `cuadra/` is a browser review app (React + DuckDB-WASM) with a local scorer; it has **no API integration** and its scorer/export are assumptions to check against the official `bankrec_score.py` and FORMAT.md.
- Run locally first: `python starter/starter.py --phase sample` then `python starter/score_train.py sub_sample.csv.gz --sample`.

## 7. Open questions

- How are the Anthropic API credits issued to the team? (not yet seen on the site)
- Does FINAL ship its own `open_items_at_start`, or must balances carry over from LIVE? Check when the data drops; the ledger must work either way.
