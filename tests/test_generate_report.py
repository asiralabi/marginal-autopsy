import re
import shutil
from pathlib import Path

from src.generate_report import generate

SYNTHETIC = Path(__file__).resolve().parents[1] / "data" / "synthetic"
TEN_PARTS = ["Project and period", "Baseline (plan or contract)", "Observed in your records", "Estimated impact",
             "Calculation and assumptions", "Sources", "Possible innocent explanation", "Confidence",
             "Suggested next action", "Your verdict"]  # with the lead description: protocol §5


def build(tmp_path):
    shutil.copytree(SYNTHETIC, tmp_path / "data")
    paths = generate(tmp_path / "data", tmp_path / "reports", generated_on="2026-10-15")
    return {p.stem: p.read_text(encoding="utf-8") for p in paths}


def findings_in(page):
    return re.findall(r"<article class=finding>.*?</article>", page, flags=re.S)


def test_one_report_per_agency_with_only_its_own_findings(tmp_path):
    pages = build(tmp_path)
    assert set(pages) == {"report_AG01", "report_AG02"}
    assert len(findings_in(pages["report_AG01"])) == 4 and "P-AG02" not in pages["report_AG01"]
    assert len(findings_in(pages["report_AG02"])) == 3 and "P-AG01" not in pages["report_AG02"]


def test_every_finding_shows_all_ten_parts(tmp_path):
    for page in build(tmp_path).values():
        for block in findings_in(page):
            assert all(label in block for label in TEN_PARTS)
            assert "class=lead" in block and "rule F" in block


def test_totals_are_split_by_impact_type_and_currency(tmp_path):
    pages = build(tmp_path)
    ag01 = pages["report_AG01"]
    assert re.search(r"Confirmed discrepancy</td><td class=num>1</td><td class=num>USD 1,170.00", ag01)
    assert re.search(r"Potentially billable</td><td class=num>1</td><td class=num>USD 720.00", ag01)
    assert re.search(r"Process issue</td><td class=num>2</td><td class=num>No amount", ag01)
    # 29,380.01 + 8,000 + 3,500: same type and currency, so they may be added
    assert re.search(r"Confirmed discrepancy</td><td class=num>3</td><td class=num>AED 40,880.01",
                     pages["report_AG02"])


def test_excluded_project_and_data_gaps_are_shown(tmp_path):
    pages = build(tmp_path)
    assert "Excluded: project still open" in pages["report_AG02"]
    assert "30h have no cost rate" in pages["report_AG01"]


def test_sources_point_to_files_and_lines(tmp_path):
    page = build(tmp_path)["report_AG02"]
    assert "D-AG02-03-05.pdf (invoice, p.1)" in page
    assert "financial_transactions.csv lines 27-28" in page


def test_output_is_deterministic(tmp_path):
    first = build(tmp_path / "a")
    assert build(tmp_path / "b") == first
