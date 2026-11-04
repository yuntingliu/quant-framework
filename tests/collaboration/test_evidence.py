from __future__ import annotations

import copy
import json
import shutil
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import pytest

from alphalab import ResultStore
from alphalab.collaboration import (
    CollaborationError,
    EvidenceArtifact,
    EvidenceStore,
    encode_json,
    sha256,
    validate_contract,
)
from apps.api.services.collaboration_evidence_service import collect_backtest_evidence

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = json.loads((ROOT / "tests/fixtures/collaboration/contracts/examples.json").read_text())


def fixture_artifacts(context=None):
    context = context or EXAMPLES["context"]
    result = []
    for descriptor in context["manifest"]["artifacts"]:
        content = context["evidence"][descriptor["artifact_id"]]["content"]
        raw = encode_json(content) if descriptor["media_type"] == "application/json" else content.encode()
        assert sha256(raw) == descriptor["sha256"]
        result.append(EvidenceArtifact(descriptor["artifact_id"], raw, descriptor["media_type"],
                                       descriptor["kind"], descriptor["source"], tuple(descriptor["locators"])))
    return result


def prepared(tmp_path):
    store = EvidenceStore(tmp_path / "evidence")
    store.register_task(EXAMPLES["messages"]["task_envelope"])
    return store


def freeze(store, *, expected=0, key="delivery-1", artifacts=None):
    return store.freeze("task-1", expected_revision=expected, delivery_key=key,
                        artifacts=artifacts or fixture_artifacts(), coverage=EXAMPLES["context"]["manifest"]["coverage"],
                        omissions=[], code_identity="fixture-code-1", environment_identity="fixture-environment-1")


def test_frozen_bytes_and_history_survive_input_changes_and_restart(tmp_path):
    store = prepared(tmp_path)
    original = fixture_artifacts()
    first = freeze(store, artifacts=original)
    reader = store.reader("task-1", [first])
    old_report = reader.read(first["manifest_id"], "report")["value"]
    changed = [item if item.artifact_id != "report" else EvidenceArtifact(
        item.artifact_id, b"A different report.\n", item.media_type, item.kind, item.source, item.locators
    ) for item in original]
    second = freeze(store, expected=1, key="delivery-2", artifacts=changed)
    assert second["sha256"] != first["sha256"]
    assert reader.read(first["manifest_id"], "report")["value"] == old_report
    reopened = EvidenceStore(store.root)
    assert reopened.reader("task-1", [first]).read(first["manifest_id"], "report")["value"] == old_report
    assert reopened.reader("task-1", [second]).read(second["manifest_id"], "report")["value"] == "A different report.\n"


def test_hashes_pin_exact_json_bytes_instead_of_reserialized_values(tmp_path):
    store = prepared(tmp_path)
    values = fixture_artifacts()
    first_value = values[2]
    raw = b'{ "net_return": -0.01, "fees":"included", "return_basis":"net", "unit":"fraction" }\n'
    values[2] = EvidenceArtifact(first_value.artifact_id, raw, first_value.media_type, first_value.kind, first_value.source, first_value.locators)
    reference = freeze(store, artifacts=values)
    result = store.reader("task-1", [reference]).read(reference["manifest_id"], "result")
    assert result["sha256"] == sha256(raw)
    assert result["sha256"] != first_value.descriptor()["sha256"]


def test_manifest_and_candidate_compare_and_swap_have_one_conflicting_winner(tmp_path):
    store = prepared(tmp_path)
    barrier = threading.Barrier(2)

    def save(index):
        barrier.wait()
        try:
            return freeze(store, key=f"delivery-{index}")
        except CollaborationError as error:
            return error.code

    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(save, [1, 2]))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert "VERSION_CONFLICT" in results
    reference = next(result for result in results if isinstance(result, dict))
    package = copy.deepcopy(EXAMPLES["messages"]["research_package"])
    package.update(revision=1, manifest=reference)
    barrier = threading.Barrier(2)

    def candidate(index):
        value = copy.deepcopy(package)
        value["assumptions"].append(f"Candidate proposal {index}")
        barrier.wait()
        try:
            return store.save_candidate(value, expected_revision=0)
        except CollaborationError as error:
            return error.code

    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(candidate, [1, 2]))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert "VERSION_CONFLICT" in results


