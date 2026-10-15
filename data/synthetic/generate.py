"""Build a small synthetic dataset with planted problems and a known answer key.

Everything here is fictional. Run `python generate.py [out_dir]`; output is
deterministic, so the committed CSVs must match a fresh run (see tests).

Conventions (until real data says otherwise):
- Amounts are in the transaction's own currency; fx_rate_to_usd converts to USD.
- time_entries.task_id equals a scope_item_id when the work belongs to that scope item.
- A blank hourly_cost_rate means "unknown", never zero.
"""
import csv
import sys
from datetime import date, timedelta
from pathlib import Path

SCHEMAS = Path(__file__).resolve().parents[2] / "schemas"
AED_TO_USD = round(1 / 3.6725, 6)  # CBUAE peg
FX = {"USD": (1.0, "USD base"), "AED": (AED_TO_USD, "CBUAE peg 3.6725")}

AGENCIES = [
    # agency_id, region, industry, employee_band, project_type, currency, billing_model, costing_method, permission
    ["AG01", "BD", "software", "10-49", "web and mobile", "USD", "fixed-price", "loaded hourly rate per role", "synthetic"],
    ["AG02", "UAE", "software", "10-49", "web and mobile", "AED", "milestone", "loaded hourly rate per role", "synthetic"],
]
RATES = {  # loaded cost per hour, in the agency currency
    "AG01": {"Developer": 12, "Designer": 10, "QA": 8, "PM": 15},
    "AG02": {"Developer": 90, "Designer": 75, "QA": 60, "PM": 110},
}
SOURCE_SYSTEM = {"AG01": "Jira", "AG02": "ClickUp"}
CURRENCY = {a[0]: a[5] for a in AGENCIES}

# Each project: estimate and actual hours per role. Actual hours may be split into
# (role, hours, task_id, rate_known) work items to plant specific problems.
PROJECTS = [
    dict(id="P-AG01-01", agency="AG01", start=date(2026, 1, 5), end=date(2026, 3, 27), contract=12000, other_est=500,
         est={"Developer": 300, "Designer": 60, "QA": 60, "PM": 40},
         work=[("Developer", 290), ("Designer", 55), ("QA", 60), ("PM", 40)]),
    # F1 labour overrun (400h -> 520h) and F6 hours with no cost rate (30h QA contractor)
    dict(id="P-AG01-02", agency="AG01", start=date(2026, 1, 12), end=date(2026, 4, 10), contract=15000, other_est=0,
         est={"Developer": 300, "Designer": 40, "QA": 40, "PM": 20},
         work=[("Developer", 390), ("Designer", 50), ("QA", 20), ("QA contractor", 30, None, False), ("PM", 30)]),
    # F2/F3: 60h on a payment gateway that was never in scope, approved, or billed
    dict(id="P-AG01-03", agency="AG01", start=date(2026, 2, 2), end=date(2026, 5, 1), contract=18000, other_est=0,
         est={"Developer": 360, "Designer": 60, "QA": 60, "PM": 40},
         work=[("Developer", 350), ("Developer", 60, "SI-AG01-03-04"), ("Designer", 60), ("QA", 55), ("PM", 40)]),
    # F7 duplicate licence expense; traps: approved and billed change request (40h),
    # subcontractor within plan (USD 2,500 ~ AED 9,181 against AED 10,000 planned)
    dict(id="P-AG02-01", agency="AG02", start=date(2026, 1, 19), end=date(2026, 4, 17), contract=60000, other_est=8000,
         sub_est=10000,
         est={"Developer": 280, "Designer": 60, "QA": 40, "PM": 40},
         work=[("Developer", 270), ("Developer", 40, "SI-AG02-01-03"), ("Designer", 60), ("QA", 40), ("PM", 40)]),
    # F5 subcontractor cost (USD 8,000) with none planned
    dict(id="P-AG02-02", agency="AG02", start=date(2026, 2, 9), end=date(2026, 5, 8), contract=50000, other_est=2000,
         est={"Developer": 260, "Designer": 50, "QA": 40, "PM": 30},
         work=[("Developer", 255), ("Designer", 45), ("QA", 40), ("PM", 30)]),
    # F8 short payment (AED 8,000) with no deduction; trap: 10% overrun, below the 15% threshold
    dict(id="P-AG02-03", agency="AG02", start=date(2026, 2, 16), end=date(2026, 6, 5), contract=80000, other_est=0,
         est={"Developer": 360, "Designer": 60, "QA": 50, "PM": 30},
         work=[("Developer", 400), ("Designer", 65), ("QA", 55), ("PM", 30)]),
    # Must be excluded: still open
    dict(id="P-AG02-04", agency="AG02", start=date(2026, 5, 4), end=None, contract=40000, other_est=0, status="open",
         est={"Developer": 240, "Designer": 40, "QA": 30, "PM": 20},
         work=[("Developer", 120), ("Designer", 30), ("PM", 10)]),
]

