"""Synthetic descriptor and transaction conformance; no network required."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from openstat import packages
from openstat.packages import MANIFEST, PackageError, PackageManager


@pytest.fixture
def site(tmp_path):
    source = tmp_path / "source"
    source.mkdir()

    def make(descriptor="v 3\nd Example package\nf demo.ado\nf demo.sthlp\n", **payloads):
        (source / "demo.pkg").write_text(descriptor, encoding="utf-8")
        for filename, data in {"demo.ado": b"program demo\nend\n", "demo.sthlp": b"help", **payloads}.items():
            path = source / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return source

    return make


def test_install_describe_find_installed_and_uninstall(tmp_path, site):
    source = site()
    manager = PackageManager(tmp_path / "ado")
    description = manager.describe("demo", str(source))
    assert description["title"] == "Example package"
    assert description["descriptor_sha256"] == hashlib.sha256((source / "demo.pkg").read_bytes()).hexdigest()
    assert description["descriptor_version"] == 3
    manifest = manager.install("demo", str(source))
    assert manifest["files"]["demo.ado"] == hashlib.sha256((source / "demo.ado").read_bytes()).hexdigest()
    assert manager.find("demo") == manager.root / "demo" / "demo.ado"
    assert manager.installed() == [manifest]
    assert set(manager.uninstall("demo")["removed"]) == {"demo.ado", "demo.sthlp"}
    assert manager.installed() == []
    assert not (manager.root / "demo").exists()


def test_duplicate_relative_remote_references_are_flattened_once(tmp_path, monkeypatch):
    base = "https://example.invalid/archive/f/"
    payload = b"v 3\nf ../j/join.ado\nf ftools.mata\nf ftools.mata\n"
    resources = {base + "demo.pkg": payload,
        "https://example.invalid/archive/j/join.ado": b"join body", base + "ftools.mata": b"mata body"}
    calls = []

    def fetch(url):
        calls.append(url)
        return resources[url]

    monkeypatch.setattr(packages, "download", fetch)
    manager = PackageManager(tmp_path / "ado")
    manifest = manager.install("demo", base)
    assert set(manifest["files"]) == {"join.ado", "ftools.mata"}
    assert (manager.root / "demo" / "join.ado").read_bytes() == b"join body"
    assert calls.count(base + "ftools.mata") == 1
    assert manifest["file_sources"]["join.ado"] == "../j/join.ado"


@pytest.mark.parametrize("suffix", [".mlib", ".mo", ".plugin", ".dll", ".so", ".dylib"])
def test_compiled_payload_is_stored_unchanged_not_executed(tmp_path, site, suffix):
    blob = bytes(range(256))
    filename = "native" + suffix
    source = site("v 3\nf " + filename + "\n", **{filename: blob})
    manager = PackageManager(tmp_path / "ado")
    manifest = manager.install("demo", str(source))
    assert (manager.root / "demo" / filename).read_bytes() == blob
    assert manifest["files"][filename] == hashlib.sha256(blob).hexdigest()
    assert "does not execute or load" in manifest["execution_compatibility"]


def test_platform_rename_required_and_forced_system_files(tmp_path, site):
    source = site("v 3\ng WIN64 native.win driver.plugin\ng LINUX64 native.linux driver.plugin\n"
        "G WIN64 data.win lookup.dat\nF support/example.csv\nh driver.plugin\n",
        **{"native.win": b"windows", "data.win": b"table", "support/example.csv": b"csv"})
    manager = PackageManager(tmp_path / "ado", platform_name="WIN64")
    manifest = manager.install("demo", str(source))
    assert set(manifest["files"]) == {"driver.plugin", "lookup.dat", "example.csv"}
    assert (manager.root / "demo" / "driver.plugin").read_bytes() == b"windows"
    assert manifest["file_sources"]["lookup.dat"] == "data.win"
    with pytest.raises(PackageError, match="required file driver.plugin"):
        PackageManager(tmp_path / "mac", platform_name="MACARM64").install("demo", str(source))


@pytest.mark.parametrize("platform_name,expected", [("LINUX64P", b"new"), ("LINUX64", b"old")])
def test_linux_platform_precedence(tmp_path, site, platform_name, expected):
    source = site("v 3\ng LINUX64 old.bin driver.plugin\ng LINUX64P new.bin driver.plugin\n",
        **{"old.bin": b"old", "new.bin": b"new"})
    manager = PackageManager(tmp_path / "ado", platform_name=platform_name)
    manager.install("demo", str(source))
    assert (manager.root / "demo" / "driver.plugin").read_bytes() == expected


def test_linux_new_platform_can_fallback(tmp_path, site):
    source = site("v 3\ng LINUX64 old.bin driver.plugin\n", **{"old.bin": b"old"})
    assert PackageManager(tmp_path / "ado", platform_name="LINUX64P").install("demo", str(source))["files"]["driver.plugin"]


def test_win64_legacy_alias(tmp_path, site):
    source = site("v 3\ng WIN64A old.bin driver.plugin\n", **{"old.bin": b"old"})
    assert PackageManager(tmp_path / "ado", platform_name="WIN64").install("demo", str(source))["files"]["driver.plugin"]


def test_ancillary_optional_all_to_cwd_and_uninstall_keeps_data(tmp_path, site, monkeypatch):
    source = site("v 3\nf demo.ado\nf example.do\nf data.dta\nf archive.zip\n",
        **{"example.do": b"example", "data.dta": b"dataset", "archive.zip": b"zip"})
    other = tmp_path / "cwd"
    other.mkdir()
    monkeypatch.chdir(other)
    manager = PackageManager(tmp_path / "ado")
    assert manager.install("demo", str(source))["ancillary"] == []
    assert not (other / "example.do").exists()
    manifest = manager.install("demo", str(source), replace=True, all_files=True)
    assert set(manifest["files"]) == {"demo.ado"}
    assert {r["name"] for r in manifest["ancillary"]} == {"example.do", "data.dta", "archive.zip"}
    assert (other / "data.dta").read_bytes() == b"dataset"
    manager.uninstall("demo")
    assert (other / "data.dta").read_bytes() == b"dataset"


def test_ancillary_collision_never_partially_installs(tmp_path, site):
    source = site("v 3\nf demo.ado\nf data.dta\n", **{"data.dta": b"new"})
    other = tmp_path / "cwd"
    other.mkdir()
    (other / "data.dta").write_bytes(b"old")
    manager = PackageManager(tmp_path / "ado")
    with pytest.raises(PackageError, match="already exists"):
        manager.install("demo", str(source), all_files=True, ancillary_dir=other)
    assert not (manager.root / "demo").exists()
    assert (other / "data.dta").read_bytes() == b"old"


def test_download_failure_preserves_previous_install(tmp_path, site):
    source = site()
    manager = PackageManager(tmp_path / "ado")
    old = manager.install("demo", str(source))
    (source / "demo.pkg").write_text("v 3\nf demo.ado\nf missing.ado\n", encoding="utf-8")
    (source / "demo.ado").write_bytes(b"new")
    with pytest.raises(OSError):
        manager.install("demo", str(source), replace=True)
    assert manager.installed() == [old]
    assert manager.find("demo").read_bytes() != b"new"


def test_commit_failure_rolls_back_package_and_ancillary(tmp_path, site, monkeypatch):
    source = site()
    manager = PackageManager(tmp_path / "ado")
    old = manager.install("demo", str(source))
    site("v 3\nf demo.ado\nf a.dta\nf b.dta\n", **{"a.dta": b"a new", "b.dta": b"b new", "demo.ado": b"new"})
    other = tmp_path / "other"
    other.mkdir()
    (other / "a.dta").write_bytes(b"a old")
    (other / "b.dta").write_bytes(b"b old")
    real_replace = os.replace

    def fail_second(src, dst):
        if Path(dst).name == "b.dta":
            raise OSError("synthetic commit failure")
        return real_replace(src, dst)

    monkeypatch.setattr(packages.os, "replace", fail_second)
    with pytest.raises(OSError, match="synthetic commit"):
        manager.install("demo", str(source), replace=True, all_files=True, ancillary_dir=other)
    assert manager.installed() == [old]
    assert (other / "a.dta").read_bytes() == b"a old"
    assert (other / "b.dta").read_bytes() == b"b old"
    assert not list(other.glob(".openstat-*"))


def test_replace_retains_unowned_files_and_removes_stale_owned_files(tmp_path, site):
    source = site()
    manager = PackageManager(tmp_path / "ado")
    manager.install("demo", str(source))
    target = manager.root / "demo"
    (target / "notes.txt").write_bytes(b"user")
    (source / "demo.pkg").write_text("v 3\nf demo.ado\n", encoding="utf-8")
    (source / "demo.ado").write_bytes(b"updated")
    manager.install("demo", str(source), replace=True)
    assert (target / "notes.txt").read_bytes() == b"user"
    assert not (target / "demo.sthlp").exists()
    assert manager.find("demo").read_bytes() == b"updated"


def test_uninstall_preserves_modified_and_unowned_files(tmp_path, site):
    source = site()
    manager = PackageManager(tmp_path / "ado")
    manager.install("demo", str(source))
    target = manager.root / "demo"
    (target / "demo.ado").write_bytes(b"user edit")
    (target / "notes.txt").write_bytes(b"user notes")
    report = manager.uninstall("demo")
    assert report["preserved"] == ["demo.ado"]
    assert (target / "demo.ado").read_bytes() == b"user edit"
    assert (target / "notes.txt").read_bytes() == b"user notes"
    assert not (target / MANIFEST).exists()


@pytest.mark.parametrize("reference", ["/absolute.ado", "//evil.test/x.ado", "https://evil.test/x.ado", "C:/evil.ado", "x.ado?query", "x.ado#fragment", "%2foutside.ado", "../foo\\evil.ado"])
def test_unsafe_descriptor_sources_rejected(tmp_path, site, reference):
    source = site("v 3\nf " + reference + "\n")
    with pytest.raises(PackageError, match="unsafe package path"):
        PackageManager(tmp_path / "ado").install("demo", str(source))


@pytest.mark.parametrize("filename", ["openstat-package.json", "NUL.ado", "CON", "COM1.dll", "bad.ado."])
def test_unsafe_local_destinations_rejected(tmp_path, site, filename):
    source = site("v 3\nf " + filename + "\n")
    with pytest.raises(PackageError, match="unsafe package destination"):
        PackageManager(tmp_path / "ado").describe("demo", str(source))


def test_local_source_cannot_escape_declared_directory(tmp_path, site):
    source = site("v 3\nf ../private.ado\n")
    (tmp_path / "private.ado").write_bytes(b"private")
    with pytest.raises(PackageError, match="leaves source directory"):
        PackageManager(tmp_path / "ado").install("demo", str(source))


def test_conflicting_flattened_sources_rejected(tmp_path, site):
    source = site("v 3\nf a/same.ado\nf b/same.ado\n")
    with pytest.raises(PackageError, match="conflicting package destination"):
        PackageManager(tmp_path / "ado").describe("demo", str(source))


def test_recursive_find_sees_nested_packages_but_not_transaction_staging(tmp_path):
    manager = PackageManager(tmp_path)
    nested = tmp_path / "package" / "nested"
    nested.mkdir(parents=True)
    (nested / "demo.ado").write_bytes(b"demo")
    staging = tmp_path / ".openstat-package-work" / "stage"
    staging.mkdir(parents=True)
    (staging / "hidden.ado").write_bytes(b"hidden")
    assert manager.find("demo") == nested / "demo.ado"
    assert manager.find("demo", recursive=False) is None
    assert manager.find("hidden") is None
    assert manager.find("../demo") is None


def test_end_comments_descriptions_and_blank_records(tmp_path, site):
    source = site("* ignored\n\nv 3\nd Title\nd\nf\tdemo.ado author's description with unmatched quote \"\ne\nf missing.ado\n")
    manager = PackageManager(tmp_path / "ado")
    assert manager.describe("demo", str(source))["description"] == ["Title", ""]
    assert set(manager.install("demo", str(source))["files"]) == {"demo.ado"}


def test_corrupt_manifest_cannot_remove_unowned_files(tmp_path, site):
    manager = PackageManager(tmp_path / "ado")
    manager.install("demo", str(site()))
    manifest = manager.root / "demo" / MANIFEST
    contents = json.loads(manifest.read_text(encoding="utf-8"))
    contents["files"] = {"../outside.ado": "a" * 64}
    manifest.write_text(json.dumps(contents), encoding="utf-8")
    with pytest.raises(PackageError, match="ownership manifest"):
        manager.uninstall("demo")


def test_byte_limit_local_and_remote_errors(tmp_path, site, monkeypatch):
    source = site()
    monkeypatch.setenv("OPENSTAT_PACKAGE_MAX_BYTES", "4")
    with pytest.raises(PackageError, match="exceeds"):
        PackageManager(tmp_path / "ado").install("demo", str(source))
    monkeypatch.setenv("OPENSTAT_PACKAGE_MAX_BYTES", "invalid")
    with pytest.raises(PackageError, match="integer"):
        packages._read_limit()


def test_default_source_retains_existing_ssc_endpoint(tmp_path, monkeypatch):
    seen = []

    def fetch(url):
        seen.append(url)
        return b"v 3\nf distinct.ado\nf distinct.hlp\n" if url.endswith(".pkg") else b"payload"

    monkeypatch.setattr(packages, "download", fetch)
    PackageManager(tmp_path).install("distinct")
    assert seen[0] == "http://fmwww.bc.edu/repec/bocode/d/distinct.pkg"