def test_duplicate_delivery_is_idempotent_and_conflicting_content_is_rejected(tmp_path):
    store = prepared(tmp_path)
    store.register_task(EXAMPLES["messages"]["task_envelope"])
    with pytest.raises(CollaborationError) as changed_task:
        store.register_task({**EXAMPLES["messages"]["task_envelope"], "grant_id": "different-grant"})
    assert changed_task.value.code == "DUPLICATE_CONFLICT"
    first = freeze(store)
    assert freeze(store) == first
    changed = fixture_artifacts()
    changed[0] = EvidenceArtifact("source", b"Changed source text", "text/plain", "source", changed[0].source)
    with pytest.raises(CollaborationError) as error:
        freeze(store, artifacts=changed)
    assert error.value.code == "DUPLICATE_CONFLICT"
    assert freeze(store, expected=1, key="delivery-2")["revision"] == 2
    assert freeze(store) == first  # Retry does not overwrite history or advance head.


@pytest.mark.parametrize("target", ["blob", "manifest"])
def test_corruption_is_explicit_with_no_current_data_fallback(tmp_path, target):
    store = prepared(tmp_path)
    reference = freeze(store)
    reader = store.reader("task-1", [reference])
    with sqlite3.connect(store.root / "evidence.sqlite3") as connection:
        if target == "blob":
            digest = next(item for item in fixture_artifacts() if item.artifact_id == "report").descriptor()["sha256"]
            connection.execute("UPDATE blobs SET payload=? WHERE sha256=?", (b"tampered", digest))
        else:
            connection.execute("UPDATE manifests SET payload=? WHERE manifest_id=?", (b"tampered", reference["manifest_id"]))
    with pytest.raises(CollaborationError) as error:
        reader.read(reference["manifest_id"], "report")
    assert error.value.code == "INTEGRITY_ERROR"
    if target == "blob":
        with pytest.raises(CollaborationError) as replay_error:
            freeze(store)
        assert replay_error.value.code == "INTEGRITY_ERROR"


def test_failure_after_a_staged_blob_rolls_back_every_record(tmp_path):
    store = prepared(tmp_path)
    with sqlite3.connect(store.root / "evidence.sqlite3") as connection:
        connection.execute("""CREATE TRIGGER fail_second_blob BEFORE INSERT ON blobs
            WHEN (SELECT COUNT(*) FROM blobs) > 0 BEGIN SELECT RAISE(ABORT,'injected'); END""")
    with pytest.raises(CollaborationError) as error:
        freeze(store)
    assert error.value.code == "STORAGE_UNAVAILABLE"
    with sqlite3.connect(store.root / "evidence.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM blobs").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM manifests").fetchone()[0] == 0
        assert connection.execute("SELECT manifest_revision FROM tasks").fetchone()[0] == 0
        connection.execute("DROP TRIGGER fail_second_blob")
    assert freeze(store)["revision"] == 1


def test_abrupt_writer_exit_recovers_without_uncommitted_evidence(tmp_path):
    store = prepared(tmp_path)
    command = "import sqlite3,os,sys; c=sqlite3.connect(sys.argv[1]); c.execute('BEGIN IMMEDIATE'); c.execute('INSERT INTO blobs VALUES (?,?)',('uncommitted',b'partial')); os._exit(17)"
    completed = subprocess.run([sys.executable, "-c", command, str(store.root / "evidence.sqlite3")], timeout=10)
    assert completed.returncode == 17
    reopened = EvidenceStore(store.root)
    with sqlite3.connect(reopened.root / "evidence.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM blobs").fetchone()[0] == 0
    assert freeze(reopened)["revision"] == 1


