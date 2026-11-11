// Runs the audit engine (src/*.py) inside Pyodide, so the analyst tool processes
// agency files in the browser. The Python code is used unchanged; this file only
// copies it into Pyodide's in-memory file system and calls it.

const ENGINE_FILES = [
  "src/normalize.py", "src/reconcile.py", "src/detect_variances.py", "src/generate_report.py", "src/rules.toml",
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
// reconcile and the rules, writing baseline.csv and findings.csv). Returns the
// reports ({name, html}) and the counts the CLI prints after each step.
export function runAudit(pyodide, rawDir, normalizedDir, reportDir, generatedOn) {
  const run = pyodide.runPython(`
from src.normalize import normalize
from src.generate_report import generate
from src.reconcile import read

def run(raw, normalized, reports, generated_on):
    log = normalize(raw, normalized)
    paths = [str(p) for p in generate(normalized, reports, generated_on)]
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

// Audits a folder on disk holding the agency's CSVs (a FileSystemDirectoryHandle with
// read-write permission). Results are written to normalized/ and reports/ inside it;
// the agency's own files are never modified.
export async function auditFolder(pyodide, dir, generatedOn) {
  if (["normalized", "reports"].includes(dir.name))
    throw new Error(`"${dir.name}" holds results from an earlier run. Pick the agency folder that contains it.`);
  const mount = "/agency";
  const nativefs = await pyodide.mountNativeFS(mount, dir);
  try {
    if (!pyodide.FS.analyzePath(`${mount}/projects.csv`).exists)
      throw new Error(`"${dir.name}" has no projects.csv. Pick the folder that holds the agency's CSV files.`);
    const result = runAudit(pyodide, mount, `${mount}/normalized`, `${mount}/reports`, generatedOn);
    await nativefs.syncfs();  // write normalized/ and reports/ back to disk
    return result;
  } finally {
    pyodide.FS.unmount(mount);
  }
}
