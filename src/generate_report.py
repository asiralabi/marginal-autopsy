"""Step 4 of the audit method: write one draft report per agency.

Runs the rules (which run reconciliation), then writes report_<agency_id>.html:
summary by impact type, planned vs actual per project, every finding in the
protocol's 10-part format with a verdict box, recurring patterns, data gaps,
and the method. Amounts stay in their original currency and impact types are
never added together. Findings are ordered by confidence, then by impact in
USD (used for ordering only).

Usage: python -m src.generate_report NORMALIZED_DIR OUT_DIR
"""
import argparse
import html
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from src.detect_variances import detect, load_rules
from src.reconcile import read

IMPACT_TYPES = {  # protocol §4 order and plain-language meaning
    "confirmed_discrepancy": "Confirmed discrepancy: the records disagree by this amount.",
    "potentially_billable": "Potentially billable: work that may be invoiceable; value depends on your rates.",
    "avoidable_future_cost": "Avoidable future cost: a cost that a process change could prevent.",
    "forecasted_risk": "Forecasted risk: a likely future loss if nothing changes.",
    "unquantified_process_issue": "Process issue: a control gap with no amount attached.",
    "realized_recovery": "Realized recovery: money actually recovered after the audit.",
}
CONFIDENCE_ORDER = {"high": 0, "medium": 1, "low": 2}
TABLES = {"time_entries": "entry_id", "financial_transactions": "transaction_id", "scope_items": "scope_item_id"}
e = html.escape


def evidence_index(folder):
    """Map each record or document ID to where a reader finds it."""
    index = {}
    for d in read(folder, "evidence_documents"):
        index[d["document_id"]] = ("doc", f"{d['file_reference']} ({d['document_type']}, {d['relevant_page_or_row']})")
    for table, key in TABLES.items():
        for line, row in enumerate(read(folder, table), start=2):  # line 1 is the header
            index[row[key]] = (f"{table}.csv", line)
    return index


def line_ranges(lines):
    lines, out, start = sorted(lines), [], None
    for i, n in enumerate(lines):
        start = n if start is None else start
        if i + 1 == len(lines) or lines[i + 1] != n + 1:
            out.append(str(start) if start == n else f"{start}-{n}")
            start = None
    return ", ".join(out)


def sources(ids, index):
    docs, rows = [], defaultdict(list)
    for i in ids:
        kind, where = index.get(i, ("doc", i))  # an unknown ID is shown as-is
        if kind == "doc":
            docs.append(where)
        else:
            rows[kind].append(where)
    parts = list(dict.fromkeys(docs))
    parts += [f"{table} line{'s' if len(n) > 1 else ''} {line_ranges(n)}" for table, n in sorted(rows.items())]
    return parts


def usd_rates(folder):
    rates = defaultdict(set)
    for t in read(folder, "financial_transactions"):
        if t["fx_rate_to_usd"]:
            rates[t["currency"]].add(Decimal(t["fx_rate_to_usd"]))
    return {c: r.pop() for c, r in rates.items() if len(r) == 1} | {"USD": Decimal(1)}


def order(findings, rates):
    def key(f):
        usd = Decimal(f["estimated_impact"]) * rates[f["currency"]] if f["estimated_impact"] and f["currency"] in rates else None
        return (CONFIDENCE_ORDER.get(f["confidence"], 3), usd is None, -(usd or 0), f["finding_id"])
    return sorted(findings, key=key)


def amount(value, currency, missing="Not calculated"):
    return f"{currency} {Decimal(value):,.2f}" if value not in ("", None) else missing