def test_task_grants_paths_and_locators_cannot_escape_the_reader(tmp_path):
    store = prepared(tmp_path)
    reference = freeze(store)
    other = {**EXAMPLES["messages"]["task_envelope"], "task_id": "task-2"}
    store.register_task(other)
    for task_id, grants in [("task-2", [reference]), ("task-1", [{**reference, "sha256": "f" * 64}]),
                            ("task-1", [{**reference, "revision": True}])]:
        with pytest.raises(CollaborationError) as error:
            store.reader(task_id, grants)
        assert error.value.code == "ACCESS_DENIED"
    reader = store.reader("task-1", [reference])
    for manifest_id, artifact_id, locator in [
        ("unregistered", "report", ""), (reference["manifest_id"], "../private", ""),
        (reference["manifest_id"], "report", "lines:1:99"), (reference["manifest_id"], "data", "/rows/99"),
    ]:
        with pytest.raises(CollaborationError):
            reader.read(manifest_id, artifact_id, locator=locator)
    assert reader.read(reference["manifest_id"], "data", locator="/rows", offset=0, limit=1)["value"][0]["return_value"] == "0.10"
    with pytest.raises(CollaborationError) as error:
        reader.read(reference["manifest_id"], "data", locator="/rows", offset=0, limit=1001)
    assert error.value.code == "EVIDENCE_LIMIT"


def test_oversized_reads_fail_while_bounded_array_pages_succeed(tmp_path):
    store = prepared(tmp_path)
    values = fixture_artifacts()
    original = next(item for item in values if item.artifact_id == "data")
    large = EvidenceArtifact.json("data", {"rows": [{"return_value": "0.10", "note": "x" * 1000}] * 200},
                                  kind="data", source=original.source, locators=("", "/rows"))
    values = [large if item.artifact_id == "data" else item for item in values]
    reference = freeze(store, artifacts=values)
    reader = store.reader("task-1", [reference])
    with pytest.raises(CollaborationError) as error:
        reader.read(reference["manifest_id"], "data")
    assert error.value.code == "EVIDENCE_LIMIT"
    assert len(reader.read(reference["manifest_id"], "data", locator="/rows", offset=10, limit=2)["value"]) == 2


def test_opaque_original_is_integrity_checked_but_requires_a_separate_reviewable_view(tmp_path):
    store = prepared(tmp_path)
    values = fixture_artifacts()
    values.append(EvidenceArtifact("original", b"original-binary-bytes", "application/octet-stream", "data", values[0].source))
    reference = freeze(store, artifacts=values)
    reader = store.reader("task-1", [reference])
    assert "original" not in reader.context(reference["manifest_id"])["evidence"]
    with pytest.raises(CollaborationError):
        reader.read(reference["manifest_id"], "original")
    assert reader.read(reference["manifest_id"], "result", locator="/net_return")["value"] == -0.01


def test_candidate_bytes_are_archived_and_an_old_review_cannot_approve_a_new_revision(tmp_path):
    store = prepared(tmp_path)
    manifest = freeze(store)
    package = copy.deepcopy(EXAMPLES["messages"]["research_package"])
    package.update(revision=1.0, manifest=manifest)  # JSON Schema integer values may be serialized as 1.0.
    first = store.save_candidate(package, expected_revision=0)
    assert type(first["revision"]) is int
    assert store.candidate_context("task-1", first)["candidate"] == first
    assert store.save_candidate(package, expected_revision=0) == first
    package["revision"] = 2
    package["limitations"].append("Additional declared limitation.")
    second = store.save_candidate(package, expected_revision=1)
    assert store.candidate("task-1", first)["revision"] == 1
    context = store.reader("task-1", [manifest]).context(manifest["manifest_id"])
    context.update(candidate=second, research_package=package, delegation={**EXAMPLES["messages"]["delegation_request"], "candidate": second, "manifest": manifest})
    review = {**EXAMPLES["messages"]["review_result"], "candidate": first, "manifest": manifest}
    with pytest.raises(CollaborationError) as error:
        validate_contract("review_result", review, context)
    assert error.value.code == "STALE_CANDIDATE"


def test_database_symlink_outside_store_is_rejected(tmp_path):
    root = tmp_path / "evidence"
    root.mkdir()
    outside = tmp_path / "other.sqlite3"
    outside.write_bytes(b"not-a-database")
    try:
        (root / "evidence.sqlite3").symlink_to(outside)
    except OSError:
        pytest.skip("Symlink creation is unavailable on this platform.")
    with pytest.raises(CollaborationError) as error:
        EvidenceStore(root)
    assert error.value.code == "ACCESS_DENIED"
    assert outside.read_bytes() == b"not-a-database"


