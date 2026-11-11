from decimal import Decimal
from pathlib import Path

import pytest

from src.detect_variances import detect
from src.normalize import normalize
from src.review import VERDICTS, confirmed_totals, load, record, reviewed

SYNTHETIC = Path(__file__).resolve().parents[1] / "data" / "synthetic"


@pytest.fixture
def folder(tmp_path):
    normalize(SYNTHETIC, tmp_path)
    return tmp_path


def test_finding_ids_are_unique_and_the_same_on_every_run(folder):
    first = [f["finding_id"] for f in detect(folder)]
    assert len(set(first)) == len(first)
    assert [f["finding_id"] for f in detect(folder)] == first


def test_verdicts_survive_a_rerun_and_attach_to_their_findings(folder):
    record(folder, [{"finding_id": "F1:P-AG01-02:project", "analyst_decision": "keep", "customer_verdict": "confirmed",
                     "agency_comment": "PM logged support hours here"}], reviewer="AG01-CEO", today="2026-11-11")
    before = (folder / VERDICTS).read_bytes()
    findings = reviewed(detect(folder), load(folder))       # the rules run again
    assert (folder / VERDICTS).read_bytes() == before
    f1 = next(f for f in findings if f["finding_id"] == "F1:P-AG01-02:project")
    assert (f1["customer_verdict"], f1["reviewer_id"], f1["agency_comment"]) == ("confirmed", "AG01-CEO", "PM logged support hours here")
    assert {f["customer_verdict"] for f in findings if f is not f1} == {"unreviewed"}


def test_a_changed_verdict_is_stamped_and_other_edits_keep_the_stamp(folder):
    fid = "F8:P-AG02-03:INV-1302"
    record(folder, [{"finding_id": fid, "customer_verdict": "needs_more_evidence"}], "AG02-FIN", "2026-11-11")
    record(folder, [{"finding_id": fid, "agency_comment": "found the WHT certificate"}], "someone-else", "2026-11-12")
    assert (load(folder)[fid]["reviewer_id"], load(folder)[fid]["reviewed_on"]) == ("AG02-FIN", "2026-11-11")
    record(folder, [{"finding_id": fid, "customer_verdict": "rejected"}], "AG02-CEO", "2026-11-13")
    row = load(folder)[fid]
    assert (row["customer_verdict"], row["reviewer_id"], row["reviewed_on"], row["agency_comment"]) == \
        ("rejected", "AG02-CEO", "2026-11-13", "found the WHT certificate")


def test_verdicts_for_findings_no_longer_produced_are_kept(folder):
    record(folder, [{"finding_id": "F1:P-GONE:project", "customer_verdict": "confirmed"}])
    record(folder, [{"finding_id": "F1:P-AG01-02:project", "customer_verdict": "rejected"}])
    assert set(load(folder)) == {"F1:P-GONE:project", "F1:P-AG01-02:project"}


def test_bad_values_are_refused_and_nothing_is_written(folder):
    with pytest.raises(ValueError, match="customer_verdict 'Confirmed' is not one of"):
        record(folder, [{"finding_id": "x", "customer_verdict": "Confirmed"}])  # metrics count exact values only
    with pytest.raises(ValueError, match="agreed amount 'about 500' is not a number"):
        record(folder, [{"finding_id": "x", "agreed_amount": "about 500"}])
    assert not (folder / VERDICTS).exists()
    assert record(folder, [{"finding_id": "x", "agreed_amount": "1,200.50"}])["x"]["agreed_amount"] == "1200.50"


def finding(fid, verdict, impact="100.00", agreed="", decision="", kind="confirmed_discrepancy", currency="USD"):
    return {"finding_id": fid, "customer_verdict": verdict, "estimated_impact": impact, "agreed_amount": agreed,
            "analyst_decision": decision, "impact_type": kind, "currency": currency}


def test_only_confirmed_amounts_count_and_kinds_and_currencies_stay_apart():
    totals = confirmed_totals([
        finding("a", "confirmed"),                                   # counts the estimate: 100
        finding("b", "confirmed", agreed="40"),                      # the agreed amount wins: 40
        finding("c", "partially_confirmed"),                         # no agreed amount: nothing
        finding("d", "partially_confirmed", agreed="25.50"),         # counts 25.50
        finding("e", "rejected"), finding("f", "needs_more_evidence"), finding("g", "unreviewed"),
        finding("h", "confirmed", decision="drop"),                  # dropped before review: never
        finding("i", "confirmed", impact=""),                        # a process issue with no amount
        finding("j", "confirmed", kind="potentially_billable"),
        finding("k", "confirmed", currency="AED"),
    ])
    assert totals == {"confirmed_discrepancy": {"USD": Decimal("165.50"), "AED": Decimal("100.00")},
                      "potentially_billable": {"USD": Decimal("100.00")}}