def summary_rows(findings):
    rows = []
    for kind, meaning in IMPACT_TYPES.items():
        group = [f for f in findings if f["impact_type"] == kind]
        if not group:
            continue
        totals = defaultdict(Decimal)
        for f in group:
            if f["estimated_impact"]:
                totals[f["currency"]] += Decimal(f["estimated_impact"])
        money = "; ".join(f"{c} {v:,.2f}" for c, v in sorted(totals.items())) or "No amount"
        unpriced = sum(1 for f in group if not f["estimated_impact"])
        note = f" (plus {unpriced} without an amount)" if unpriced and totals else ""
        rows.append(f"<tr><td>{e(meaning.split(':')[0])}</td><td class=num>{len(group)}</td>"
                    f"<td class=num>{e(money)}{note}</td></tr>")
    return "".join(rows)


def project_rows(baseline, projects):
    out = []
    for b in baseline:
        p = projects[b["project_id"]]
        mark = " &dagger;" if b["included"] == "true" and b["data_complete"] == "false" else ""
        cell = (f"<td class=proj>{e(b['project_id'])}{mark}<br><span class=muted>{e(p['start_date'])} to "
                f"{e(p['end_date'] or 'open')}</span></td>")
        if b["included"] != "true":
            out.append(f"<tr class=excluded>{cell}<td colspan=5>Excluded: {e(b['notes'])}</td></tr>")
            continue
        margin = lambda v: f"{v}%" if v else "n/a"
        out.append(
            f"<tr>{cell}"
            f"<td class=num>{e(amount(b['planned_profit'], b['currency']))}</td><td class=num>{margin(b['planned_margin_pct'])}</td>"
            f"<td class=num>{e(amount(b['actual_profit'], b['currency']))}</td><td class=num>{margin(b['actual_margin_pct'])}</td>"
            f"<td class=num>{e(amount(b['profit_variance'], b['currency']))}</td></tr>")
    return "".join(out)


def finding_block(n, f, project, rules, index):
    r = rules[f["finding_type"]]
    srcs = "".join(f"<li>{e(s)}</li>" for s in sources(f["evidence_ids"].split(";"), index))
    period = f"{project['start_date']} to {project['end_date']}"
    return f"""
<article class=finding>
  <header><span class=rank>{n}</span><h3>{e(r['name'])}</h3>
    <span class="tag {e(f['impact_type'])}">{e(IMPACT_TYPES[f['impact_type']].split(':')[0])}</span></header>
  <p class=lead>{e(f['description'])}</p>
  <dl>
    <dt>Project and period</dt><dd>{e(f['project_id'])}, {e(period)}</dd>
    <dt>Baseline (plan or contract)</dt><dd>{e(amount(f['baseline_amount'], f['currency'], "None recorded"))}</dd>
    <dt>Observed in your records</dt><dd>{e(amount(f['observed_amount'], f['currency'], "None recorded"))}</dd>
    <dt>Estimated impact</dt><dd><strong>{e(amount(f['estimated_impact'], f['currency']))}</strong></dd>
    <dt>Calculation and assumptions</dt><dd>{e(f['calculation'])}</dd>
    <dt>Sources</dt><dd><ul>{srcs}</ul></dd>
    <dt>Possible innocent explanation</dt><dd>{e(f['alternative_explanation'])}</dd>
    <dt>Confidence</dt><dd>{e(f['confidence'].capitalize())}</dd>
    <dt>Suggested next action</dt><dd>{e(f['next_action'])}</dd>
    <dt>Your verdict</dt><dd class=verdict><span class=opt>&#9744; Confirmed</span><span class=opt>&#9744; Partly confirmed</span>
      <span class=opt>&#9744; Rejected</span><span class=opt>&#9744; Needs more evidence</span><br>
      Already known to you? <span class=opt>&#9744; Yes</span><span class=opt>&#9744; No</span><br>Notes:</dd>
  </dl>
  <p class=meta>{e(f['finding_id'])} · rule {e(f['finding_type'])} v{e(str(f['rule_version']))}</p>
</article>"""


