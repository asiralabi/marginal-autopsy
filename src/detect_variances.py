"""Step 3 of the audit method: run the detection rules and write findings.csv.

Every finding is a candidate for human review (customer_verdict = unreviewed),
records the version of the rule that produced it, and lists the IDs of the
records behind it. Excluded projects are skipped. Thresholds and wording come
from rules.toml. Amounts are in the transaction's or project's currency; impact
types are never added together.

Usage: python -m src.detect_variances NORMALIZED_DIR
"""
import argparse
import csv
import tomllib
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from src.reconcile import by_project, money, num, read, reconcile

RULES_FILE = Path(__file__).with_name("rules.toml")
SCHEMA = Path(__file__).resolve().parents[1] / "schemas" / "audit_findings.csv"


def load_rules(path=RULES_FILE):
    with open(path, "rb") as f:
        return tomllib.load(f)


def hours_of(entries):
    return sum((Decimal(e["hours"]) for e in entries if e["hours"]), Decimal(0))


def finding(rules, rule_id, p, key, description, evidence, baseline=None, observed=None, impact=None,
            currency=None, confidence=None):
    r = rules[rule_id]
    return {"finding_id": f"{rule_id}:{p['project_id']}:{key}", "project_id": p["project_id"],
            "finding_type": rule_id, "rule_version": r["version"], "description": description,
            "evidence_ids": ";".join(evidence), "baseline_amount": money(baseline),
            "observed_amount": money(observed), "estimated_impact": money(impact),
            "currency": currency or p["currency"], "impact_type": r["impact_type"],
            "confidence": confidence or r["confidence"], "alternative_explanation": r["alternative_explanation"],
            "customer_verdict": "unreviewed", "actionability": "", "next_action": r["next_action"],
            "reviewer_id": ""}


def labour_overrun(rules, p, c):  # F1
    estimate, logged = num(p["estimated_labor_hours"]), hours_of(c.entries)
    if estimate is None or logged <= estimate * (1 + Decimal(str(rules["F1"]["threshold"]))):
        return []
    over = ((logged / estimate - 1) * 100).quantize(Decimal(1))
    text = f"Logged {logged}h against an estimate of {estimate}h (+{over}%)."
    if Decimal(c.baseline["labour_hours_without_cost"]):
        text += f" {c.baseline['labour_hours_without_cost']}h have no cost rate, so the cost overrun is a lower bound."
    planned, actual = num(p["estimated_labor_cost"]), num(c.baseline["labour_cost"])
    return [finding(rules, "F1", p, "project", text, c.estimate_docs + [e["entry_id"] for e in c.entries],
                    planned, actual, actual - planned if planned is not None else None)]


def changed_scope(c):
    return [s for s in c.scope if s["included_in_original_scope"] == "false"]


def unbilled_work(rules, p, c):  # F2
    out = []
    for s in changed_scope(c):
        work = [e for e in c.entries if e["task_id"] == s["scope_item_id"]]
        if not work or s["billable_status"] == "billed":
            continue
        cost = sum((Decimal(e["cost_amount"]) for e in work if e["cost_amount"]), Decimal(0))
        text = (f"{hours_of(work)}h logged on '{s['description']}', which was not in the original scope and "
                f"has no matching invoice. The work cost at least {p['currency']} {money(cost)}; "
                f"its billable value depends on the agreed rate.")
        evidence = [s["scope_item_id"]] + ([s["source_document_id"]] if s["source_document_id"] else [])
        out.append(finding(rules, "F2", p, s["scope_item_id"], text, evidence + [e["entry_id"] for e in work],
                           num(s["agreed_price_change"]), cost, cost))
    return out


def undocumented_change(rules, p, c):  # F3
    return [finding(rules, "F3", p, s["scope_item_id"],
                    f"'{s['description']}' was added outside the original scope"
                    f"{' (requested ' + s['change_request_date'] + ')' if s['change_request_date'] else ''}"
                    f" with no approval record.",
                    [s["scope_item_id"]] + ([s["source_document_id"]] if s["source_document_id"] else []))
            for s in changed_scope(c) if s["change_approval_status"] != "approved"]


def unplanned_subcontracting(rules, p, c):  # F5
    actual, planned = num(c.baseline["subcontractor_cost"]), num(p["estimated_subcontractor_cost"])
    subs = [t for t in c.txs if t["transaction_type"] == "expense_subcontractor"]
    if not actual or (planned is not None and actual <= planned):
        return []
    evidence = c.estimate_docs + [x for t in subs for x in (t["transaction_id"], t["source_document_id"]) if x]
    if planned is None:
        return [finding(rules, "F5", p, "project",
                        f"Subcontractor cost of {p['currency']} {money(actual)} with no planned figure recorded; "
                        f"needs the original budget to confirm.", evidence, None, actual, None, confidence="low")]
    return [finding(rules, "F5", p, "project",
                    f"Subcontractor cost of {p['currency']} {money(actual)} against {money(planned)} planned.",
                    evidence, planned, actual, actual - planned)]


