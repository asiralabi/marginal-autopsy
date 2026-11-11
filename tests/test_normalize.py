import csv
import json
import shutil
from pathlib import Path

from src.detect_variances import detect
from src.normalize import TABLES, headers, normalize

SYNTHETIC = Path(__file__).resolve().parents[1] / "data" / "synthetic"


def read(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write(path, rows, fields):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def test_clean_synthetic_data_passes_through_unchanged(tmp_path):
    log = normalize(SYNTHETIC, tmp_path)
    assert log == []
    for table in TABLES:
        assert (tmp_path / f"{table}.csv").read_bytes() == (SYNTHETIC / f"{table}.csv").read_bytes(), table


def test_messy_input_is_fixed_or_logged(tmp_path):
    raw, out = tmp_path / "raw", tmp_path / "out"
    shutil.copytree(SYNTHETIC, raw)

    te = read(raw / "time_entries.csv")
    fields = list(te[0]) + ["internal_note"]                 # extra column
    te[0].update(date="12/01/2026", hours="1,040", internal_note="x")  # DD/MM date, thousands separator
    te[1]["hours"] = "abc"                                    # not a number
    te[2]["entry_id"] = te[3]["entry_id"]                     # duplicate id
    te[4]["entry_id"] = ""                                    # missing id
    te[5]["project_id"] = "P-NOPE"                            # unknown project
    write(raw / "time_entries.csv", te, fields)

    tx = read(raw / "financial_transactions.csv")
    aed = next(t for t in tx if t["currency"] == "AED")
    aed.update(currency=" aed ", fx_rate_to_usd="", is_estimated="No")  # messy code, missing FX, yes/no boolean
    write(raw / "financial_transactions.csv", tx, list(tx[0]))

    ev = read(raw / "evidence_documents.csv")
    write(raw / "evidence_documents.csv", ev, [c for c in ev[0] if c != "extracted_fact"])  # missing column
    (raw / "scope_items.csv").unlink()                        # table not supplied

    log = normalize(raw, out, date_format="%d/%m/%Y")
    issues = {(e["level"], e["table"], e["column"], e["note"]) for e in log}

    te_out = read(out / "time_entries.csv")
    assert (te_out[0]["date"], te_out[0]["hours"]) == ("2026-01-12", "1040")
    assert "internal_note" not in te_out[0]
    assert te_out[1]["hours"] == ""                           # blank, never zero
    tx_out = next(t for t in read(out / "financial_transactions.csv") if t["transaction_id"] == aed["transaction_id"])
    assert (tx_out["currency"], tx_out["is_estimated"]) == ("AED", "false")

    assert {
        ("info", "time_entries", "date", "reformatted"),
        ("info", "time_entries", "internal_note", "column not in schema; dropped"),
        ("warning", "time_entries", "hours", "not a number"),
        ("error", "time_entries", "entry_id", "duplicate record id"),
        ("error", "time_entries", "entry_id", "missing record id"),
        ("warning", "time_entries", "project_id", "unknown project"),
        ("info", "financial_transactions", "currency", "reformatted"),
        ("warning", "financial_transactions", "fx_rate_to_usd", "no FX rate; amount cannot be converted to USD"),
        ("error", "evidence_documents", "extracted_fact", "column missing from source"),
        ("warning", "scope_items", "", "table not supplied"),
    } <= issues
    assert read(out / "normalization_log.csv") == [{k: str(v) for k, v in e.items()} for e in log]


def test_ambiguous_date_is_not_guessed(tmp_path):
    raw = tmp_path / "raw"
    shutil.copytree(SYNTHETIC, raw)
    te = read(raw / "time_entries.csv")
    te[0]["date"] = "03/04/2026"                              # March 4 or April 3?
    write(raw / "time_entries.csv", te, list(te[0]))
    log = normalize(raw, tmp_path / "out")                    # no date format declared
    assert [e["note"][:16] for e in log] == ["unreadable date "]
    assert read(tmp_path / "out" / "time_entries.csv")[0]["date"] == ""


def one_agency(agency_id):
    """The synthetic tables cut down to one agency, as a real agency folder would be."""
    rows = {t: read(SYNTHETIC / f"{t}.csv") for t in TABLES}
    projects = {p["project_id"] for p in rows["projects"] if p["agency_id"] == agency_id}
    return {t: [r for r in rs if r.get("agency_id", agency_id) == agency_id and r.get("project_id", "") in projects | {""}]
            for t, rs in rows.items()}


def thousands(v):  # "12000.0" -> "12,000.0", the way spreadsheets export amounts
    whole, dot, frac = v.partition(".")
    return f"{int(whole):,}{dot}{frac}" if whole.isdigit() else v


def export(rows, renames, date_column=None, date_format=None, numbers=(), drop=(), extra=None):
    """Rewrite schema rows the way an agency's tool would export them."""
    out = []
    for r in rows:
        r = dict(r)
        if date_column and r[date_column]:
            y, m, d = r[date_column].split("-")
            r[date_column] = date_format.replace("%Y", y).replace("%m", m).replace("%d", d)
        for c in numbers:
            r[c] = thousands(r[c])
        if "currency" in r:
            r["currency"] = r["currency"].lower()
        r = {renames.get(c, c): v for c, v in reversed(r.items()) if c not in drop}  # reordered columns
        out.append(r | (extra or {}))
    return out


def test_agency_export_with_mapping_matches_clean_data(tmp_path):
    rows = one_agency("AG01")
    clean, messy = tmp_path / "clean", tmp_path / "messy"
    clean.mkdir(), messy.mkdir()
    for t, rs in rows.items():
        write(clean / f"{t}.csv", rs, list(rs[0]))
    for t in ["agencies", "scope_items", "evidence_documents"]:         # already in schema form
        shutil.copy(clean / f"{t}.csv", messy)

    projects = {"project_id": "Project Code", "start_date": "Start", "end_date": "End", "contract_value": "Contract Value"}
    pr = export(rows["projects"], projects, numbers=["contract_value"], drop=["agency_id"])
    time = {"entry_id": "Entry", "project_id": "Project", "date": "Date", "hours": "Duration (h)", "hourly_cost_rate": "Rate"}
    te = export(rows["time_entries"], time, "date", "%m/%d/%Y", extra={"Billable": "Yes"})
    money = {"transaction_id": "Ref", "project_id": "Project", "transaction_date": "Date", "amount": "Amount"}
    tx = export(rows["financial_transactions"], money, "transaction_date", "%d/%m/%Y", numbers=["amount"])
    write(messy / "Project list.csv", pr, list(pr[0]))
    write(messy / "Clockify report.csv", te, list(te[0]))
    write(messy / "Invoices.csv", tx, list(tx[0]))
    (messy / "mapping.json").write_text(json.dumps({
        "projects": {"file": "Project list.csv", "columns": projects, "fixed": {"agency_id": "AG01"}},
        "time_entries": {"file": "Clockify report.csv", "columns": time, "date_format": "%m/%d/%Y"},
        "financial_transactions": {"file": "Invoices.csv", "columns": money, "date_format": "%d/%m/%Y"},
    }), encoding="utf-8")

    assert normalize(clean, tmp_path / "clean_out") == []
    log = normalize(messy, tmp_path / "messy_out")
    for t in TABLES:
        assert (tmp_path / "messy_out" / f"{t}.csv").read_bytes() == (tmp_path / "clean_out" / f"{t}.csv").read_bytes(), t
    findings = detect(tmp_path / "clean_out")
    assert findings and detect(tmp_path / "messy_out") == findings

    assert not [e for e in log if e["level"] != "info"]
    assert {
        ("projects", "", "Project list.csv", "read from this file"),
        ("projects", "agency_id", "", "fixed value for every row"),
        ("time_entries", "hours", "Duration (h)", "read from this column"),
        ("time_entries", "Billable", "", "column not in schema; dropped"),
    } <= {(e["table"], e["column"], e["original"], e["note"]) for e in log}
    assert headers(messy)["Clockify report.csv"][0] == "entry_confidence"  # columns came out reversed


def test_mapping_mistakes_are_logged(tmp_path):
    raw = tmp_path / "raw"
    shutil.copytree(SYNTHETIC, raw)
    (raw / "mapping.json").write_text(json.dumps({
        "timesheets": {},                                                  # not a table
        "time_entries": {"columns": {"hrs": "Hours", "hours": "Duration"}},  # not a column; header absent
        "financial_transactions": {"file": "nope.csv"},                    # file absent
        "scope_items": {"file": None},                                     # not supplied
    }), encoding="utf-8")
    issues = {(e["level"], e["table"], e["column"], e["original"], e["note"]) for e in normalize(raw, tmp_path / "out")}
    assert {
        ("error", "timesheets", "", "", "mapping.json names a table not in the schema"),
        ("error", "time_entries", "hrs", "", "mapping.json names a column not in the schema"),
        ("error", "time_entries", "hours", "Duration", "column missing from source"),
        ("warning", "financial_transactions", "", "nope.csv", "table not supplied"),
        ("warning", "scope_items", "", "", "table not supplied"),
    } <= issues


def test_table_not_supplied_is_written_empty_so_a_rerun_never_reads_old_output(tmp_path):
    raw, out = tmp_path / "raw", tmp_path / "out"
    shutil.copytree(SYNTHETIC, raw)
    normalize(raw, out)                                       # first run writes scope_items.csv
    (raw / "scope_items.csv").unlink()
    normalize(raw, out)
    assert read(out / "scope_items.csv") == []
    detect(out)                                               # the rest of the pipeline still runs