def saved_result(tmp_path):
    store = ResultStore(tmp_path / "business.sqlite3")
    source = "raise RuntimeError('must never execute during evidence capture')\n"
    marker = tmp_path / "executed"
    source += f"open({str(marker)!r}, 'w').write('bad')\n"
    store.save_backtest(pd.Series([0.1, -0.1], index=pd.to_datetime(["2024-01-02", "2024-01-03"])),
                        {"total_return": -0.01}, backtest_id="result-1", strategy_source=source,
                        strategy_revision=1, strategy_source_sha256=sha256(source.encode()),
                        validation_source=source, validation_revision=1, validation_source_sha256=sha256(source.encode()),
                        validation_output={"performance": {"total_return": -0.01}})
    return store, marker


def test_existing_run_collection_never_executes_source_or_invents_old_input_bytes(tmp_path):
    business, marker = saved_result(tmp_path)
    try:
        outputs = EXAMPLES["messages"]["final_output_request"]["outputs"]
        collected = collect_backtest_evidence(business, "result-1", request_id="request-1", report_markdown="Frozen result.", outputs=outputs)
        assert collected["coverage"] == {"data": "unavailable", "sources": "complete", "results": "complete", "pit": "unavailable"}
        assert any("current data was not substituted" in text for text in collected["omissions"])
        archive = prepared(tmp_path)
        reference = archive.freeze("task-1", expected_revision=0, delivery_key="backtest-1", **collected)
        reader = archive.reader("task-1", [reference])
        assert reader.read(reference["manifest_id"], "return-series", locator="/rows", offset=0, limit=2)["value"][0]["strategy"] == 0.1
        assert not marker.exists()
        assert business.export_backtest_evidence("result-1")["record"]["strategy_revision"] == 1
    finally:
        business.close()


def test_export_is_consistent_under_same_result_id_replay_and_respects_row_bounds(tmp_path):
    business, _ = saved_result(tmp_path)
    try:
        with pytest.raises(ValueError, match="row limit"):
            business.export_backtest_evidence("result-1", max_rows=1)
        stop = threading.Event()
        business.save_backtest(pd.Series([0.0], index=pd.to_datetime(["2024-01-02"])),
                               {"total_return": 0.0}, backtest_id="replayed")

        def writer():
            try:
                for i in range(15):
                    value = i / 100
                    business.save_backtest(pd.Series([value], index=pd.to_datetime(["2024-01-02"])),
                                           {"total_return": value}, backtest_id="replayed")
            finally:
                stop.set()

        with ThreadPoolExecutor(1) as executor:
            future = executor.submit(writer)
            checked = 0
            while not stop.is_set() or checked < 10:
                exported = business.export_backtest_evidence("replayed")
                assert exported["record"]["total_return"] == exported["returns"][0]["strategy"]
                checked += 1
            future.result(timeout=5)
    finally:
        business.close()


@pytest.mark.skipif(not shutil.which("node") or not (ROOT / "build/conexus/node_modules/ajv").exists(), reason="Build the bundled Node runtime first.")
def test_node_client_uses_read_only_stdio_with_fixed_scope_and_no_http_or_model_key(tmp_path):
    store = prepared(tmp_path)
    reference = freeze(store)
    client = (ROOT / "integrations/conexus/collaboration/evidence-client.mjs").as_uri()
    program = """
import { createEvidenceClient } from READER;
const [pythonExecutable, storeRoot, ref] = process.argv.slice(1);
const manifest = JSON.parse(ref);
const reader = createEvidenceClient({pythonExecutable, storeRoot, taskId:'task-1', manifests:[manifest]});
const value = await reader({operation:'read',manifest_id:manifest.manifest_id,artifact_id:'report'});
let denied = false;
try { await reader({operation:'read',manifest_id:manifest.manifest_id,artifact_id:'report',task_id:'task-2'}); }
catch(error) { denied = error.code === 'INVALID_CONTRACT'; }
console.log(JSON.stringify({value,denied}));
""".replace("READER", json.dumps(client))
    before = (store.root / "evidence.sqlite3").read_bytes()
    completed = subprocess.run([shutil.which("node"), "--input-type=module", "-e", program, sys.executable,
                                str(store.root), json.dumps(reference)], cwd=ROOT, capture_output=True, text=True, timeout=40, check=True)
    result = json.loads(completed.stdout)
    assert result["denied"] and "-1%" in result["value"]["value"]
    assert (store.root / "evidence.sqlite3").read_bytes() == before
