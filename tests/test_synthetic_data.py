"""The synthetic dataset must match the schema, hang together, and contain exactly
the problems its answer key claims. Later engine tests rely on this."""
import csv
import importlib.util
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "synthetic"
SCHEMAS = ROOT / "schemas"


def read(name, folder=DATA):
    with open(folder / f"{name}.csv", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def header(path):
    with open(path, encoding="utf-8") as f:
        return next(csv.reader(f))


def test_tables_match_schema():
    for schema in SCHEMAS.glob("*.csv"):
        if schema.stem == "audit_findings":  # engine output, not input
            continue
        assert header(DATA / schema.name) == header(schema), schema.stem


def test_references_resolve():
    projects = {p["project_id"] for p in read("projects")}
    docs = {d["document_id"] for d in read("evidence_documents")}
    scope = {s["scope_item_id"] for s in read("scope_items")}
    for table in ("time_entries", "financial_transactions", "scope_items", "evidence_documents"):
        assert {r["project_id"] for r in read(table)} <= projects, table
    assert {t["source_document_id"] for t in read("financial_transactions")} <= docs
    assert {s["source_document_id"] for s in read("scope_items")} - {""} <= docs
    assert {t["task_id"] for t in read("time_entries") if t["task_id"].startswith("SI-")} <= scope


def hours_by_project():
    total = defaultdict(float)
    for t in read("time_entries"):
        total[t["project_id"]] += float(t["hours"])
    return total


def test_planted_labour_overruns():
    est = {p["project_id"]: float(p["estimated_labor_hours"]) for p in read("projects")}
    actual = hours_by_project()
    assert (est["P-AG01-02"], actual["P-AG01-02"]) == (400, 520)   # K02: +30%
    assert (est["P-AG02-03"], actual["P-AG02-03"]) == (500, 550)   # K11: +10%, below threshold
    over = {p for p in est if actual[p] > est[p] * 1.15}
    assert over == {"P-AG01-02"}  # no accidental overruns elsewhere


def test_planted_missing_cost_rate():
    blank = [t for t in read("time_entries") if t["hourly_cost_rate"] == ""]
    assert {t["project_id"] for t in blank} == {"P-AG01-02"}       # K03
    assert sum(float(t["hours"]) for t in blank) == 30
    assert all(t["cost_amount"] == "" for t in blank)              # unknown, not zero


def test_planted_scope_changes():
    unapproved = [s for s in read("scope_items")
                  if s["included_in_original_scope"] == "false" and s["change_approval_status"] != "approved"]
    assert [s["scope_item_id"] for s in unapproved] == ["SI-AG01-03-04"]  # K04/K05
    cr_hours = sum(float(t["hours"]) for t in read("time_entries") if t["task_id"] == "SI-AG01-03-04")
    assert cr_hours == 60
    billed = [t for t in read("financial_transactions") if t["invoice_id"] == "INV-1103" and t["transaction_type"] == "invoice"]
    assert float(billed[0]["amount"]) == 6000                      # K08: approved change was invoiced


def test_planted_duplicate_expense():
    seen, dups = defaultdict(list), []
    for t in read("financial_transactions"):
        if t["transaction_type"].startswith("expense"):
            key = (t["project_id"], t["vendor_or_customer_ref"], t["amount"], t["currency"])
            d = date.fromisoformat(t["transaction_date"])
            dups += [key for other in seen[key] if abs((d - other).days) <= 3]
            seen[key].append(d)
    assert dups == [("P-AG02-01", "V-TOOLS-01", "3500", "AED")]   # K07


def test_planted_payment_gaps():
    gap = defaultdict(float)  # invoiced - received - documented deductions, per invoice
    for t in read("financial_transactions"):
        sign = {"invoice": 1, "payment": -1, "bank_fee": -1}.get(t["transaction_type"], 0)
        if sign:
            gap[t["invoice_id"]] += sign * float(t["amount"])
    open_invoices = {"INV-1401"}  # belongs to the excluded open project
    short = {inv: g for inv, g in gap.items() if g and inv not in open_invoices}
    assert short == {"INV-1302": 8000}                              # K10 flagged, K06 reconciles


def test_planted_subcontractor_costs():
    planned = {p["project_id"]: float(p["estimated_subcontractor_cost"]) for p in read("projects")}
    actual = defaultdict(float)  # in AED, the currency of every project that subcontracts
    for t in read("financial_transactions"):
        if t["transaction_type"] == "expense_subcontractor":
            actual[t["project_id"]] += float(t["amount"]) * (3.6725 if t["currency"] == "USD" else 1)
    assert planned["P-AG02-02"] == 0 and actual["P-AG02-02"] == 29380        # K09: none planned
    assert planned["P-AG02-01"] == 10000 and 9000 < actual["P-AG02-01"] < 10000  # K13: within plan
    assert set(actual) == {"P-AG02-01", "P-AG02-02"}


def test_only_open_project_is_excluded():
    open_ = [p["project_id"] for p in read("projects") if p["project_status"] != "completed"]
    assert open_ == ["P-AG02-04"]                                   # K12


def test_generator_reproduces_committed_files(tmp_path):
    spec = importlib.util.spec_from_file_location("generate", DATA / "generate.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    gen.write(tmp_path)
    for f in tmp_path.glob("*.csv"):
        assert f.read_bytes() == (DATA / f.name).read_bytes(), f.name