SCOPE_ITEMS = [
    # scope_item_id, project_id, description, source doc ("SOW", "APPROVAL" or blank), in_original,
    # cr_date, approval, price_change, billable_status, notes
    ["SI-AG01-03-01", "P-AG01-03", "User accounts", "SOW", "true", "", "", "", "in fixed fee", ""],
    ["SI-AG01-03-02", "P-AG01-03", "Product catalogue", "SOW", "true", "", "", "", "in fixed fee", ""],
    ["SI-AG01-03-03", "P-AG01-03", "Admin dashboard", "SOW", "true", "", "", "", "in fixed fee", ""],
    ["SI-AG01-03-04", "P-AG01-03", "Payment gateway integration", "", "false", "2026-03-10", "none", "", "unknown",
     "Requested on a call; no written approval or price"],
    ["SI-AG02-01-01", "P-AG02-01", "Booking flow", "SOW", "true", "", "", "", "in fixed fee", ""],
    ["SI-AG02-01-02", "P-AG02-01", "Customer app", "SOW", "true", "", "", "", "in fixed fee", ""],
    ["SI-AG02-01-03", "P-AG02-01", "Arabic localisation", "APPROVAL", "false", "2026-03-02", "approved", "6000", "billed",
     "Approved by client email; invoiced separately"],
]

