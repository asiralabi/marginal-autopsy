"""Step 1 of the audit method: load source CSVs into the common schema.

Raw files are never modified. Output: one normalized CSV per table plus
normalization_log.csv, which lists every changed value and every problem found.
Blank stays blank: a missing value is never turned into zero.

Usage: python -m src.normalize IN_DIR OUT_DIR [--date-format "%d/%m/%Y"]
"""
import argparse
import csv
import re
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"
# Order matters: projects must load before the tables that reference them.
TABLES = ["agencies", "projects", "scope_items", "time_entries", "financial_transactions", "evidence_documents"]
DATES = {"start_date", "end_date", "date", "transaction_date", "source_date", "change_request_date"}
NUMBERS = {"contract_value", "estimated_labor_hours", "estimated_labor_cost", "estimated_other_cost",
           "estimated_subcontractor_cost", "planned_margin", "actual_revenue", "actual_cost", "hours", "hourly_cost_rate", "cost_amount",
           "amount", "fx_rate_to_usd", "agreed_price_change"}
BOOLEANS = {"included_in_original_scope", "is_estimated"}
TRUE, FALSE = {"true", "yes", "y", "1"}, {"false", "no", "n", "0"}
LOG_FIELDS = ["level", "table", "line", "record_id", "column", "original", "normalized", "note"]


def schema_columns(table):
    with open(SCHEMAS / f"{table}.csv", encoding="utf-8") as f:
        return next(csv.reader(f))


def clean(column, raw, date_format=None):
    """Return (normalized value, problem). On a problem the value is left blank."""
    v = raw.strip()
    if v == "":
        return "", None
    if column in DATES:
        for fmt in filter(None, ("%Y-%m-%d", date_format)):
            try:
                return datetime.strptime(v, fmt).date().isoformat(), None
            except ValueError:
                pass
        return "", f"unreadable date (accepted: YYYY-MM-DD{', ' + date_format if date_format else ''})"
    if column in NUMBERS:
        n = v.replace(",", "").replace(" ", "")
        try:
            ok = Decimal(n).is_finite()
        except InvalidOperation:
            ok = False
        return (n, None) if ok else ("", "not a number")
    if column in BOOLEANS:
        b = v.lower()
        return ("true", None) if b in TRUE else ("false", None) if b in FALSE else ("", "not true/false")
    if column == "currency":
        c = v.upper()
        return (c, None) if re.fullmatch(r"[A-Z]{3}", c) else ("", "not a 3-letter currency code")
    return v, None


def normalize(in_dir, out_dir, date_format=None):
    in_dir, out_dir = Path(in_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    log, project_ids = [], set()

    def note(level, table, line, rid, column, original, normalized, msg):
        log.append(dict(zip(LOG_FIELDS, [level, table, line, rid, column, original, normalized, msg])))

    for table in TABLES:
        src = in_dir / f"{table}.csv"
        if not src.exists():
            note("warning", table, "", "", "", "", "", "table not supplied")
            continue
        cols = schema_columns(table)
        with open(src, encoding="utf-8-sig", newline="") as f:  # -sig: Excel adds a BOM
            reader = csv.DictReader(f)
            given, rows = reader.fieldnames or [], list(reader)
        for c in cols:
            if c not in given:
                note("error", table, 1, "", c, "", "", "column missing from source")
        for c in given:
            if c not in cols:
                note("info", table, 1, "", c, "", "", "column not in schema; dropped")

        key, seen, out = cols[0], set(), []
        for line, row in enumerate(rows, start=2):  # line 1 is the header
            rec, rid = {}, (row.get(key) or "").strip()
            for c in cols:
                original = row.get(c) or ""
                rec[c], problem = clean(c, original, date_format)
                if problem:
                    note("warning", table, line, rid, c, original, "", problem)
                elif rec[c] != original:
                    note("info", table, line, rid, c, original, rec[c], "reformatted")
            if not rid:
                note("error", table, line, "", key, "", "", "missing record id")
            elif rid in seen:
                note("error", table, line, rid, key, rid, "", "duplicate record id")
            seen.add(rid)
            if table == "projects":
                project_ids.add(rid)
            elif "project_id" in rec and rec["project_id"] not in project_ids:
                note("warning", table, line, rid, "project_id", rec["project_id"], "", "unknown project")
            if table == "financial_transactions" and rec["currency"] not in ("", "USD") and not rec["fx_rate_to_usd"]:
                note("warning", table, line, rid, "fx_rate_to_usd", "", "", "no FX rate; amount cannot be converted to USD")
            out.append(rec)

        with open(out_dir / f"{table}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n")
            w.writeheader()
            w.writerows(out)

    with open(out_dir / "normalization_log.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(log)
    return log


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Normalize source CSVs into the common schema.")
    p.add_argument("in_dir")
    p.add_argument("out_dir")
    p.add_argument("--date-format", help='one extra accepted date format, e.g. "%%d/%%m/%%Y"')
    a = p.parse_args()
    counts = Counter(entry["level"] for entry in normalize(a.in_dir, a.out_dir, a.date_format))
    print(f"errors: {counts['error']}  warnings: {counts['warning']}  changes: {counts['info']}"
          f"  -> {Path(a.out_dir) / 'normalization_log.csv'}")
