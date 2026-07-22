import json
import subprocess
from pathlib import Path

from .config import (
    BEPINEX_PLUGINS_SUBPATH, CORE_SPT_NAME_PREFIX, LEGACY_SERVER_MODS_SUBPATH,
    MODREADER_EXE, MODREADER_TIMEOUT_SECONDS, SERVER_MODS_SUBPATH,
)

_CREATE_NO_WINDOW = 0x08000000  # avoid a console flash launching a console-mode exe from the GUI app


def _run_modreader(spt_root, client_dlls, server_dlls):
    """Run the ModReader.exe helper once for the whole batch (not per-DLL --
    process startup cost adds up fast otherwise) and return its parsed
    {"client": {...}, "server": {...}} response, keyed by DLL path.

    ModReader does the actual extraction via real CLR reflection (see
    modreader/Program.cs) instead of guessing at compiled bytecode shapes --
    it can read any mod regardless of what code the author used to build
    their metadata, which no amount of static-pattern-matching in Python
    ever could. Returns empty results (not a crash) if the helper is
    missing or fails; a scan finding nothing is expected/recoverable, same
    as any other per-mod extraction failure.
    """
    if not MODREADER_EXE.exists():
        return {"client": {}, "server": {}}

    request = {
        "sptRoot": str(spt_root),
        "clientDlls": [str(p) for p in client_dlls],
        "serverDlls": [str(p) for p in server_dlls],
    }
    try:
        proc = subprocess.run(
            [str(MODREADER_EXE)],
            input=json.dumps(request),
            capture_output=True,
            text=True,
            timeout=MODREADER_TIMEOUT_SECONDS,
            creationflags=_CREATE_NO_WINDOW,
        )
        return json.loads(proc.stdout)
    except Exception:
        return {"client": {}, "server": {}}


def find_bepinex_plugins(spt_root):
    plugins_dir = Path(spt_root) / BEPINEX_PLUGINS_SUBPATH
    if not plugins_dir.is_dir():
        return []
    return sorted(plugins_dir.rglob("*.dll"))


def find_server_mods(spt_root):
    """SPT v4 server mods -- each mod's own DLL lives directly in its folder,
    named after the folder (e.g. user/mods/foo/foo.dll)."""
    mods_dir = Path(spt_root) / SERVER_MODS_SUBPATH
    if not mods_dir.is_dir():
        return []
    return sorted(mods_dir.glob("*/*.dll"))


def find_legacy_server_mods(spt_root):
    """Pre-v4 SPT server mods, identified by a package.json manifest. Rare on
    current installs but trivial to support."""
    mods_dir = Path(spt_root) / LEGACY_SERVER_MODS_SUBPATH
    if not mods_dir.is_dir():
        return []
    return sorted(mods_dir.glob("*/package.json"))


def _parse_legacy_manifest(manifest_path):
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    name = data.get("name")
    version = data.get("version")
    if not name or not version:
        return None
    return {
        "source": "server_legacy",
        "path": str(manifest_path.parent),
        "guid": None,
        "name": name,
        "author": data.get("author"),
        "version": version,
    }


def _is_core_spt_component(record):
    """SPT's own bundled DLLs (SPT.Common, SPT.Reflection, ...) can sit in
    the same folder shapes as real mods and get scanned right alongside
    them -- filter by name/guid prefix rather than skipping them at
    discovery time, since ModReader still needs to read them to tell them
    apart from an actual mod living in the same folder."""
    prefix = CORE_SPT_NAME_PREFIX
    name = (record.get("name") or "").lower()
    guid = (record.get("guid") or "").lower()
    return name.startswith(prefix) or guid.startswith(prefix)


def validate_spt_root(path):
    """Quick sanity check for the folder picker: does this look like an SPT
    install? Checks both the client plugin dir and the v4 server mods dir."""
    root = Path(path)
    return (root / BEPINEX_PLUGINS_SUBPATH).is_dir() or (root / SERVER_MODS_SUBPATH).is_dir()


def scan_installed_mods(spt_root):
    """Scan an SPT install for locally installed mods. Each record has
    source/path/guid/name/version (and author/spt_version, when the reader
    found them); entries where extraction failed are skipped rather than
    included with missing data."""
    results = []

    client_dlls = find_bepinex_plugins(spt_root)
    server_dlls = find_server_mods(spt_root)
    reader_output = _run_modreader(spt_root, client_dlls, server_dlls)
    client_meta = reader_output.get("client", {})
    server_meta = reader_output.get("server", {})

    for dll_path in client_dlls:
        meta = client_meta.get(str(dll_path))
        if meta and not meta.get("error") and meta.get("guid"):
            results.append({
                "source": "client", "path": str(dll_path), "guid": meta.get("guid"),
                "name": meta.get("name"), "version": meta.get("version"),
            })

    for dll_path in server_dlls:
        meta = server_meta.get(str(dll_path))
        if meta and not meta.get("error") and meta.get("guid"):
            results.append({
                "source": "server", "path": str(dll_path), "guid": meta.get("guid"),
                # The folder name is always accurate even when the mod's own
                # Name property is missing or blank.
                "name": meta.get("name") or dll_path.parent.name,
                "author": meta.get("author"), "version": meta.get("version"),
                "spt_version": meta.get("sptVersion"),
            })

    for manifest_path in find_legacy_server_mods(spt_root):
        record = _parse_legacy_manifest(manifest_path)
        if record:
            results.append(record)

    return [r for r in results if not _is_core_spt_component(r)]
