"""Step 1 of the audit method: load source CSVs into the common schema.

Raw files are never modified. Output: one normalized CSV per table plus
normalization_log.csv, which lists every changed value and every problem found.
Blank stays blank: a missing value is never turned into zero.

An agency's own export rarely matches the schema. An optional mapping.json in
IN_DIR says, per table, which file to read, which source column feeds each
schema column, values fixed for every row, and the table's date format:

  {"time_entries": {"file": "Clockify report.csv", "date_format": "%m/%d/%Y",
                    "columns": {"project_id": "Project", "hours": "Duration"},
                    "fixed": {"source_system": "Clockify"}}}

Tables and columns it does not mention are read as <table>.csv and by schema name.
"file": null means the table was not supplied.

Usage: python -m src.normalize IN_DIR OUT_DIR [--date-format "%d/%m/%Y"]
"""
import argparse
import csv
import json
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
MAPPING = "mapping.json"


def schema_columns(table):
    with open(SCHEMAS / f"{table}.csv", encoding="utf-8") as f:
        return next(csv.reader(f))


def headers(in_dir):
    """Header row of each CSV file in in_dir, for matching an export's columns to the schema."""
    found = {}
    for p in sorted(Path(in_dir).iterdir()):
        if p.is_file() and p.suffix.lower() == ".csv":
            with open(p, encoding="utf-8-sig", newline="") as f:
                found[p.name] = next(csv.reader(f), [])
    return found


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

    def save(table, cols, rows):
        with open(out_dir / f"{table}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n")
            w.writeheader()
            w.writerows(rows)

    mapping = {}
    if (in_dir / MAPPING).exists():
        mapping = json.loads((in_dir / MAPPING).read_text(encoding="utf-8"))
        note("info", "", "", "", "", "", "", f"using {MAPPING}")
        for table, m in mapping.items():
            if table not in TABLES:
                note("error", table, "", "", "", "", "", f"{MAPPING} names a table not in the schema")
                continue
            for c in [*m.get("columns", {}), *m.get("fixed", {})]:
                if c not in schema_columns(table):
                    note("error", table, "", "", c, "", "", f"{MAPPING} names a column not in the schema")

    for table in TABLES:
        m = mapping.get(table, {})
        name = m.get("file", f"{table}.csv")
        if name is None or not (in_dir / name).exists():
            note("warning", table, "", "", "", name or "", "", "table not supplied")
            save(table, schema_columns(table), [])  # empty, so a re-run never reads an old file
            continue
        if name != f"{table}.csv":
            note("info", table, "", "", "", name, "", "read from this file")
        cols, fixed = schema_columns(table), {c: str(v) for c, v in m.get("fixed", {}).items()}
        source = {c: m.get("columns", {}).get(c, c) for c in cols if c not in fixed}
        fmt = m.get("date_format", date_format)
        with open(in_dir / name, encoding="utf-8-sig", newline="") as f:  # -sig: Excel adds a BOM
            reader = csv.DictReader(f)
            given, rows = reader.fieldnames or [], list(reader)
        for c in cols:
            if c in fixed:
                note("info", table, 1, "", c, "", fixed[c], "fixed value for every row")
            elif source[c] not in given:
                note("error", table, 1, "", c, "" if source[c] == c else source[c], "", "column missing from source")
            elif source[c] != c:
                note("info", table, 1, "", c, source[c], "", "read from this column")
        for c in given:
            if c not in source.values():
                note("info", table, 1, "", c, "", "", "column not in schema; dropped")

        key, seen, out = cols[0], set(), []
        for line, row in enumerate(rows, start=2):  # line 1 is the header
            rec, rid = {}, (row.get(source.get(key, key)) or "").strip()
            for c in cols:
                original = fixed[c] if c in fixed else row.get(source[c]) or ""
                rec[c], problem = clean(c, original, fmt)
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
        save(table, cols, out)

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
