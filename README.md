# Margin Autopsy — audit engine (experiment)

Deterministic audit engine for completed software-agency projects: compare what
was promised, performed, spent, and billed, and flag evidence-backed
discrepancies for human review.

```
schemas/           common data schema (CSV headers)
data/synthetic/    fictional dataset with planted problems + answer_key.csv
data/raw/          real agency exports        (git-ignored)
data/normalized/   cleaned real data          (git-ignored)
reports/           generated audit reports    (git-ignored)
src/               engine code
tests/             pytest suite
```

## Run

```
pip install -r requirements.txt
python -m pytest
python data/synthetic/generate.py   # rebuild the synthetic CSVs
python -m src.normalize data/raw/AG01 data/normalized/AG01 --date-format "%d/%m/%Y"
python -m src.reconcile data/normalized/AG01
python -m src.detect_variances data/normalized/AG01
```

`normalize` writes the cleaned tables plus `normalization_log.csv` (every changed
value and every problem). Raw files are never modified.

`reconcile` writes `baseline.csv`: planned vs actual cost, profit, margin and
variance per project, in the project currency. Revenue is the invoiced amount;
cash received is a separate column. Costs that cannot be known are left out and
flagged (`data_complete = false`), never guessed.

`detect_variances` writes `findings.csv` in the `audit_findings` schema. Rules F1,
F2, F3, F5, F6, F7 and F8 are implemented; thresholds and wording live in
`src/rules.toml`, and each finding records the rule version that produced it.
Every finding starts as `unreviewed` until an analyst and the agency check it.

## Rules

- **This repository is public. Real agency data never goes into it.** Keep it in
  `data/raw/` and `data/normalized/`, which are ignored by git.
- All financial arithmetic is deterministic and tested.
- The synthetic answer key is the target: the engine should flag every `flag`
  case, stay silent on every `no_flag` case, and exclude every `exclude` case.