def hours_without_cost(rules, p, c):  # F6
    unknown = [e for e in c.entries if e["hours"] and not e["cost_amount"]]
    if not unknown:
        return []
    roles = ", ".join(sorted({e["role_or_pseudonym"] for e in unknown}))
    return [finding(rules, "F6", p, "project",
                    f"{hours_of(unknown)}h logged by {roles} have no cost rate, so the project's cost is understated.",
                    [e["entry_id"] for e in unknown])]


def payment_gaps(rules, p, c):  # F8
    by_invoice = defaultdict(list)
    for t in c.txs:
        if t["invoice_id"] and t["transaction_type"] in ("invoice", "payment", "bank_fee"):
            by_invoice[t["invoice_id"]].append(t)
    out, tolerance = [], Decimal(str(rules["F8"]["tolerance"]))
    for inv, txs in sorted(by_invoice.items()):
        total = {k: sum((num(t["amount"]) for t in txs if t["transaction_type"] == k), Decimal(0))
                 for k in ("invoice", "payment", "bank_fee")}
        gap = total["invoice"] - total["payment"] - total["bank_fee"]
        if abs(gap) <= tolerance:
            continue
        cur = txs[0]["currency"]
        text = (f"Invoice {inv}: invoiced {cur} {money(total['invoice'])}, received {money(total['payment'])}, "
                f"documented deductions {money(total['bank_fee'])}; {money(abs(gap))} "
                f"{'unaccounted for' if gap > 0 else 'received above the invoice'}.")
        evidence = [x for t in txs for x in (t["transaction_id"], t["source_document_id"]) if x]
        out.append(finding(rules, "F8", p, inv, text, evidence, total["invoice"], total["payment"] + total["bank_fee"],
                           abs(gap), currency=cur))
    return out


PROJECT_RULES = [labour_overrun, unbilled_work, undocumented_change, unplanned_subcontracting,
                 hours_without_cost, payment_gaps]


def duplicates(rules, projects, txs_by_project):  # F7: compares across the agency's projects
    window, seen, out = rules["F7"]["window_days"], defaultdict(list), []
    rows = [t for pid in projects for t in txs_by_project[pid]]
    for t in sorted(rows, key=lambda t: (t["transaction_date"], t["transaction_id"])):
        key = (t["transaction_type"], t["vendor_or_customer_ref"], t["amount"], t["currency"])
        day = date.fromisoformat(t["transaction_date"])
        earlier = [o for o in seen[key] if (day - date.fromisoformat(o["transaction_date"])).days <= window]
        if earlier:
            first, amount = earlier[0], num(t["amount"])
            out.append(finding(rules, "F7", projects[t["project_id"]], t["transaction_id"],
                               f"{t['transaction_type']} of {t['currency']} {money(amount)} from "
                               f"{t['vendor_or_customer_ref']} on {t['transaction_date']} repeats "
                               f"{first['transaction_id']} on {first['transaction_date']}.",
                               [first["transaction_id"], first["source_document_id"],
                                t["transaction_id"], t["source_document_id"]],
                               amount, amount * 2, amount, currency=t["currency"]))
        seen[key].append(t)
    return out


def detect(folder, rules=None):
    rules = rules or load_rules()
    baseline = {b["project_id"]: b for b in reconcile(folder)}
    projects = {p["project_id"]: p for p in read(folder, "projects") if baseline[p["project_id"]]["included"] == "true"}
    entries, txs = by_project(read(folder, "time_entries")), by_project(read(folder, "financial_transactions"))
    scope, docs = by_project(read(folder, "scope_items")), by_project(read(folder, "evidence_documents"))
    findings = []
    for pid, p in projects.items():
        c = SimpleNamespace(baseline=baseline[pid], entries=entries[pid], txs=txs[pid], scope=scope[pid],
                            estimate_docs=[d["document_id"] for d in docs[pid] if d["document_type"] == "estimate"])
        for rule in PROJECT_RULES:
            findings += rule(rules, p, c)
    findings += duplicates(rules, projects, txs)

    with open(SCHEMA, encoding="utf-8") as f:
        columns = next(csv.reader(f))
    with open(Path(folder) / "findings.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, lineterminator="\n")
        w.writeheader()
        w.writerows(findings)
    return findings


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Run the detection rules on a normalized folder.")
    p.add_argument("normalized_dir")
    folder = p.parse_args().normalized_dir
    print(f"{len(detect(folder))} findings -> {Path(folder) / 'findings.csv'}")
