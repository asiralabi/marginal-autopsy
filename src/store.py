"""Audit store: a local Postgres database holding every audit run and the verdict ledger.
It stores what the engine wrote to an agency folder and never calculates money.
The tables hold pseudonymous project records, so DATABASE_URL must point at a database
on the analyst's machine or a self-hosted edition, never at the cloud (playbook §23).
Usage: python -m src.store migrate
       python -m src.store load AGENCY_DIR [--generated-on YYYY-MM-DD]
       python -m src.store delete-agency AGENCY_ID
DATABASE_URL defaults to the container the README starts.
"""
import argparse
import csv
import json
import os
import subprocess
from datetime import date
from decimal import Decimal
from pathlib import Path

import psycopg2
from psycopg2.extras import Json, execute_values

from src.detect_variances import load_rules
from src.normalize import MAPPING
from src.review import FIELDS as VERDICT_FIELDS, load as load_verdicts

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "db" / "migrations"
DEFAULT_URL = "postgresql://postgres:ma@127.0.0.1:5433/margin_autopsy"

# Columns that are not text, per table. Everything else is stored as it was written.
NUMERIC = {
    "projects": {"contract_value", "estimated_labor_hours", "estimated_labor_cost", "estimated_other_cost",
                 "estimated_subcontractor_cost", "planned_margin", "actual_revenue", "actual_cost"},
    "scope_items": {"agreed_price_change"},
    "time_entries": {"hours", "hourly_cost_rate", "cost_amount"},
    "financial_transactions": {"amount", "fx_rate_to_usd"},
    "baseline": {"contract_value", "planned_cost", "planned_profit", "planned_margin_pct", "invoiced_revenue",
                 "cash_received", "labour_hours", "labour_hours_without_cost", "labour_cost", "subcontractor_cost",
                 "other_cost", "actual_cost", "actual_profit", "actual_margin_pct", "profit_variance"},
    "findings": {"baseline_amount", "observed_amount", "estimated_impact"},
}
DATES = {
    "projects": {"start_date", "end_date"}, "scope_items": {"change_request_date"}, "time_entries": {"date"},
    "financial_transactions": {"transaction_date"}, "evidence_documents": {"source_date"},
}
BOOLEANS = {"scope_items": {"included_in_original_scope"}, "financial_transactions": {"is_estimated"},
            "baseline": {"included", "data_complete"}}
INTEGERS = {"findings": {"rule_version"}, "normalization_log": {"line"}}
# Verdict columns in findings.csv are placeholders; the ledger holds the real ones.
DROPPED = {"findings": {"customer_verdict", "actionability", "reviewer_id"}}
RENAMED = {"normalization_log": {"table": "table_name", "column": "column_name"}}
SNAPSHOT_TABLES = ["projects", "scope_items", "time_entries", "financial_transactions", "evidence_documents",
                   "baseline", "findings", "normalization_log"]


def connect(url=None):
    return psycopg2.connect(url or os.environ.get("DATABASE_URL", DEFAULT_URL))


def migrate(conn):
    """Apply the SQL files in db/migrations that have not been applied yet. Returns their names."""
    applied = []
    with conn, conn.cursor() as cur:
        cur.execute("create schema if not exists audit")
        cur.execute("create table if not exists audit.schema_migrations "
                    "(version text primary key, applied_at timestamptz not null default now())")
        cur.execute("select version from audit.schema_migrations")
        done = {v for (v,) in cur.fetchall()}
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name not in done:
                cur.execute(path.read_text(encoding="utf-8"))
                cur.execute("insert into audit.schema_migrations (version) values (%s)", (path.name,))
                applied.append(path.name)
    return applied


