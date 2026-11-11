"""Steps 6 and 7 of the audit method: record the analyst's review and the agency's verdict.

Verdicts live in verdicts.csv in the agency folder, apart from the generated
findings, so re-running the rules never loses them. They are matched to findings
by finding_id, which is built from the rule, project and record, so the same data
always gives the same IDs. Rows for findings the rules no longer produce are kept:
human input is never deleted.
"""
import csv
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from src.normalize import clean

VERDICTS = "verdicts.csv"
FIELDS = ["finding_id", "analyst_decision", "analyst_note", "customer_verdict", "already_known",
          "actionability", "agreed_amount", "agency_comment", "reviewer_id", "reviewed_on"]
ALLOWED = {
    "analyst_decision": {"", "keep", "drop"},
    "customer_verdict": {"unreviewed", "confirmed", "partially_confirmed", "rejected", "needs_more_evidence"},  # protocol §5
    "already_known": {"", "yes", "no"},
    "actionability": {"", "yes", "no"},  # yes = a next step was agreed (protocol §6, actionability)
}


def load(folder):
    path = Path(folder) / VERDICTS
    if not path.exists():
        return {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        return {r["finding_id"]: r for r in csv.DictReader(f)}


def record(folder, updates, reviewer="", today=""):
    """Merge edited rows into verdicts.csv and return all rows. A changed verdict is
    stamped with the reviewer and the date."""
    rows = load(folder)
    for u in updates:
        fid, old = u["finding_id"], rows.get(u["finding_id"], {})
        new = {f: str(u.get(f, old.get(f, ""))).strip() for f in FIELDS}
        new["customer_verdict"] = new["customer_verdict"] or "unreviewed"
        for field, allowed in ALLOWED.items():
            if new[field] not in allowed:
                raise ValueError(f"{fid}: {field} '{new[field]}' is not one of {', '.join(sorted(allowed - {''}))}")
        new["agreed_amount"], problem = clean("amount", new["agreed_amount"])
        if problem:
            raise ValueError(f"{fid}: agreed amount '{u.get('agreed_amount')}' is {problem}")
        if new["customer_verdict"] != old.get("customer_verdict", "unreviewed"):
            new["reviewer_id"], new["reviewed_on"] = reviewer, today
        rows[fid] = new
    with open(Path(folder) / VERDICTS, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows.values())
    return rows


def reviewed(findings, verdicts):
    """Each finding with its review attached; a finding nobody has reviewed is 'unreviewed'."""
    return [{**f, **{k: verdicts.get(f["finding_id"], {}).get(k, "") for k in FIELDS[1:]},
             "customer_verdict": verdicts.get(f["finding_id"], {}).get("customer_verdict") or "unreviewed"}
            for f in findings]


def confirmed_totals(findings):
    """Confirmed impact by impact type and currency; impact types are never added together.
    'confirmed' counts the agreed amount, else the estimate. 'partially_confirmed' counts only
    an agreed amount. Dropped, rejected, unresolved and unreviewed findings never count."""
    totals = defaultdict(lambda: defaultdict(Decimal))
    for f in findings:
        if f["analyst_decision"] == "drop" or f["customer_verdict"] not in ("confirmed", "partially_confirmed"):
            continue
        amount = f["agreed_amount"] or (f["estimated_impact"] if f["customer_verdict"] == "confirmed" else "")
        if amount:
            totals[f["impact_type"]][f["currency"]] += Decimal(amount)
    return {kind: dict(by_currency) for kind, by_currency in totals.items()}
