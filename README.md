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
```

`normalize` writes the cleaned tables plus `normalization_log.csv` (every changed
value and every problem). Raw files are never modified.

## Rules

- **This repository is public. Real agency data never goes into it.** Keep it in
  `data/raw/` and `data/normalized/`, which are ignored by git.
- All financial arithmetic is deterministic and tested.
- The synthetic answer key is the target: the engine should flag every `flag`
  case, stay silent on every `no_flag` case, and exclude every `exclude` case.
