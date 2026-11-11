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
python -m src.generate_report data/normalized/AG01 reports/AG01
```

Analyst tool (browser): `python -m http.server 8765 --bind 127.0.0.1`, then open
http://127.0.0.1:8765/app/. It runs the same `src/` code unchanged inside Pyodide,
so files are processed in the browser, not by the server.

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

`generate_report` runs the rules (which run reconciliation) and writes one draft
HTML report per agency: summary by impact type, planned vs actual profit, each
finding in the protocol's 10-part format with a verdict box, recurring
patterns, data gaps and method. Open it in a browser; print to PDF to share.

## Rules

- **This repository is public. Real agency data never goes into it.** Keep it in
  `data/raw/` and `data/normalized/`, which are ignored by git.
- All financial arithmetic is deterministic and tested.
- The synthetic answer key is the target: the engine should flag every `flag`
  case, stay silent on every `no_flag` case, and exclude every `exclude` case.
