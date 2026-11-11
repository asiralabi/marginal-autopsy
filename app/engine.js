// Runs the audit engine (src/*.py) inside Pyodide, so the analyst tool processes
// agency files in the browser. The Python code is used unchanged; this file only
// copies it into Pyodide's in-memory file system and calls it.

const ENGINE_FILES = [
  "src/normalize.py", "src/reconcile.py", "src/detect_variances.py", "src/generate_report.py", "src/review.py",
  "src/rules.toml",
  ...["agencies", "projects", "scope_items", "time_entries", "financial_transactions", "evidence_documents",
      "audit_findings"].map(t => `schemas/${t}.csv`),
];

// readBytes(path) returns a Uint8Array for a path relative to the repository root.
// Returns the input table names the engine expects (normalize.TABLES).
export async function startEngine(pyodide, readBytes) {
  for (const path of ENGINE_FILES) {
    const target = `/engine/${path}`;
    pyodide.FS.mkdirTree(target.slice(0, target.lastIndexOf("/")));
    pyodide.FS.writeFile(target, await readBytes(path));
  }
  pyodide.runPython("import sys; sys.path.insert(0, '/engine')");
  return pyodide.runPython("from src.normalize import TABLES; TABLES").toJs();
}

// The README's command sequence: normalize, then generate_report (which runs
// reconcile and the rules, writing baseline.csv and findings.csv), with the
// verdicts.csv beside the source files if there is one. Returns the
// reports ({name, html}) and the counts the CLI prints after each step.
export function runAudit(pyodide, rawDir, normalizedDir, reportDir, generatedOn) {
  const run = pyodide.runPython(`
from src.normalize import normalize
from src.generate_report import generate
from src.reconcile import read
from src.review import load

def run(raw, normalized, reports, generated_on):
    log = normalize(raw, normalized)
    paths = [str(p) for p in generate(normalized, reports, generated_on, load(raw))]
    baseline = read(normalized, "baseline")
    return dict(reports=paths, problems=[e for e in log if e["level"] != "info"],
                changes=sum(e["level"] == "info" for e in log), projects=len(baseline),
                excluded=sum(b["included"] == "false" for b in baseline),
                incomplete=sum(b.get("data_complete") == "false" for b in baseline),
                findings=len(read(normalized, "findings")))
run`);
  const proxy = run(rawDir, normalizedDir, reportDir, generatedOn);
  const result = proxy.toJs({ dict_converter: Object.fromEntries });
  proxy.destroy();
  result.reports = result.reports.map(p => ({ name: p.split("/").pop(), html: pyodide.FS.readFile(p, { encoding: "utf8" }) }));
  return result;
}

// What column matching needs from a folder on disk: the header row of each CSV,
// the saved mapping.json (or null), each table's schema columns, and the date columns.
export async function inspectFolder(pyodide, dir) {
  if (["normalized", "reports"].includes(dir.name))
    throw new Error(`"${dir.name}" holds results from an earlier run. Pick the agency folder that contains it.`);
  const mount = "/agency";
  await pyodide.mountNativeFS(mount, dir);
  try {
    const inspect = pyodide.runPython(`
from pathlib import Path
from src.normalize import DATES, MAPPING, TABLES, headers, schema_columns

def inspect(folder):
    saved = Path(folder) / MAPPING
    return dict(files=headers(folder), mapping=saved.read_text(encoding="utf-8") if saved.exists() else None,
                schemas={t: schema_columns(t) for t in TABLES}, dates=sorted(DATES))
inspect`);
    const proxy = inspect(mount);
    const info = proxy.toJs({ dict_converter: Object.fromEntries });
    proxy.destroy();
    inspect.destroy();
    info.mapping = info.mapping == null ? null : JSON.parse(info.mapping);  // Python None arrives as undefined
    return info;
  } finally {
    pyodide.FS.unmount(mount);
  }
}

// Audits a folder on disk holding the agency's CSVs (a FileSystemDirectoryHandle with
// read-write permission), using its mapping.json if there is one. Results are written
// to normalized/ and reports/ inside it; the agency's own files are never modified.
export async function auditFolder(pyodide, dir, generatedOn) {
  const mount = "/agency";
  const nativefs = await pyodide.mountNativeFS(mount, dir);
  try {
    const result = runAudit(pyodide, mount, `${mount}/normalized`, `${mount}/reports`, generatedOn);
    await nativefs.syncfs();  // write normalized/ and reports/ back to disk
    return result;
  } finally {
    pyodide.FS.unmount(mount);
  }
}

// The review of an audited folder: its findings in report order with their verdicts,
// confirmed totals by impact type, and how many saved verdicts match no current
// finding. With updates, first merges them into the folder's verdicts.csv.
export async function reviewFolder(pyodide, dir, updates = null, reviewer = "", today = "") {
  const mount = "/agency";
  const nativefs = await pyodide.mountNativeFS(mount, dir);
  try {
    const review = pyodide.runPython(`
from pathlib import Path
from src.detect_variances import load_rules
from src.generate_report import IMPACT_TYPES, amount, order, usd_rates
from src.reconcile import read
from src.review import confirmed_totals, load, record, reviewed

def review(folder, updates, reviewer, today):
    normalized = Path(folder) / "normalized"
    if not (normalized / "findings.csv").exists():
        raise ValueError("Run the audit on this folder before reviewing it.")
    if updates:
        record(folder, updates, reviewer, today)
    verdicts, rules = load(folder), load_rules()
    label = lambda kind: IMPACT_TYPES[kind].split(":")[0]
    findings = reviewed(order(read(normalized, "findings"), usd_rates(normalized)), verdicts)
    for f in findings:
        f["rule_name"], f["impact_label"] = rules[f["finding_type"]]["name"], label(f["impact_type"])
        f["impact_text"] = amount(f["estimated_impact"], f["currency"], "no amount")
    totals = {label(k): {c: f"{v:,.2f}" for c, v in t.items()} for k, t in confirmed_totals(findings).items()}
    return dict(findings=findings, totals=totals, orphans=len(set(verdicts) - {f["finding_id"] for f in findings}))
review`);
    const proxy = review(mount, updates && pyodide.toPy(updates), reviewer, today);
    const result = proxy.toJs({ dict_converter: Object.fromEntries });
    proxy.destroy();
    review.destroy();
    if (updates) await nativefs.syncfs();  // write verdicts.csv back to disk
    return result;
  } finally {
    pyodide.FS.unmount(mount);
  }
}
