from __future__ import annotations

import json
import multiprocessing
import queue
import pytest
import simulacra.workplace.source_staging as source_staging
from simulacra.demo import sources as source_module
from simulacra.demo import runs
from simulacra.demo.identity import ensure_bootstrap
from simulacra.demo.tenants import default_tenant_id

from simulacra.workplace.source_staging import SourceStaging


def _hold_publication_lock(root: str, ready, release) -> None:
    staging = SourceStaging(root)
    with staging._publication_locked():
        ready.set()
        release.wait(10)


def _gc_in_another_process(root: str, results) -> None:
    try:
        results.put(SourceStaging(root).gc_orphans())
    except Exception as exc:  # pragma: no cover - assertion happens in parent
        results.put(repr(exc))


def test_staged_sources_are_tenant_actor_scoped_immutable_and_linked_to_reservation(tmp_path):
    staging = SourceStaging(tmp_path)
    first = staging.stage(tenant_id="tenant_one", actor_id="human_one", client_request_id="source_1",
                          filename="../evidence.csv", media_type="text/csv", data=b"a,b\n1,2\n")
    assert first.public()["filename"] == "evidence.csv"
    assert staging.stage(tenant_id="tenant_one", actor_id="human_one", client_request_id="source_1",
                         filename="evidence.csv", media_type="text/csv", data=b"a,b\n1,2\n") == first
    with pytest.raises(ValueError, match="idempotency_mismatch"):
        staging.stage(tenant_id="tenant_one", actor_id="human_one", client_request_id="source_1",
                      filename="evidence.csv", media_type="text/csv", data=b"a,b\n9,9\n")
    with pytest.raises(KeyError):
        staging.get(tenant_id="tenant_one", actor_id="human_two", source_ref=first.source_ref)


def test_workspace_bootstrap_staged_sources_replay_mismatch_and_crash_recovery(tmp_path):
    staging = SourceStaging(tmp_path)
    record = staging.stage(tenant_id="tenant_one", actor_id="human_one", client_request_id="source_1",
                           filename="source.txt", media_type="text/plain", data=b"immutable")
    assert staging.blob_bytes(record) == b"immutable"
    assert (tmp_path / "blobs" / record.canonical_content_sha256[:2] / record.canonical_content_sha256).is_file()


def test_blob_first_publication_never_exposes_missing_blob_and_gc_reclaims_orphan(tmp_path):
    staging = SourceStaging(tmp_path)
    record = staging.stage(tenant_id="tenant_one", actor_id="human_one", client_request_id="source_1",
                           filename="source.txt", media_type="text/plain", data=b"immutable")
    orphan = tmp_path / "blobs" / "aa" / ("a" * 64)
    orphan.parent.mkdir(parents=True); orphan.write_bytes(b"orphan")
    assert staging.gc_orphans() == 1
    assert staging.blob_bytes(record) == b"immutable"


def test_publication_and_orphan_gc_share_a_cross_process_barrier(tmp_path):
    """GC cannot race a source publication/read across deployment processes."""
    staging = SourceStaging(tmp_path)
    record = staging.stage(tenant_id="tenant_one", actor_id="human_one", client_request_id="source_1",
                           filename="source.txt", media_type="text/plain", data=b"immutable")
    context = multiprocessing.get_context("fork")
    ready, release, results = context.Event(), context.Event(), context.Queue()
    holder = context.Process(target=_hold_publication_lock, args=(str(tmp_path), ready, release))
    collector = context.Process(target=_gc_in_another_process, args=(str(tmp_path), results))
    holder.start(); assert ready.wait(5)
    collector.start()
    # The collector is blocked until source publication/read completes, rather
    # than observing a record/blob pair at two different moments.
    with pytest.raises(queue.Empty):
        results.get(timeout=0.25)
    release.set(); holder.join(10); collector.join(10)
    assert holder.exitcode == collector.exitcode == 0
    assert results.get(timeout=2) == 0
    assert staging.blob_bytes(record) == b"immutable"


def test_stage_does_not_report_success_when_record_parent_fsync_fails(tmp_path, monkeypatch):
    staging = SourceStaging(tmp_path)
    monkeypatch.setattr(source_staging, "_fsync_dir", lambda _directory: (_ for _ in ()).throw(OSError("disk sync failed")))
    with pytest.raises(OSError, match="disk sync failed"):
        staging.stage(tenant_id="tenant_one", actor_id="human_one", client_request_id="source_1",
                      filename="source.txt", media_type="text/plain", data=b"immutable")


def test_gc_keeps_hashes_protected_by_incomplete_bootstrap(tmp_path):
    staging = SourceStaging(tmp_path)
    source = staging.stage(tenant_id="tenant_one", actor_id="human_one", client_request_id="source_1",
                           filename="source.txt", media_type="text/plain", data=b"immutable")
    # This simulates a published blob whose stage descriptor was removed only
    # after a bootstrap journal adopted its immutable reference.
    staging._record_path("tenant_one", "human_one", "source_1").unlink()
    assert staging.gc_orphans(protected_hashes=[source.canonical_content_sha256]) == 0
    assert staging.blob_bytes(source) == b"immutable"