def patterns(findings, included):
    spread = defaultdict(set)
    for f in findings:
        spread[f["finding_type"]].add(f["project_id"])
    recurring = {r: p for r, p in spread.items() if len(p) > 1}
    if not recurring:
        return (f"<p>No rule fired on more than one of the {included} audited projects. "
                f"A pattern needs the same problem on several projects.</p>")
    return "<ul>" + "".join(f"<li>{e(r)} on {len(p)} of {included} projects: {e(', '.join(sorted(p)))}</li>"
                            for r, p in sorted(recurring.items())) + "</ul>"


def gaps(baseline, log):
    items = [f"<li>{e(b['project_id'])}: {e(b['notes'])}</li>" for b in baseline
             if b["included"] != "true" or b["data_complete"] == "false"]
    levels = Counter(entry["level"] for entry in log)
    if levels["error"] or levels["warning"]:
        items.append(f"<li>Data cleaning logged {levels['error']} errors and {levels['warning']} warnings; "
                     f"see normalization_log.csv.</li>")
    return "<ul>" + "".join(items) + "</ul>" if items else "<p>No gaps found in the supplied records.</p>"


def render(agency, findings, baseline, projects, rules, index, rates, log, generated_on):
    included = sum(1 for b in baseline if b["included"] == "true")
    fx = sorted({b["fx_used"] for b in baseline if b.get("fx_used")})
    body = "".join(finding_block(n, f, projects[f["project_id"]], rules, index)
                   for n, f in enumerate(order(findings, rates), start=1))
    return f"""<!doctype html>
<html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width, initial-scale=1">
<title>Project profit audit · {e(agency['agency_id'])}</title>
<style>
:root {{ --ink:#1d2433; --muted:#5d6779; --rule:#d9dde5; --paper:#ffffff; --tint:#f4f6f9; --accent:#2f4f8f;
  --confirmed:#9c2f2f; --billable:#2e6b4f; --process:#6a5a1e; }}
* {{ box-sizing:border-box }}
body {{ margin:0; background:var(--paper); color:var(--ink); font:15px/1.55 system-ui, "Segoe UI", sans-serif; }}
main {{ max-width:860px; margin:0 auto; padding:40px 20px 64px; display:grid; gap:32px; }}
h1, h2, h3 {{ font-family:Georgia, "Times New Roman", serif; margin:0; text-wrap:balance; }}
h1 {{ font-size:30px }} h2 {{ font-size:21px; border-bottom:1px solid var(--rule); padding-bottom:6px }} h3 {{ font-size:17px }}
section {{ display:grid; gap:12px }}
.draft {{ border:1px solid var(--accent); background:var(--tint); padding:12px 14px; border-radius:4px }}
.muted, .meta {{ color:var(--muted); font-size:13px }}
table {{ border-collapse:collapse; width:100%; font-size:13.5px }}
.wide {{ overflow-x:auto }}
th, td {{ text-align:left; padding:7px 8px; border-bottom:1px solid var(--rule); vertical-align:top }}
th {{ font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted) }}
.num {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap }}
.proj {{ white-space:nowrap }} .proj .muted {{ font-size:12px }}
.opt {{ white-space:nowrap; margin-right:12px }}
tr.excluded td {{ color:var(--muted) }}
.finding {{ border:1px solid var(--rule); border-radius:4px; padding:16px; display:grid; gap:10px; break-inside:avoid }}
.finding header {{ display:flex; flex-wrap:wrap; gap:10px; align-items:baseline }}
.rank {{ font:600 13px ui-monospace, Consolas, monospace; color:var(--muted) }}
.tag {{ font-size:12px; padding:1px 8px; border:1px solid currentColor; border-radius:999px }}
.tag.confirmed_discrepancy {{ color:var(--confirmed) }} .tag.potentially_billable {{ color:var(--billable) }}
.tag.unquantified_process_issue {{ color:var(--process) }}
.lead {{ margin:0; font-size:16px }}
dl {{ display:grid; grid-template-columns:minmax(150px, 220px) 1fr; gap:6px 14px; margin:0 }}
dt {{ color:var(--muted); font-size:13px }} dd {{ margin:0; min-width:0 }}
dd ul {{ margin:0; padding-left:18px }}
.verdict {{ line-height:2 }}
@media (max-width:560px) {{ dl {{ grid-template-columns:1fr }} dt {{ margin-top:6px }} }}
@media print {{ main {{ padding:0 }} .finding {{ page-break-inside:avoid }} .wide {{ overflow:visible }} }}
</style></head>
<body><main>
<header>
  <p class=muted>Margin Autopsy · historical project audit · generated {e(generated_on)}</p>
  <h1>Project profit audit · {e(agency['agency_id'])}</h1>
  <p class=muted>{e(agency['region'])} · {e(agency['billing_model'])} · {included} projects audited,
    {len(baseline) - included} excluded</p>
</header>
<p class=draft><strong>Draft for your review.</strong> Every finding below is a candidate, not a conclusion.
  Please confirm or reject each one; only confirmed findings count. Amounts stay in their original currency,
  and different kinds of impact are never added together.</p>

<section><h2>Summary</h2>
  <div class=wide><table><thead><tr><th>Kind of impact</th><th class=num>Findings</th><th class=num>Total by currency</th></tr></thead>
  <tbody>{summary_rows(findings) or '<tr><td colspan=3>No findings.</td></tr>'}</tbody></table></div>
</section>

<section><h2>Planned vs actual profit</h2>
  <p class=muted>Revenue is the invoiced amount. Profit = revenue minus labour, subcontractor and other costs.</p>
  <div class=wide><table><thead><tr><th>Project</th><th class=num>Planned profit</th><th class=num>Planned margin</th>
  <th class=num>Actual profit</th><th class=num>Actual margin</th><th class=num>Variance</th></tr></thead>
  <tbody>{project_rows(baseline, projects)}</tbody></table></div>
  <p class=muted>&dagger; Some records are missing; see Missing records and uncertain calculations.</p>
</section>

<section><h2>Findings for review</h2>
  <p class=muted>Ordered by confidence, then by size of impact.</p>
  {body or '<p>No findings.</p>'}
</section>

<section><h2>Recurring patterns</h2>{patterns(findings, included)}</section>

<section><h2>Missing records and uncertain calculations</h2>{gaps(baseline, log)}</section>

<section><h2>Method</h2><ul>
  <li>Records were cleaned into a common format; the original files were not changed.</li>
  <li>Exchange rates used: {e('; '.join(fx) or 'none needed')}.</li>
  <li>Rules applied: {e(', '.join(f"{r} {rules[r]['name']} v{rules[r]['version']}" for r in sorted(rules)))}.</li>
  <li>Possible problems the current data cannot test (revision rounds, milestones, your reported margin,
    estimates by role, support terms) are not covered by this report.</li>
</ul></section>
</main></body></html>
"""


def generate(folder, out_dir, generated_on=None):
    folder, out_dir = Path(folder), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    generated_on = generated_on or date.today().isoformat()
    findings, rules = detect(folder), load_rules()
    baseline, projects = read(folder, "baseline"), {p["project_id"]: p for p in read(folder, "projects")}
    log = read(folder, "normalization_log") if (folder / "normalization_log.csv").exists() else []
    index, rates, written = evidence_index(folder), usd_rates(folder), []
    for agency in read(folder, "agencies"):
        aid = agency["agency_id"]
        own = lambda pid: projects[pid]["agency_id"] == aid
        page = render(agency, [f for f in findings if own(f["project_id"])],
                      [b for b in baseline if own(b["project_id"])], projects, rules, index, rates,
                      log, generated_on)
        path = out_dir / f"report_{aid}.html"
        path.write_text(page, encoding="utf-8", newline="\n")
        written.append(path)
    return written


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Write one draft audit report per agency.")
    p.add_argument("normalized_dir")
    p.add_argument("out_dir")
    a = p.parse_args()
    for path in generate(a.normalized_dir, a.out_dir):
        print(path)
