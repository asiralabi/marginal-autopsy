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
// reconcile and the rules, writing baseline.csv and findings.csv). Returns report paths.
export function runAudit(pyodide, rawDir, normalizedDir, reportDir, generatedOn) {
  const run = pyodide.runPython(`
from src.normalize import normalize
from src.generate_report import generate

def run(raw, normalized, reports, generated_on):
    normalize(raw, normalized)
    return [str(p) for p in generate(normalized, reports, generated_on)]
run`);
  return run(rawDir, normalizedDir, reportDir, generatedOn).toJs();
}
