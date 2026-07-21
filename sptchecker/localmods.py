import json
from pathlib import Path

from . import dotnet_meta as dm
from .config import BEPINEX_PLUGINS_SUBPATH, LEGACY_SERVER_MODS_SUBPATH, SERVER_MODS_SUBPATH

_MAX_SCAN_BYTES = 100 * 1024 * 1024


def _load_assembly(dll_path):
    try:
        size = dll_path.stat().st_size
        if size == 0 or size > _MAX_SCAN_BYTES:
            return None
        return dm.load(dll_path)
    except (OSError, dm.MetadataError):
        return None


def _extract_client_plugin(dll_path):
    """BepInEx client plugins: resolve the BepInPlugin custom attribute by
    its actual declaring type name (real assembly metadata, not a guess from
    string shapes -- immune to case, unrelated adjacent attributes like
    BepInDependency, etc.) and decode its 3 constructor string args."""
    meta = _load_assembly(dll_path)
    if meta is None:
        return None
    try:
        matches = [
            meta.decode_fixed_string_args(blob, 3)
            for name, _ns, blob in meta.custom_attributes()
            if name == "BepInPlugin"
        ]
    except Exception:
        return None
    matches = [m for m in matches if m and all(m)]
    if len(matches) != 1:
        return None
    guid, name, version = matches[0]
    return {"guid": guid, "name": name, "version": version, "spt_version": None}


def _extract_server_mod(dll_path):
    """SPT v4 server mods: find the method that sets ModMetadata's Guid and
    Version properties. Roslyn always lowers `new ModMetadata { Guid = ...,
    Version = ... }` to the same dup/ldstr/call-setter IL shape regardless of
    field order in source, so this reads the exact values assigned rather
    than classifying nearby string literals by shape."""
    meta = _load_assembly(dll_path)
    if meta is None:
        return None
    try:
        found = dm.find_property_set_cluster(
            meta, required={"Guid", "Version"}, optional={"Name", "SptVersion"},
        )
    except Exception:
        return None
    if not found:
        return None
    return {
        "guid": found.get("Guid"),
        "name": found.get("Name"),
        "version": found.get("Version"),
        "spt_version": found.get("SptVersion"),
    }


def extract_mod_metadata(dll_path, mode):
    """Best-effort extraction of a compiled mod's declared guid/name/version
    from its real .NET assembly metadata. Returns None on failure or
    ambiguity rather than raising -- most DLLs in a plugins folder are
    dependencies, not the plugin itself, and that's expected, not an error.
    """
    if mode == "attribute":
        return _extract_client_plugin(dll_path)
    return _extract_server_mod(dll_path)


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
        "spt_version": data.get("sptVersion"),
    }


def validate_spt_root(path):
    """Quick sanity check for the folder picker: does this look like an SPT
    install? Checks both the client plugin dir and the v4 server mods dir."""
    root = Path(path)
    return (root / BEPINEX_PLUGINS_SUBPATH).is_dir() or (root / SERVER_MODS_SUBPATH).is_dir()


def scan_installed_mods(spt_root):
    """Scan an SPT install for locally installed mods. Returns a list of
    records shaped per config.LOCAL_MOD_FIELDS; entries where extraction
    failed are skipped rather than included with missing data."""
    results = []

    for dll_path in find_bepinex_plugins(spt_root):
        meta = extract_mod_metadata(dll_path, mode="attribute")
        if meta:
            results.append({"source": "client", "path": str(dll_path), **meta})

    for dll_path in find_server_mods(spt_root):
        meta = extract_mod_metadata(dll_path, mode="record")
        if meta:
            meta["name"] = meta["name"] or dll_path.parent.name
            results.append({"source": "server", "path": str(dll_path), **meta})

    for manifest_path in find_legacy_server_mods(spt_root):
        record = _parse_legacy_manifest(manifest_path)
        if record:
            results.append(record)

    return results
