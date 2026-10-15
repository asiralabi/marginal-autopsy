import csv
import shutil
from pathlib import Path

from src.reconcile import reconcile

SYNTHETIC = Path(__file__).resolve().parents[1] / "data" / "synthetic"

# Worked by hand from generate.py, independently of reconcile.py.
EXPECTED = {
    # USD project, clean: labour 290*12 + 55*10 + 60*8 + 40*15 = 5110, hosting 450
    "P-AG01-01": dict(planned_cost="5780.00", planned_profit="6220.00", planned_margin_pct="51.8",
                      invoiced_revenue="12000.00", cash_received="12000.00", labour_hours="445",
                      labour_cost="5110.00", other_cost="450.00", actual_cost="5560.00",
                      actual_profit="6440.00", actual_margin_pct="53.7", profit_variance="220.00",
                      data_complete="true"),
    # 30h without a cost rate: hours counted, cost not; data flagged incomplete
    "P-AG01-02": dict(planned_profit="10380.00", labour_hours="520", labour_hours_without_cost="30",
                      labour_cost="5790.00", actual_cost="5790.00", actual_profit="9210.00",
                      actual_margin_pct="61.4", profit_variance="-1170.00", data_complete="false"),
    # AED project, USD 8,000 subcontractor converted at 1.0 / 0.272294 = AED 29,380.01
    "P-AG02-02": dict(planned_cost="34850.00", planned_profit="15150.00", labour_cost="32025.00",
                      subcontractor_cost="29380.01", other_cost="1800.00", actual_cost="63205.01",
                      actual_profit="-13205.01", actual_margin_pct="-26.4", profit_variance="-28355.01",
                      data_complete="true"),
}


def run(folder):
    return {r["project_id"]: r for r in reconcile(folder)}


def copy_synthetic(tmp_path):
    shutil.copytree(SYNTHETIC, tmp_path / "data")
    return tmp_path / "data"


def edit(path, change):
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    rows = [r for r in (change(r) for r in rows) if r]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def test_baseline_matches_hand_calculation(tmp_path):
    rows = run(copy_synthetic(tmp_path))
    for pid, expected in EXPECTED.items():
        assert {k: rows[pid][k] for k in expected} == expected, pid
    assert "0.272294" in rows["P-AG02-02"]["fx_used"]  # the rate used is recorded


def test_open_project_is_excluded_with_reason(tmp_path):
    row = run(copy_synthetic(tmp_path))["P-AG02-04"]
    assert (row["included"], row["notes"], row.get("actual_cost")) == ("false", "project still open", None)


def test_zero_revenue_leaves_margin_undefined(tmp_path):
    data = copy_synthetic(tmp_path)
    edit(data / "financial_transactions.csv",
         lambda r: None if (r["project_id"], r["transaction_type"]) == ("P-AG01-01", "invoice") else r)
    row = run(data)["P-AG01-01"]
    assert (row["invoiced_revenue"], row["actual_margin_pct"]) == ("0.00", "")
    assert row["actual_profit"] == "-5560.00"
    assert "margin undefined" in row["notes"] and row["data_complete"] == "false"


def test_missing_fx_rate_is_flagged_not_guessed(tmp_path):
    data = copy_synthetic(tmp_path)
    edit(data / "financial_transactions.csv",
         lambda r: {**r, "fx_rate_to_usd": ""} if r["project_id"] == "P-AG02-02" and r["currency"] == "AED" else r)
    row = run(data)["P-AG02-02"]
    assert row["subcontractor_cost"] == "0.00"          # unconvertible USD cost left out, not guessed
    assert row["other_cost"] == "1800.00"               # same-currency costs need no rate
    assert "cannot convert USD to AED" in row["notes"] and row["data_complete"] == "false"
