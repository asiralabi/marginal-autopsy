# Databases

Two Postgres databases, one rule between them: **the cloud never holds a project
record.** Playbook §23 promises "your raw financial and project data never reaches our
servers", and §24.2 limits the cloud to accounts, billing, rule updates, report
templates and encrypted backups.

| | Audit store | Cloud |
|---|---|---|
| Where | Postgres on the analyst's machine (Docker); later the self-hosted edition | Supabase project `margin-autopsy` (ap-south-1) |
| Holds | Every audit run as a snapshot, the verdict ledger | Audit requests from the website; after the pilot gate: accounts, subscriptions, rule packs, report templates, encrypted backups, usage counts |
| Never | Anything but pseudonymous IDs | Projects, scope items, time entries, transactions, invoices, evidence |
| Migrations | `db/migrations/*.sql`, applied by `python -m src.store migrate` | `website/supabase/migrations/*.sql`, applied to the Supabase project |

## Audit store

The engine stays CSV in, CSV out, deterministic and tested. The store loads what the
engine wrote to an agency folder; it never calculates money.

```
docker run -d --name ma-pg -e POSTGRES_PASSWORD=ma -e POSTGRES_DB=margin_autopsy -p 127.0.0.1:5433:5432 postgres:17
pip install psycopg2-binary
python -m src.store migrate
python -m src.store load D:/path/to/agency_folder --generated-on 2026-11-12
python -m src.store delete-agency AG01
```

`DATABASE_URL` overrides the container's address. Keep it pointed at a database on
this machine.

What a `load` writes, all in one transaction:

- `audit.agencies` — upserted from `normalized/agencies.csv` (one agency per folder).
- `audit.runs` — one row per load: when, which folder, the `mapping.json` in force,
  the rule versions from `rules.toml`, the engine's git commit.
- Snapshot tables keyed by `(run_id, id)`: `projects`, `scope_items`, `time_entries`,
  `financial_transactions`, `evidence_documents`, `baseline`, `findings`,
  `normalization_log`. Blank values are NULL, money is `numeric`, dates are dates,
  `evidence_ids` is an array that joins to `evidence_documents`.
- `audit.verdict_events` — the ledger. A row is appended only when a finding's
  verdict row in `verdicts.csv` differs from the latest event, so loading twice
  changes nothing and the history of every decision is kept with reviewer and date.

Views: `verdicts_current` (latest event per finding), `latest_runs` (one per agency),
`findings_current` (latest run's findings with their current verdict). They join;
they do not sum. Confirmed totals come from `src/review.py`.

`delete-agency` removes the agency row and, by cascade, every run, snapshot row and
verdict event that belongs to it: the one-command deletion the data-handling
agreement promises.

Tests: `python -m pytest tests/test_store.py` runs against `DATABASE_URL` and drops
and recreates the `audit` schema there. It skips when no Postgres is reachable.