# Money movements per project, in the transaction currency.
# (type, date, amount, currency, counterparty, invoice_id, doc_type)
TRANSACTIONS = {
    "P-AG01-01": [("invoice", "2026-03-27", 12000, "USD", "C-AG01-A", "INV-0101", "invoice"),
                  ("payment", "2026-04-15", 12000, "USD", "C-AG01-A", "INV-0101", "bank statement"),
                  ("expense_other", "2026-02-01", 450, "USD", "V-HOST-01", "", "vendor invoice")],
    "P-AG01-02": [("invoice", "2026-04-10", 15000, "USD", "C-AG01-B", "INV-0201", "invoice"),
                  ("payment", "2026-04-30", 15000, "USD", "C-AG01-B", "INV-0201", "bank statement")],
    "P-AG01-03": [("invoice", "2026-05-01", 18000, "USD", "C-AG01-C", "INV-0301", "invoice"),
                  ("payment", "2026-05-20", 17975, "USD", "C-AG01-C", "INV-0301", "bank statement"),
                  ("bank_fee", "2026-05-20", 25, "USD", "C-AG01-C", "INV-0301", "bank charge advice")],
    "P-AG02-01": [("invoice", "2026-02-28", 30000, "AED", "C-AG02-A", "INV-1101", "invoice"),
                  ("payment", "2026-03-20", 30000, "AED", "C-AG02-A", "INV-1101", "bank statement"),
                  ("invoice", "2026-04-17", 30000, "AED", "C-AG02-A", "INV-1102", "invoice"),
                  ("payment", "2026-05-06", 30000, "AED", "C-AG02-A", "INV-1102", "bank statement"),
                  ("invoice", "2026-04-17", 6000, "AED", "C-AG02-A", "INV-1103", "invoice"),
                  ("payment", "2026-05-06", 6000, "AED", "C-AG02-A", "INV-1103", "bank statement"),
                  ("expense_subcontractor", "2026-03-15", 2500, "USD", "V-DHAKA-02", "", "vendor invoice"),
                  ("expense_other", "2026-04-05", 3500, "AED", "V-TOOLS-01", "", "vendor invoice"),
                  ("expense_other", "2026-04-07", 3500, "AED", "V-TOOLS-01", "", "vendor invoice")],
    "P-AG02-02": [("invoice", "2026-03-31", 25000, "AED", "C-AG02-B", "INV-1201", "invoice"),
                  ("payment", "2026-04-20", 25000, "AED", "C-AG02-B", "INV-1201", "bank statement"),
                  ("invoice", "2026-05-08", 25000, "AED", "C-AG02-B", "INV-1202", "invoice"),
                  ("payment", "2026-05-28", 25000, "AED", "C-AG02-B", "INV-1202", "bank statement"),
                  ("expense_subcontractor", "2026-04-15", 8000, "USD", "V-DHAKA-01", "", "vendor invoice"),
                  ("expense_other", "2026-03-01", 1800, "AED", "V-HOST-02", "", "vendor invoice")],
    "P-AG02-03": [("invoice", "2026-04-15", 40000, "AED", "C-AG02-C", "INV-1301", "invoice"),
                  ("payment", "2026-05-05", 40000, "AED", "C-AG02-C", "INV-1301", "bank statement"),
                  ("invoice", "2026-06-05", 40000, "AED", "C-AG02-C", "INV-1302", "invoice"),
                  ("payment", "2026-06-25", 32000, "AED", "C-AG02-C", "INV-1302", "bank statement")],
    "P-AG02-04": [("invoice", "2026-06-01", 20000, "AED", "C-AG02-D", "INV-1401", "invoice")],
}

ANSWER_KEY = [
    # case_id, project_id, rule, expected, detail
    ["K01", "P-AG01-01", "all", "no_flag", "Clean control project: within estimate, paid in full"],
    ["K02", "P-AG01-02", "F1", "flag", "Estimated 400h, actual 520h (+30%)"],
    ["K03", "P-AG01-02", "F6", "flag", "30h by 'QA contractor' with no cost rate"],
    ["K04", "P-AG01-03", "F2", "flag", "60h on SI-AG01-03-04 (payment gateway): outside original scope, no extra invoice"],
    ["K05", "P-AG01-03", "F3", "flag", "SI-AG01-03-04 has no approval record; same root cause as K04, merge at review"],
    ["K06", "P-AG01-03", "F8", "no_flag", "Received USD 17,975 of 18,000; the USD 25 gap is a documented bank fee"],
    ["K07", "P-AG02-01", "F7", "flag", "AED 3,500 licence from V-TOOLS-01 recorded twice (2026-04-05 and 2026-04-07)"],
    ["K08", "P-AG02-01", "F2/F3", "no_flag", "SI-AG02-01-03 is outside original scope but approved and invoiced (INV-1103)"],
    ["K09", "P-AG02-02", "F5", "flag", "USD 8,000 subcontractor cost; no subcontractor cost planned (0)"],
    ["K10", "P-AG02-03", "F8", "flag", "INV-1302: invoiced AED 40,000, received AED 32,000, no deduction recorded"],
    ["K11", "P-AG02-03", "F1", "no_flag", "Estimated 500h, actual 550h (+10%), below the 15% threshold"],
    ["K12", "P-AG02-04", "exclusion", "exclude", "Project still open"],
    ["K13", "P-AG02-01", "F5", "no_flag", "Subcontractor USD 2,500 (~AED 9,181) within the AED 10,000 planned"],
]


