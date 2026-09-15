import ctypes
import json
import subprocess
from ctypes import wintypes
from pathlib import Path

from .config import (
    BEPINEX_PLUGINS_SUBPATH, CORE_SPT_NAME_PREFIX, LEGACY_SERVER_MODS_SUBPATH,
    MODREADER_EXE, MODREADER_TIMEOUT_SECONDS, SERVER_MODS_SUBPATHS,
    SPT_SERVER_EXE_SUBPATHS,
)

_CREATE_NO_WINDOW = 0x08000000  # avoid a console flash launching a console-mode exe from the GUI app


class _VS_FIXEDFILEINFO(ctypes.Structure):
    _fields_ = [
        ("dwSignature", wintypes.DWORD), ("dwStrucVersion", wintypes.DWORD),
        ("dwFileVersionMS", wintypes.DWORD), ("dwFileVersionLS", wintypes.DWORD),
        ("dwProductVersionMS", wintypes.DWORD), ("dwProductVersionLS", wintypes.DWORD),
        ("dwFileFlagsMask", wintypes.DWORD), ("dwFileFlags", wintypes.DWORD),
        ("dwFileOS", wintypes.DWORD), ("dwFileType", wintypes.DWORD),
        ("dwFileSubtype", wintypes.DWORD), ("dwFileDateMS", wintypes.DWORD),
        ("dwFileDateLS", wintypes.DWORD),
    ]


def _find_server_exe(spt_root):
    """Locate SPT.Server.exe across the layouts 4.0 and 4.1 use, or None.

    Only paths inside a known server folder count. A copy sitting loose in the
    game root is deliberately ignored: SPT 4.1's own mod manager was misled by
    exactly that, spare executables people leave lying around after upgrading,
    and picked the wrong folder as a result.
    """
    root = Path(spt_root)
    for subpath in SPT_SERVER_EXE_SUBPATHS:
        exe = root / subpath
        if exe.is_file():
            return str(exe)
    return None


def detect_spt_version(spt_root):
    """Read the installed SPT server's version straight from SPT.Server.exe's
    own Windows version resource -- there's no plain-text config file that
    records it, but the exe's file version is always accurate (it's set at
    build time). Needed to query Forge's mods/updates endpoint, which
    requires the target SPT version to correctly judge compatibility.
    Returns None (never raises) if the exe is missing or unreadable -- e.g. a
    client-only install with no server component."""
    exe_path = _find_server_exe(spt_root)
    if not exe_path:
        return None
    try:
        version_dll = ctypes.WinDLL("version.dll")
        size = version_dll.GetFileVersionInfoSizeW(exe_path, None)
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        if not version_dll.GetFileVersionInfoW(exe_path, 0, size, buf):
            return None
        value = ctypes.c_void_p()
        value_len = wintypes.UINT()
        if not version_dll.VerQueryValueW(buf, "\\", ctypes.byref(value), ctypes.byref(value_len)):
            return None
        info = ctypes.cast(value, ctypes.POINTER(_VS_FIXEDFILEINFO)).contents
        major = info.dwFileVersionMS >> 16
        minor = info.dwFileVersionMS & 0xFFFF
        build = info.dwFileVersionLS >> 16
        return f"{major}.{minor}.{build}"
    except OSError:
        return None


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
    named after the folder (e.g. user/mods/foo/foo.dll).

    Checks every known server folder name rather than one: 4.1 renamed "SPT"
    to "SPT_Runtime", so hardcoding either misses half the installs out there.
    A 4.1 user with no client plugins previously came back with nothing at all,
    since the only folder being looked at didn't exist on their machine.

    Both are scanned when both exist -- an in-place upgrade can leave the old
    folder behind, and a mod found twice is deduplicated later by its Forge
    match, whereas a mod missed here is invisible for the rest of the scan.
    """
    root = Path(spt_root)
    found = []
    for subpath in SERVER_MODS_SUBPATHS:
        mods_dir = root / subpath
        if mods_dir.is_dir():
            found.extend(mods_dir.glob("*/*.dll"))
    return sorted(found)


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
    install?

    Accepts the client plugin dir, any known server mods dir, or the server
    executable itself. Previously this knew only 4.0's "SPT" folder, so a 4.1
    install that runs server mods without BepInEx failed the check outright --
    the folder was rejected before a scan could even start.
    """
    root = Path(path)
    if (root / BEPINEX_PLUGINS_SUBPATH).is_dir():
        return True
    if any((root / sub).is_dir() for sub in SERVER_MODS_SUBPATHS):
        return True
    return _find_server_exe(path) is not None


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
