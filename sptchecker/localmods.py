import json
import re
from pathlib import Path

from .config import BEPINEX_PLUGINS_SUBPATH, LEGACY_SERVER_MODS_SUBPATH, SERVER_MODS_SUBPATH

# .NET custom-attribute constructor args (e.g. BepInEx's BepInPlugin(guid, name, version))
# are UTF-8 in the assembly's blob heap, so a plain ASCII scan finds them.
_ASCII_STRING_RE = re.compile(rb"[\x20-\x7e]{4,}")
# Object/record-initializer string literals (e.g. SPT v4 server mods' `new ModMetadata
# { Guid = ..., ... }`) are `ldstr`-loaded from the UTF-16LE user-string heap instead.
_UTF16_STRING_RE = re.compile(rb"(?:[\x20-\x7e]\x00){3,}")

_GUID_RE = re.compile(r"^(?=.*[a-z])[a-z0-9_-]+(\.[a-z0-9_-]+){1,4}$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(\.\d+)?$")
_SPT_VERSION_RE = re.compile(r"^[~^]\d+(\.\d+){1,2}(\.\d+)?$")
# Dotted strings ending in a common file extension are filenames referenced in
# code (e.g. "config.json"), not a mod's GUID -- same shape as a real 2-segment
# GUID otherwise, so this can't be caught by the GUID regex alone.
_FILE_EXTENSIONS = {
    "json", "jsonc", "dll", "txt", "xml", "config", "png", "jpg", "jpeg",
    "cs", "pdb", "ini", "yaml", "yml", "bundle", "db",
}


def _looks_like_guid(s):
    return bool(_GUID_RE.match(s)) and s.rsplit(".", 1)[-1] not in _FILE_EXTENSIONS

_MAX_SCAN_BYTES = 100 * 1024 * 1024
_RECORD_WINDOW = 8


def _ascii_strings(data):
    return [m.group().decode("ascii") for m in _ASCII_STRING_RE.finditer(data)]


def _utf16_strings(data):
    return [m.group().decode("utf-16-le") for m in _UTF16_STRING_RE.finditer(data)]


def _extract_attribute_style(data):
    """BepInEx client plugins: find the (guid, name, version) triple that
    BepInPlugin's constructor args leave adjacent in the blob heap."""
    strings = _ascii_strings(data)
    for i in range(len(strings) - 2):
        guid, name, version = strings[i], strings[i + 1], strings[i + 2]
        if (_looks_like_guid(guid) and _VERSION_RE.match(version)
                and 0 < len(name) < 60 and "." not in name):
            return {"guid": guid, "name": name, "version": version, "spt_version": None}
    return None


def _extract_record_style(data):
    """SPT v4 server mods: find the ModMetadata object-initializer cluster.
    Field order isn't guaranteed (varies mod to mod), so classify each string
    in a small window by shape rather than assuming a fixed position.

    The display name isn't reliably distinguishable from other nearby string
    literals this way (too much false-positive noise from unrelated `ldstr`
    values sitting in the same window) -- callers should use the mod's
    folder name instead, which is always accurate.
    """
    strings = _utf16_strings(data)
    for start in range(len(strings)):
        window = strings[start:start + _RECORD_WINDOW]
        guid = next((s for s in window if _looks_like_guid(s)), None)
        version = next((s for s in window if _VERSION_RE.match(s)), None)
        if not guid or not version:
            continue
        spt_version = next((s for s in window if _SPT_VERSION_RE.match(s)), None)
        return {"guid": guid, "name": None, "version": version, "spt_version": spt_version}
    return None


def extract_mod_metadata(dll_path, mode):
    """Best-effort, dependency-free extraction of a compiled mod's declared
    guid/name/version from its raw bytes. Returns None on failure or
    ambiguity rather than raising -- most DLLs in a plugins folder are
    dependencies, not the plugin itself, and that's expected, not an error.
    """
    try:
        size = dll_path.stat().st_size
        if size == 0 or size > _MAX_SCAN_BYTES:
            return None
        data = dll_path.read_bytes()
    except OSError:
        return None

    try:
        if mode == "attribute":
            return _extract_attribute_style(data)
        return _extract_record_style(data)
    except Exception:
        return None


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
