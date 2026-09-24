"""End-to-end tests for the Local Mods scan, against real SPT installs.

These run the real ModReader.exe (assets/) over real server mods, because the bug they guard
against only exists against real mods: SPT 4.1 replaced the abstract base class
`AbstractModMetadata` with the interface `IModMetadata`, and ModReader only recognised the
former, so every 4.1 server mod read as nothing. A server-only install (no BepInEx client
plugins) then reported "0 mods scanned" with no error at all.

Run:  python -m pytest tests -v
Opt-in installs (integration tests skip when the env var is unset):
  SPT41_ROOT   path to an SPT 4.1.x install with server mods
  SPT40_ROOT   path to an SPT 4.0.x install with server mods
"""

import os
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sptchecker import localmods  # noqa: E402

def configured_install(variable):
    value = os.environ.get(variable)
    if not value:
        pytest.skip(f"Set {variable} to opt into real-install scan tests")
    root = Path(value).expanduser().resolve()
    if not root.is_dir():
        pytest.fail(f"{variable} is not an existing install directory: {root}")
    return root


@pytest.fixture
def spt41_root():
    return configured_install("SPT41_ROOT")


@pytest.fixture
def spt40_root():
    return configured_install("SPT40_ROOT")


def server_records(root):
    return [r for r in localmods.scan_installed_mods(root) if r["source"] == "server"]


def mod_folders(root, server_dir):
    """Server mod folders that hold at least one DLL: each should yield a record."""
    mods = root / server_dir / "user" / "mods"
    return sorted(d.name for d in mods.iterdir() if d.is_dir() and any(d.glob("*.dll")))


def test_spt41_server_mods_are_read(spt41_root):
    """The reported bug: a 4.1 install's server mods must come back with real metadata."""
    records = server_records(spt41_root)
    found = {Path(r["path"]).parent.name for r in records}

    assert set(mod_folders(spt41_root, "SPT_Runtime")) <= found, f"server mods missing from the scan: {found}"
    for record in records:
        assert record["guid"] and record["version"], record


def test_spt41_reads_the_values_the_mod_declares(spt41_root):
    sherpa = [r for r in server_records(spt41_root) if r["guid"] == "com.sherpa.server"]
    if not sherpa:
        pytest.skip("Sherpa is not installed in the 4.1 test install")
    assert sherpa[0]["name"] == "Sherpa"
    assert sherpa[0]["version"].startswith("0.1.0")
    assert sherpa[0]["spt_version"] and "4.1.6" in sherpa[0]["spt_version"]


def test_spt40_server_mods_still_read(spt40_root):
    """The 4.0 form (a subclass of AbstractModMetadata) must keep working."""
    found = {Path(r["path"]).parent.name for r in server_records(spt40_root)}

    assert set(mod_folders(spt40_root, "SPT")) <= found, f"server mods missing from the scan: {found}"


def test_a_failing_modreader_is_reported_not_turned_into_zero_mods(tmp_path, monkeypatch):
    """A helper that crashes must surface as an error the scan window can show, not as an
    empty result that reads as '0 mods scanned'."""
    fake = tmp_path / "ModReader.cmd"
    fake.write_text("@echo ModReader exploded: missing thing 1>&2\r\n@exit /b 3\r\n", encoding="ascii")
    monkeypatch.setattr(localmods, "MODREADER_EXE", fake)

    with pytest.raises(localmods.ModReaderError) as failure:
        localmods._run_modreader(str(tmp_path), [], [str(tmp_path / "x.dll")])

    assert "exit code 3" in str(failure.value)
    assert "ModReader exploded" in str(failure.value)


def test_a_missing_modreader_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(localmods, "MODREADER_EXE", tmp_path / "nope.exe")

    with pytest.raises(localmods.ModReaderError, match="ModReader"):
        localmods._run_modreader(str(tmp_path), [], [str(tmp_path / "x.dll")])


def test_nothing_to_read_needs_no_helper(tmp_path, monkeypatch):
    """An install with no DLLs at all is a genuine zero, not an error."""
    monkeypatch.setattr(localmods, "MODREADER_EXE", tmp_path / "nope.exe")

    assert localmods._run_modreader(str(tmp_path), [], []) == {"client": {}, "server": {}}


def test_spt41_without_client_plugins(monkeypatch, spt41_root):
    monkeypatch.setattr(localmods, "find_bepinex_plugins", lambda root: [])
    records = server_records(spt41_root)
    expected = set(mod_folders(spt41_root, "SPT_Runtime"))
    assert expected, "Configure an SPT 4.1 install containing server mods"
    assert expected <= {Path(r["path"]).parent.name for r in records}


@pytest.mark.parametrize("stdout", ["not json", "null", "[]", '{}',
    '{"client":{},"server":{"x.dll":42}}', '{"client":{},"server":{}}'])
def test_malformed_helper_response(monkeypatch, stdout):
    monkeypatch.setattr(localmods, "MODREADER_EXE", Path(__file__))
    monkeypatch.setattr(localmods.subprocess, "run", lambda *a, **kw:
        subprocess.CompletedProcess([], 0, stdout, ""))
    with pytest.raises(localmods.ModReaderError):
        localmods._run_modreader(".", [], ["x.dll"])


@pytest.mark.parametrize("error, message", [
    (subprocess.TimeoutExpired("ModReader", 120), "timed out"),
    (OSError("cannot launch helper"), "Could not start"),
])
def test_helper_launch_failure(monkeypatch, error, message):
    monkeypatch.setattr(localmods, "MODREADER_EXE", Path(__file__))
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(localmods.subprocess, "run", fail)
    with pytest.raises(localmods.ModReaderError, match=message):
        localmods._run_modreader(".", [], ["x.dll"])


def test_per_dll_failure_preserves_readable_mods(monkeypatch, caplog):
    monkeypatch.setattr(localmods, "MODREADER_EXE", Path(__file__))
    output = {"client": {}, "server": {"bad.dll": {"error": "missing dependency"},
        "good.dll": {"guid": "example.mod", "version": "1.0.0"}}}
    monkeypatch.setattr(localmods.subprocess, "run", lambda *a, **kw:
        subprocess.CompletedProcess([], 0, json.dumps(output), ""))
    assert localmods._run_modreader(".", [], list(output["server"])) == output
    assert "bad.dll" in caplog.text
    del output["server"]["good.dll"]
    with pytest.raises(localmods.ModReaderError, match="missing dependency"):
        localmods._run_modreader(".", [], ["bad.dll"])
