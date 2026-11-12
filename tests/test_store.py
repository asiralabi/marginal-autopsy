"""The audit store (src/store.py) against a real Postgres. Skipped when none is reachable:
start one with the docker command in db/README.md, or set DATABASE_URL."""
import csv
from decimal import Decimal
from pathlib import Path

import pytest

psycopg2 = pytest.importorskip("psycopg2")

from src import store
from src.generate_report import generate
from src.normalize import TABLES, normalize
from src.review import record
from test_normalize import one_agency


@pytest.fixture(scope="module")
def conn():
    try:
        c = store.connect()
    except psycopg2.OperationalError as e:
        pytest.skip(f"no Postgres at DATABASE_URL: {e}".strip())
    with c, c.cursor() as cur:
        cur.execute("drop schema if exists audit cascade")  # a clean store for this test module
    store.migrate(c)
    yield c
    c.close()


def write_folder(tmp_path, agency_id):
    """An audited one-agency folder, laid out as the analyst tool leaves it."""
    folder = tmp_path / agency_id
    folder.mkdir()
    for table, rows in one_agency(agency_id).items():
        with open(folder / f"{table}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
    normalize(folder, folder / "normalized")
    generate(folder / "normalized", folder / "reports", "2026-11-12")
    return folder


def count(conn, table, where="true", params=()):
    with conn.cursor() as cur:
        cur.execute(f"select count(*) from audit.{table} where {where}", params)
        return cur.fetchone()[0]


def csv_rows(folder, table):
    with open(folder / "normalized" / f"{table}.csv", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_migrate_is_idempotent(conn):
    assert store.migrate(conn) == []
    with conn.cursor() as cur:
        cur.execute("select table_name from information_schema.tables where table_schema = 'audit' order by 1")
        tables = {t for (t,) in cur.fetchall()}
    assert {"agencies", "runs", "findings", "verdict_events", "verdicts_current", "findings_current"} <= tables


def test_load_stores_a_complete_typed_snapshot(conn, tmp_path):
    folder = write_folder(tmp_path, "AG01")
    result = store.load(conn, folder, "2026-11-12")
    run_id = result["run_id"]
    assert result["agency_id"] == "AG01" and result["verdict_events"] == 0
    for table in store.SNAPSHOT_TABLES:
        assert count(conn, table, "run_id = %s", (run_id,)) == len(csv_rows(folder, table)) == result[table]
    assert count(conn, "agencies", "agency_id = 'AG01'") == 1

    with conn.cursor() as cur:
        cur.execute("select rule_versions, generated_on, source_folder from audit.runs where run_id = %s", (run_id,))
        rule_versions, generated_on, source = cur.fetchone()
        assert rule_versions["F1"] == 1 and str(generated_on) == "2026-11-12" and source == str(folder)
        # blanks are NULL and money is exact, not float
        baseline = csv_rows(folder, "baseline")
        cur.execute("select included, planned_profit from audit.baseline where run_id = %s and project_id = %s",
                    (run_id, baseline[0]["project_id"]))
        included, planned_profit = cur.fetchone()
        assert included is (baseline[0]["included"] == "true")
        assert planned_profit == (Decimal(baseline[0]["planned_profit"]) if baseline[0]["planned_profit"] else None)
        cur.execute("select count(*) from audit.time_entries where run_id = %s and hourly_cost_rate is null", (run_id,))
        assert cur.fetchone()[0] == sum(r["hourly_cost_rate"] == "" for r in csv_rows(folder, "time_entries"))
        # evidence ids become an array that joins back to the evidence rows
        cur.execute("""select count(*) from audit.findings f
                       join audit.evidence_documents d on d.run_id = f.run_id and d.document_id = any(f.evidence_ids)
                       where f.run_id = %s""", (run_id,))
        assert cur.fetchone()[0] > 0


def test_each_load_is_a_new_run_and_the_ledger_does_not_repeat(conn, tmp_path):
    folder = write_folder(tmp_path, "AG01")
    first = store.load(conn, folder)["run_id"]
    second = store.load(conn, folder)["run_id"]
    assert second > first
    assert count(conn, "findings", "run_id = %s", (second,)) == count(conn, "findings", "run_id = %s", (first,))
    with conn.cursor() as cur:
        cur.execute("select run_id from audit.latest_runs where agency_id = 'AG01'")
        assert cur.fetchone()[0] == second


def test_verdict_ledger_keeps_history_and_the_current_view_shows_the_latest(conn, tmp_path):
    folder = write_folder(tmp_path, "AG01")
    fid = csv_rows(folder, "findings")[0]["finding_id"]
    record(folder, [dict(finding_id=fid, customer_verdict="confirmed", agreed_amount="1,200.50")], "CEO", "2026-11-12")
    assert store.load(conn, folder)["verdict_events"] == 1
    assert store.load(conn, folder)["verdict_events"] == 0  # unchanged verdicts add nothing
    record(folder, [dict(finding_id=fid, customer_verdict="rejected", agency_comment="was goodwill")], "CEO", "2026-11-13")
    assert store.load(conn, folder)["verdict_events"] == 1
    with conn.cursor() as cur:
        cur.execute("select customer_verdict, agreed_amount, reviewer_id, reviewed_on from audit.verdict_events "
                    "where agency_id = 'AG01' and finding_id = %s order by event_id", (fid,))
        history = cur.fetchall()
        assert [h[0] for h in history] == ["confirmed", "rejected"]
        assert history[0][1] == Decimal("1200.50") and history[0][2] == "CEO" and str(history[0][3]) == "2026-11-12"
        cur.execute("select customer_verdict, agency_comment from audit.verdicts_current "
                    "where agency_id = 'AG01' and finding_id = %s", (fid,))
        assert cur.fetchone() == ("rejected", "was goodwill")
        cur.execute("select customer_verdict from audit.findings_current where agency_id = 'AG01' and finding_id = %s", (fid,))
        assert cur.fetchone() == ("rejected",)
        cur.execute("select count(*) from audit.findings_current where agency_id = 'AG01' and customer_verdict = 'unreviewed'")
        assert cur.fetchone()[0] == len(csv_rows(folder, "findings")) - 1


def test_bad_verdict_values_are_refused_by_the_database(conn):
    with pytest.raises(psycopg2.errors.CheckViolation):
        with conn, conn.cursor() as cur:
            cur.execute("insert into audit.verdict_events (agency_id, finding_id, customer_verdict) "
                        "values ('AG01', 'F1:x', 'maybe')")


def test_delete_agency_removes_everything_of_that_agency_only(conn, tmp_path):
    store.load(conn, write_folder(tmp_path, "AG01"))
    store.load(conn, write_folder(tmp_path, "AG02"))
    assert store.delete_agency(conn, "AG01") == 1
    assert store.delete_agency(conn, "AG01") == 0
    for table in ["agencies", "runs", "verdict_events"]:
        assert count(conn, table, "agency_id = 'AG01'") == 0
    for table in store.SNAPSHOT_TABLES:
        assert count(conn, table, "run_id not in (select run_id from audit.runs)") == 0
    assert count(conn, "runs", "agency_id = 'AG02'") >= 1
    assert count(conn, "findings", "run_id in (select run_id from audit.runs where agency_id = 'AG02')") > 0


def test_command_line_runs_each_command(conn, tmp_path, monkeypatch, capsys):
    folder = write_folder(tmp_path, "AG02")
    for argv in (["migrate"], ["load", str(folder), "--generated-on", "2026-11-12"], ["delete-agency", "AG02"]):
        monkeypatch.setattr("sys.argv", ["store"] + argv)
        store.main()
    out = capsys.readouterr().out
    assert "applied 0 migration(s)" in out and "run " in out and "deleted 1 agency" in out
    assert count(conn, "runs", "agency_id = 'AG02'") == 0


def test_load_refuses_unaudited_and_multi_agency_folders(conn, tmp_path):
    with pytest.raises(ValueError, match="Run the audit"):
        store.load(conn, tmp_path)
    folder = write_folder(tmp_path, "AG01")
    with open(folder / "normalized" / "agencies.csv", "a", encoding="utf-8") as f:
        f.write("AG09,BD,software,10-49,web,USD,fixed,role_rate,granted\n")
    with pytest.raises(ValueError, match="one agency folder at a time"):
        store.load(conn, folder)
