import csv
import shutil
from pathlib import Path

from src.detect_variances import detect, load_rules

SYNTHETIC = Path(__file__).resolve().parents[1] / "data" / "synthetic"


def copy_synthetic(tmp_path):
    shutil.copytree(SYNTHETIC, tmp_path / "data")
    return tmp_path / "data"


def answer_key():
    with open(SYNTHETIC / "answer_key.csv", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_findings_match_answer_key_exactly(tmp_path):
    found = {(f["project_id"], f["finding_type"]) for f in detect(copy_synthetic(tmp_path))}
    expected = {(k["project_id"], k["rule"]) for k in answer_key() if k["expected"] == "flag"}
    assert found == expected  # every planted problem found, nothing else flagged


def test_traps_and_excluded_projects_stay_silent(tmp_path):
    found = {(f["project_id"], f["finding_type"]) for f in detect(copy_synthetic(tmp_path))}
    for k in answer_key():
        if k["expected"] == "no_flag":
            rules = None if k["rule"] == "all" else k["rule"].split("/")  # "F2/F3" covers both
            assert not {f for f in found if f[0] == k["project_id"] and (rules is None or f[1] in rules)}, k["case_id"]
        if k["expected"] == "exclude":
            assert not {f for f in found if f[0] == k["project_id"]}, k["case_id"]


def test_amounts_match_hand_calculation(tmp_path):
    impact = {(f["finding_type"], f["project_id"]): (f["estimated_impact"], f["currency"])
              for f in detect(copy_synthetic(tmp_path))}
    assert impact[("F1", "P-AG01-02")] == ("1170.00", "USD")    # 5790 actual - 4620 planned labour
    assert impact[("F2", "P-AG01-03")] == ("720.00", "USD")     # 60h x USD 12
    assert impact[("F5", "P-AG02-02")] == ("29380.01", "AED")   # USD 8,000 converted, none planned
    assert impact[("F7", "P-AG02-01")] == ("3500.00", "AED")    # the duplicate copy
    assert impact[("F8", "P-AG02-03")] == ("8000.00", "AED")    # 40,000 invoiced - 32,000 received
    assert impact[("F6", "P-AG01-02")] == ("", "USD")           # unknown, not zero


def test_every_finding_is_traceable_and_versioned(tmp_path):
    data = copy_synthetic(tmp_path)
    known = set()
    for table, key in [("time_entries", "entry_id"), ("financial_transactions", "transaction_id"),
                       ("scope_items", "scope_item_id"), ("evidence_documents", "document_id")]:
        with open(data / f"{table}.csv", encoding="utf-8") as f:
            known |= {r[key] for r in csv.DictReader(f)}
    for f in detect(data):
        ids = f["evidence_ids"].split(";")
        assert ids and set(ids) <= known, f["finding_id"]
        assert f["rule_version"] and f["customer_verdict"] == "unreviewed"


def test_threshold_comes_from_rules_config(tmp_path):
    rules = load_rules()
    rules["F1"]["threshold"] = 0.05  # P-AG02-03 is +10%: silent at 15%, flagged at 5%
    found = {(f["project_id"], f["finding_type"]) for f in detect(copy_synthetic(tmp_path), rules)}
    assert ("P-AG02-03", "F1") in found


def test_output_is_deterministic(tmp_path):
    data = copy_synthetic(tmp_path)
    detect(data)
    first = (data / "findings.csv").read_bytes()
    detect(data)
    assert (data / "findings.csv").read_bytes() == first
