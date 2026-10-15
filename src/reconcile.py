"""Step 2 of the audit method: reconstruct each project's economics.

Reads normalized tables and writes baseline.csv: planned vs actual cost, profit,
margin and variance per project, in the project's own currency.

- Revenue = invoiced amount; cash received is shown beside it, never mixed in.
- Missing values are never treated as zero: costs that cannot be known are left
  out of the total, data_complete is set to false, and notes say why.
- Decimal arithmetic throughout; rounding happens only when writing.

Usage: python -m src.reconcile NORMALIZED_DIR
"""
import argparse
import csv
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

FIELDS = ["project_id", "agency_id", "currency", "included", "contract_value", "planned_cost", "planned_profit",
          "planned_margin_pct", "invoiced_revenue", "cash_received", "labour_hours", "labour_hours_without_cost",
          "labour_cost", "subcontractor_cost", "other_cost", "actual_cost", "actual_profit", "actual_margin_pct",
          "profit_variance", "data_complete", "fx_used", "notes"]
COST_TYPES = {"expense_subcontractor": "subcontractor_cost", "expense_other": "other_cost"}
NOT_COSTS = {"invoice", "payment", "bank_fee"}


def num(v):
    return Decimal(v) if v not in ("", None) else None


def money(x):
    return "" if x is None else str(x.quantize(Decimal("0.01"), ROUND_HALF_UP))


def pct(part, whole):
    if part is None or not whole:  # zero or unknown denominator: ratio undefined
        return ""
    return str((part / whole * 100).quantize(Decimal("0.1"), ROUND_HALF_UP))


def read(folder, table):
    with open(Path(folder) / f"{table}.csv", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def by_project(rows):
    grouped = defaultdict(list)
    for r in rows:
        grouped[r["project_id"]].append(r)
    return grouped


def project_rate(currency, txs):
    """USD value of one unit of the project currency, taken from its own transactions."""
    if currency == "USD":
        return Decimal(1), "USD base"
    rates = {(t["fx_rate_to_usd"], t["fx_rate_source"]) for t in txs if t["currency"] == currency and t["fx_rate_to_usd"]}
    if len(rates) == 1:
        rate, source = rates.pop()
        return Decimal(rate), source
    return None, f"{'no' if not rates else 'several different'} {currency}->USD rates recorded"


def baseline(p, entries, txs):
    row = {"project_id": p["project_id"], "agency_id": p["agency_id"], "currency": p["currency"]}
    if p["project_status"] != "completed":
        row.update(included="false", notes=p["exclusion_reason"] or f"status: {p['project_status']}")
        return row
    cur, notes = p["currency"], []

    planned_parts = [num(p[c]) for c in ("estimated_labor_cost", "estimated_other_cost", "estimated_subcontractor_cost")]
    if None in planned_parts:
        notes.append("planned cost incomplete (an estimate field is blank)")
    planned_cost = sum((x for x in planned_parts if x is not None), Decimal(0))
    contract = num(p["contract_value"])
    planned_profit = contract - planned_cost if contract is not None else None

    hours = sum((Decimal(e["hours"]) for e in entries if e["hours"]), Decimal(0))
    unknown = [e for e in entries if e["hours"] and not e["cost_amount"]]
    hours_without_cost = sum((Decimal(e["hours"]) for e in unknown), Decimal(0))
    if unknown:
        notes.append(f"{hours_without_cost}h have no cost rate; their cost is not included")
    labour = sum((Decimal(e["cost_amount"]) for e in entries if e["cost_amount"]), Decimal(0))

    rate, rate_source = project_rate(cur, txs)
    totals, fx_used = defaultdict(Decimal), set()
    for t in txs:
        kind, amount = t["transaction_type"], num(t["amount"])
        if kind not in COST_TYPES and kind not in NOT_COSTS:
            notes.append(f"{t['transaction_id']}: unrecognized type '{kind}', ignored")
            continue
        if amount is None:
            notes.append(f"{t['transaction_id']}: no amount")
            continue
        if t["currency"] != cur:
            if rate is None or not t["fx_rate_to_usd"]:
                notes.append(f"{t['transaction_id']}: cannot convert {t['currency']} to {cur} ({rate_source})")
                continue
            amount = amount * Decimal(t["fx_rate_to_usd"]) / rate
            fx_used.add(f"{t['currency']}->{cur} via USD: {t['fx_rate_to_usd']} / {rate} ({rate_source})")
        totals[COST_TYPES.get(kind, kind)] += amount

    revenue = totals["invoice"]
    actual_cost = labour + totals["subcontractor_cost"] + totals["other_cost"]
    profit = revenue - actual_cost
    if not revenue:
        notes.append("no invoiced revenue; margin undefined")
    row.update(
        included="true", contract_value=money(contract), planned_cost=money(planned_cost),
        planned_profit=money(planned_profit), planned_margin_pct=pct(planned_profit, contract),
        invoiced_revenue=money(revenue), cash_received=money(totals["payment"]), labour_hours=str(hours),
        labour_hours_without_cost=str(hours_without_cost), labour_cost=money(labour),
        subcontractor_cost=money(totals["subcontractor_cost"]), other_cost=money(totals["other_cost"]),
        actual_cost=money(actual_cost), actual_profit=money(profit), actual_margin_pct=pct(profit, revenue),
        profit_variance=money(profit - planned_profit) if planned_profit is not None else "",
        data_complete="false" if notes else "true", fx_used="; ".join(sorted(fx_used)), notes="; ".join(notes))
    return row


def reconcile(folder):
    entries = by_project(read(folder, "time_entries"))
    txs = by_project(read(folder, "financial_transactions"))
    rows = [baseline(p, entries[p["project_id"]], txs[p["project_id"]]) for p in read(folder, "projects")]
    with open(Path(folder) / "baseline.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return rows


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Reconstruct planned vs actual economics per project.")
    p.add_argument("normalized_dir")
    rows = reconcile(p.parse_args().normalized_dir)
    print(f"{sum(r['included'] == 'true' for r in rows)} projects reconciled, "
          f"{sum(r['included'] == 'false' for r in rows)} excluded, "
          f"{sum(r.get('data_complete') == 'false' for r in rows)} with incomplete data")