def read(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def convert(table, column, value):
    if value == "":
        return None
    if column in NUMERIC.get(table, ()):
        return Decimal(value)
    if column in DATES.get(table, ()):
        return date.fromisoformat(value)
    if column in BOOLEANS.get(table, ()):
        return value == "true"
    if column in INTEGERS.get(table, ()):
        return int(value)
    if table == "findings" and column == "evidence_ids":
        return [e for e in value.split(";") if e]
    return value


def engine_commit():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def load(conn, folder, generated_on=None):
    """Store the results in an agency folder as a new run. Returns the run id and row counts.
    The folder must hold one agency's files and must have been audited (normalized/findings.csv)."""
    folder = Path(folder)
    normalized = folder / "normalized"
    if not (normalized / "findings.csv").exists():
        raise ValueError("Run the audit on this folder before loading it.")
    agencies = read(normalized / "agencies.csv")
    if len(agencies) != 1:
        raise ValueError(f"The store takes one agency folder at a time; this folder has {len(agencies)} agencies.")
    agency = agencies[0]
    mapping_file = folder / MAPPING
    mapping = json.loads(mapping_file.read_text(encoding="utf-8")) if mapping_file.exists() else None
    rule_versions = {rule: spec["version"] for rule, spec in load_rules().items()}
    counts = {}
    with conn, conn.cursor() as cur:
        cur.execute("""insert into audit.agencies (agency_id, region, industry, employee_band, project_type, currency,
                       billing_model, costing_method, data_permission_status)
                       values (%(agency_id)s, %(region)s, %(industry)s, %(employee_band)s, %(project_type)s,
                               %(currency)s, %(billing_model)s, %(costing_method)s, %(data_permission_status)s)
                       on conflict (agency_id) do update set
                         region = excluded.region, industry = excluded.industry, employee_band = excluded.employee_band,
                         project_type = excluded.project_type, currency = excluded.currency,
                         billing_model = excluded.billing_model, costing_method = excluded.costing_method,
                         data_permission_status = excluded.data_permission_status""",
                    {k: agency.get(k) or None for k in ("agency_id", "region", "industry", "employee_band",
                                                        "project_type", "currency", "billing_model",
                                                        "costing_method", "data_permission_status")})
        cur.execute("""insert into audit.runs (agency_id, generated_on, source_folder, mapping, rule_versions, engine_commit)
                       values (%s, %s, %s, %s, %s, %s) returning run_id""",
                    (agency["agency_id"], generated_on, str(folder), Json(mapping) if mapping else None,
                     Json(rule_versions), engine_commit()))
        run_id = cur.fetchone()[0]
        for table in SNAPSHOT_TABLES:
            rows = read(normalized / f"{table}.csv")
            counts[table] = len(rows)
            if not rows:
                continue
            columns = [c for c in rows[0] if c not in DROPPED.get(table, ())]
            names = ["run_id"] + [RENAMED.get(table, {}).get(c, c) for c in columns]
            if table == "normalization_log":
                names.append("seq")
            values = [[run_id] + [convert(table, c, r[c]) for c in columns] + ([i] if table == "normalization_log" else [])
                      for i, r in enumerate(rows, 1)]
            execute_values(cur, f"insert into audit.{table} ({', '.join(names)}) values %s", values)
        counts["verdict_events"] = record_verdicts(cur, agency["agency_id"], load_verdicts(folder))
    return dict(run_id=run_id, agency_id=agency["agency_id"], **counts)


def record_verdicts(cur, agency_id, verdicts):
    """Append a ledger event for every verdict row that differs from the current one."""
    fields = VERDICT_FIELDS[1:]  # everything after finding_id
    cur.execute(f"select finding_id, {', '.join(fields)} from audit.verdicts_current where agency_id = %s", (agency_id,))
    current = {row[0]: row[1:] for row in cur.fetchall()}
    added = 0
    for finding_id, row in verdicts.items():
        values = tuple(convert_verdict(f, row.get(f, "")) for f in fields)
        if current.get(finding_id) == values:
            continue
        cur.execute(f"insert into audit.verdict_events (agency_id, finding_id, {', '.join(fields)}) "
                    f"values ({', '.join(['%s'] * (len(fields) + 2))})", (agency_id, finding_id) + values)
        added += 1
    return added


def convert_verdict(field, value):
    if field == "agreed_amount":
        return Decimal(value) if value else None
    if field == "reviewed_on":
        return date.fromisoformat(value) if value else None
    return value or ("unreviewed" if field == "customer_verdict" else "")


def delete_agency(conn, agency_id):
    """Remove an agency and every run, row and verdict that belongs to it. Returns 1 if it existed."""
    with conn, conn.cursor() as cur:
        cur.execute("delete from audit.agencies where agency_id = %s", (agency_id,))
        return cur.rowcount


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--database-url", help="defaults to $DATABASE_URL, then the README's container")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate")
    load_p = sub.add_parser("load")
    load_p.add_argument("folder")
    load_p.add_argument("--generated-on", type=date.fromisoformat)
    del_p = sub.add_parser("delete-agency")
    del_p.add_argument("agency_id")
    a = p.parse_args()
    conn = connect(a.database_url)  # each function opens its own transaction; psycopg2 won't nest them
    try:
        if a.command == "migrate":
            applied = migrate(conn)
            print(f"applied {len(applied)} migration(s)" + (": " + ", ".join(applied) if applied else ""))
        elif a.command == "load":
            r = load(conn, a.folder, a.generated_on)
            print(f"run {r['run_id']} for {r['agency_id']}: " +
                  ", ".join(f"{k} {v}" for k, v in r.items() if k not in ("run_id", "agency_id")))
        else:
            print(f"deleted {delete_agency(conn, a.agency_id)} agency")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