def test_project_source_write_does_not_return_success_when_parent_fsync_fails(tmp_path, monkeypatch):
    ensure_bootstrap()
    monkeypatch.setattr(runs, "RUNS_DIR", tmp_path)
    project = runs.create_project("Prepare a useful report", tenant_id=default_tenant_id())
    monkeypatch.setattr(source_module.os, "fsync", lambda _fd: (_ for _ in ()).throw(OSError("disk sync failed")))
    with pytest.raises(OSError, match="disk sync failed"):
        source_module.add_upload(project.id, filename="report.txt", data=b"evidence", overwrite=False)


def test_stage_retries_after_post_link_sync_failure(tmp_path, monkeypatch):
    staging = SourceStaging(tmp_path)
    original = source_staging._fsync_dir
    calls = 0

    def fail_after_link(directory):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("link sync failed")
        return original(directory)

    monkeypatch.setattr(source_staging, "_fsync_dir", fail_after_link)
    with pytest.raises(OSError, match="link sync failed"):
        staging.stage(tenant_id="tenant_one", actor_id="human_one", client_request_id="source_1",
                      filename="source.txt", media_type="text/plain", data=b"immutable")
    monkeypatch.setattr(source_staging, "_fsync_dir", original)
    record = staging.stage(tenant_id="tenant_one", actor_id="human_one", client_request_id="source_1",
                           filename="source.txt", media_type="text/plain", data=b"immutable")
    assert staging.blob_bytes(record) == b"immutable"


def test_stage_retries_after_record_replace_sync_failure(tmp_path, monkeypatch):
    staging = SourceStaging(tmp_path)
    original = source_staging._atomic_json

    def replace_then_fail(path, payload):
        original(path, payload)
        raise OSError("record sync failed")

    monkeypatch.setattr(source_staging, "_atomic_json", replace_then_fail)
    with pytest.raises(OSError, match="record sync failed"):
        staging.stage(tenant_id="tenant_one", actor_id="human_one", client_request_id="source_1",
                      filename="source.txt", media_type="text/plain", data=b"immutable")
    monkeypatch.setattr(source_staging, "_atomic_json", original)
    record = staging.stage(tenant_id="tenant_one", actor_id="human_one", client_request_id="source_1",
                           filename="source.txt", media_type="text/plain", data=b"immutable")
    assert record.public()["filename"] == "source.txt"


def test_staged_record_identity_must_match_its_tenant_human_and_request_path(tmp_path):
    staging = SourceStaging(tmp_path)
    record = staging.stage(tenant_id="tenant_one", actor_id="human_one", client_request_id="source_1",
                           filename="source.txt", media_type="text/plain", data=b"immutable")
    original_path = staging._record_path("tenant_one", "human_one", "source_1")
    relocated = staging._record_path("tenant_one", "human_two", "source_1")
    relocated.parent.mkdir(parents=True, exist_ok=True)
    relocated.write_bytes(original_path.read_bytes())
    with pytest.raises(KeyError):
        staging.get(tenant_id="tenant_one", actor_id="human_two", source_ref=record.source_ref)
    payload = json.loads(original_path.read_text())
    payload["blob_ref"] = "blob:" + "0" * 64
    original_path.write_text(json.dumps(payload))
    with pytest.raises(KeyError):
        staging.get(tenant_id="tenant_one", actor_id="human_one", source_ref=record.source_ref)


@pytest.mark.parametrize("target", ["root", "ancestor", "record", "blob"])
def test_staging_control_paths_reject_symlink_matrix(tmp_path, target):
    outside = tmp_path / "outside"; outside.mkdir()
    if target == "root":
        linked_root = tmp_path / "linked-root"; linked_root.symlink_to(outside, target_is_directory=True)
        with pytest.raises(ValueError, match="staged source unavailable"):
            SourceStaging(linked_root)
        return
    root = tmp_path / "staging"
    staging = SourceStaging(root)
    record = staging.stage(tenant_id="tenant_one", actor_id="human_one", client_request_id="source_1",
                           filename="source.txt", media_type="text/plain", data=b"immutable")
    if target == "ancestor":
        actor = root / "tenant_one" / "human_one"
        moved = outside / "human_one"; actor.rename(moved)
        actor.symlink_to(moved, target_is_directory=True)
        with pytest.raises(KeyError):
            staging.get(tenant_id="tenant_one", actor_id="human_one", source_ref=record.source_ref)
    elif target == "record":
        path = staging._record_path("tenant_one", "human_one", "source_1")
        path.unlink(); path.symlink_to(outside / "record.json")
        with pytest.raises(KeyError):
            staging.get(tenant_id="tenant_one", actor_id="human_one", source_ref=record.source_ref)
    else:
        path = staging._blob_path(record.canonical_content_sha256)
        path.unlink(); path.symlink_to(outside / "blob")
        with pytest.raises(KeyError):
            staging.get(tenant_id="tenant_one", actor_id="human_one", source_ref=record.source_ref)
