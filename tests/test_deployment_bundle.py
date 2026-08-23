from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest

from deploy.bundle import (
    DEPLOYMENT_MODES,
    MANIFEST_NAME,
    REQUIRED_REFERENCES,
    BundleError,
    OperationalBundleBuilder,
    build_bundle,
    verify_bundle,
)
from deploy.install import install_bundle

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "deployment"


def references() -> dict[str, str]:
    names = {
        "operation_graph": "operation-graph.json",
        "runtime_agent": "runtime-agent.txt",
    }
    return {name: names.get(name, f"{name}.txt" if name in {"app", "api", "worker", "migrations", "tests"} else f"{name}.json") for name in REQUIRED_REFERENCES}


def test_bundle_is_deterministic_content_addressed_and_target_neutral(tmp_path: Path):
    first = build_bundle(FIXTURE, references(), tmp_path / "one", release="2026.08.23")
    second = build_bundle(FIXTURE, references(), tmp_path / "two", release="2026.08.23")
    assert first.read_bytes() == second.read_bytes()
    result = verify_bundle(first)
    assert first.stem.endswith(result.bundle_hash)
    assert result.manifest["deployment_modes"] == list(DEPLOYMENT_MODES)
    assert set(result.manifest["references"]) == set(REQUIRED_REFERENCES)
    assert "target" not in result.manifest
    assert result.manifest["sbom"]["format"] == "SPDX-2.3"
    assert "provenance" in result.manifest


def test_bundle_signature_uses_injected_interfaces(tmp_path: Path):
    signer = lambda payload: b"signature:" + payload[:8]
    bundle = OperationalBundleBuilder(FIXTURE, references(), release="r1").build(tmp_path, signer=signer)
    with pytest.raises(BundleError, match="requires an injected verifier"):
        verify_bundle(bundle)
    verified = verify_bundle(bundle, verifier=lambda payload, signature: signature == signer(payload))
    assert verified.signature_verified
    with pytest.raises(BundleError, match="signature verification"):
        verify_bundle(bundle, verifier=lambda _payload, _signature: False)
    unsigned = build_bundle(FIXTURE, references(), tmp_path / "unsigned", release="r1")
    with pytest.raises(BundleError, match="requires a signed bundle"):
        verify_bundle(unsigned, require_signature=True)


def _tar(entries: list[tuple[tarfile.TarInfo, bytes]]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for info, data in entries:
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return output.getvalue()


@pytest.mark.parametrize("member_name", ["../escape", "/absolute", "payload/../../escape", "payload\\escape"])
def test_verify_rejects_archive_traversal(tmp_path: Path, member_name: str):
    path = tmp_path / "malicious.tar"
    path.write_bytes(_tar([(tarfile.TarInfo(member_name), b"bad")]))
    with pytest.raises(BundleError, match="unsafe bundle path"):
        verify_bundle(path)


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.DIRTYPE])
def test_verify_rejects_links_and_non_files(tmp_path: Path, kind: bytes):
    member = tarfile.TarInfo("payload/member")
    member.type = kind
    member.linkname = "target"
    path = tmp_path / "malicious.tar"
    path.write_bytes(_tar([(member, b"")]))
    with pytest.raises(BundleError, match="only regular files"):
        verify_bundle(path)


def test_tampering_and_named_hash_mismatch_are_rejected(tmp_path: Path):
    bundle = build_bundle(FIXTURE, references(), tmp_path, release="r1")
    data = bytearray(bundle.read_bytes())
    data[600] ^= 1
    bundle.write_bytes(data)
    with pytest.raises(BundleError, match="content address|integrity|invalid bundle"):
        verify_bundle(bundle)


def test_source_symlink_and_credentials_are_rejected(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    for source_file in FIXTURE.iterdir():
        (source / source_file.name).write_bytes(source_file.read_bytes())
    (source / "api.txt").unlink()
    (source / "api.txt").symlink_to(FIXTURE / "api.txt")
    with pytest.raises(BundleError, match="symlinks"):
        build_bundle(source, references(), tmp_path / "out", release="r1")
    (source / "api.txt").unlink()
    (source / "api.txt").write_text("api_key = super-secret-value")
    with pytest.raises(BundleError, match="credential"):
        build_bundle(source, references(), tmp_path / "out", release="r1")


def test_referenced_directories_are_sorted_and_nested_symlinks_are_rejected(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    for source_file in FIXTURE.iterdir():
        (source / source_file.name).write_bytes(source_file.read_bytes())
    app = source / "generated-app"
    app.mkdir()
    (app / "z.txt").write_text("last")
    (app / "a.txt").write_text("first")
    refs = references() | {"app": "generated-app"}
    bundle = build_bundle(source, refs, tmp_path / "out", release="r1")
    result = verify_bundle(bundle)
    assert result.manifest["references"]["app"] == "payload/generated-app"
    assert "payload/generated-app/a.txt" in result.manifest["files"]
    (app / "link.txt").symlink_to(source / "api.txt")
    with pytest.raises(BundleError, match="symlinks"):
        build_bundle(source, refs, tmp_path / "out-two", release="r1")


def test_install_verifies_before_extracting_and_is_immutable(tmp_path: Path):
    bundle = build_bundle(FIXTURE, references(), tmp_path / "built", release="r1")
    verified = verify_bundle(bundle)
    installed = install_bundle(bundle, tmp_path / "releases", expected_hash=verified.bundle_hash)
    assert (installed / MANIFEST_NAME).is_file()
    assert (installed / ".verified").read_text().strip() == verified.bundle_hash
    with pytest.raises(BundleError, match="already exists"):
        install_bundle(bundle, tmp_path / "releases")
