import csv
import shutil
from pathlib import Path

from src.normalize import TABLES, normalize

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