def weekly(start, hours):
    """Split hours into weekly entries of at most 40h, starting on `start`."""
    d, left = start, hours
    while left > 0:
        h = min(40, left)
        yield d, h
        d, left = d + timedelta(days=7), left - h


def build():
    tables = {"agencies": AGENCIES, "scope_items": [], "answer_key": ANSWER_KEY,
              "projects": [], "time_entries": [], "financial_transactions": [], "evidence_documents": []}
    for p in PROJECTS:
        pid, ag = p["id"], p["agency"]
        rates, cur = RATES[ag], CURRENCY[ag]
        docs = tables["evidence_documents"]

        def doc(doc_type, d, fact, ref="p.1"):
            doc_id = f"D-{pid[2:]}-{len([x for x in docs if x[1] == pid]) + 1:02d}"
            docs.append([doc_id, pid, doc_type, f"{doc_id}.pdf", d, ref, fact, "synthetic"])
            return doc_id

        sow = doc("statement of work", p["start"].isoformat(), f"Contract value {cur} {p['contract']}")
        doc("estimate", p["start"].isoformat(), f"Estimated hours {sum(p['est'].values())}")
        for si in (s for s in SCOPE_ITEMS if s[1] == pid):
            src = {"SOW": sow, "": ""}.get(si[3]) if si[3] != "APPROVAL" else                 doc("client approval email", si[5], f"Approved {si[2]} for {cur} {si[7]}")
            tables["scope_items"].append(si[:3] + [src] + si[4:])

        labour_cost, n = 0.0, 0
        for item in p["work"]:
            role, hours = item[0], item[1]
            task = item[2] if len(item) > 2 and item[2] else f"T-{pid[2:]}-{role.split()[0][:3].upper()}"
            rate = rates.get(role) if (len(item) < 4 or item[3]) else None
            for d, h in weekly(p["start"], hours):
                n += 1
                cost = round(h * rate, 2) if rate is not None else ""
                labour_cost += cost or 0
                tables["time_entries"].append([f"TE-{pid[2:]}-{n:03d}", pid, d.isoformat(), role, task, h,
                                               rate if rate is not None else "", cost, SOURCE_SYSTEM[ag], "recorded"])

        revenue = other_cost = 0.0
        for i, (kind, d, amt, tcur, cp, inv, dtype) in enumerate(TRANSACTIONS[pid], 1):
            rate_usd, src = FX[tcur]
            doc_id = doc(dtype, d, f"{kind} {tcur} {amt}")
            tables["financial_transactions"].append(
                [f"TX-{pid[2:]}-{i:02d}", pid, d, kind, amt, tcur, rate_usd, src, cp, inv, doc_id, "direct", "false"])
            in_project_cur = amt * rate_usd / FX[cur][0]
            if kind == "invoice":
                revenue += in_project_cur
            elif kind.startswith("expense"):
                other_cost += in_project_cur

        est_labour = sum(h * rates[r] for r, h in p["est"].items())
        sub_est = p.get("sub_est", 0)
        planned_margin = round((p["contract"] - est_labour - p["other_est"] - sub_est) / p["contract"] * 100, 1)
        status = p.get("status", "completed")
        tables["projects"].append([
            pid, ag, p["start"].isoformat(), p["end"].isoformat() if p["end"] else "", "web and mobile",
            p["contract"], sum(p["est"].values()), est_labour, p["other_est"], sub_est, planned_margin,
            round(revenue, 2), round(labour_cost + other_cost, 2), cur, status,
            "project still open" if status == "open" else ""])
    return tables


def write(out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in build().items():
        if name == "answer_key":
            header = ["case_id", "project_id", "rule", "expected", "detail"]
        else:
            header = next(csv.reader(open(SCHEMAS / f"{name}.csv", encoding="utf-8")))
        with open(out_dir / f"{name}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(header)
            w.writerows(rows)


if __name__ == "__main__":
    write(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent)
