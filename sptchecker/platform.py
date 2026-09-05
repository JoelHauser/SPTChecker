import ctypes
import sys
import winreg
from ctypes import wintypes
from pathlib import Path

from PIL import Image, ImageDraw
from winotify import Notification

from .config import (
    ACCENT_DANGER, ACCENT_NEW, ASSETS_DIR, BG, CARD_BG, PROTOCOL_REG_PATH,
    SHOW_EVENT_NAME, SHOW_URI, STARTUP_REG_NAME, STARTUP_REG_PATH, TEXT_BRIGHT,
)


def _rgb(hex_color, alpha=255):
    return tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5)) + (alpha,)

# ── DPI awareness ──────────────────────────────────────────────────────


def set_dpi_aware():
    """Declare the process per-monitor DPI aware.

    Without this, Windows treats the app as DPI-unaware and bitmap-stretches the
    whole rendered window to match display scaling -- which is what makes small,
    precise shapes (icons, color swatches) look blurry/pixelated on any scaled
    display (125%/150%, the default on most laptops). Must be called before the
    Tk root window is created.
    """
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()  # fallback for older Windows
        except Exception:
            pass


# ── Dark title bar ─────────────────────────────────────────────────────


_SWP_FLAGS = 0x2 | 0x1 | 0x4 | 0x20  # NOMOVE | NOSIZE | NOZORDER | FRAMECHANGED
_DWMWA_TRANSITIONS_FORCEDISABLED = 3


def _set_dwm_attribute(window, attr, value=1):
    """Set a DWM window attribute on a Tk window's real top-level hwnd.
    Returns (hwnd, api_result); raises only on ctypes-level errors --
    callers wrap in their own try/except."""
    hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
    c_value = ctypes.c_int(value)
    result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
        hwnd, attr, ctypes.byref(c_value), ctypes.sizeof(c_value)
    )
    return hwnd, result


def set_dark_title_bar(window, show=True):
    try:
        window.withdraw()
        window.update_idletasks()
        # 20 is DWMWA_USE_IMMERSIVE_DARK_MODE on current Windows; 19 is the
        # pre-20H1 value of the same attribute, tried only as a fallback.
        for attr in (20, 19):
            hwnd, result = _set_dwm_attribute(window, attr)
            if result == 0:
                break
        # DwmSetWindowAttribute alone doesn't always repaint the non-client
        # frame -- force Windows to redraw the title bar with the new value.
        ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, _SWP_FLAGS)
        if show:
            window.deiconify()
    except Exception:
        if show:
            window.deiconify()


def disable_show_animation(window):
    """Stop Windows from playing its fade/expand animation when the window
    comes back from withdraw() -- that animation is what exposes an
    unpainted white backbuffer for a frame or two before Tk's own dark-themed
    repaint catches up, which is what looks like a "white flash" when
    restoring from the tray."""
    try:
        _set_dwm_attribute(window, _DWMWA_TRANSITIONS_FORCEDISABLED)
    except Exception:
        pass


# ── Startup registry ──────────────────────────────────────────────────


def is_startup_enabled():
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_REG_PATH, 0, winreg.KEY_READ)
        winreg.QueryValueEx(key, STARTUP_REG_NAME)
        winreg.CloseKey(key)
        return True
    except FileNotFoundError:
        return False


def refresh_startup_if_stale():
    """Re-write the startup registry entry if the stored exe path doesn't match the running exe."""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_REG_PATH, 0, winreg.KEY_READ)
        stored, _ = winreg.QueryValueEx(key, STARTUP_REG_NAME)
        winreg.CloseKey(key)
    except FileNotFoundError:
        return
    current_exe = sys.executable
    if current_exe.lower() not in stored.lower():
        set_startup_enabled(True)


def _launch_command(*args):
    """The command line that starts this app, however it happens to be running.

    Frozen, that's the exe itself. From source it's pythonw.exe plus main.py --
    pythonw specifically, since python.exe flashes a console window every time
    Windows launches us behind the user's back, which is exactly what both
    callers do.
    """
    if getattr(sys, "frozen", False):
        parts = [f'"{sys.executable}"']
    else:
        exe = sys.executable
        if exe.endswith("python.exe"):
            exe = exe.replace("python.exe", "pythonw.exe")
        script = str((Path(__file__).parent.parent / "main.py").resolve())
        parts = [f'"{exe}"', f'"{script}"']
    return " ".join(parts + list(args))


def set_startup_enabled(enable):
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_REG_PATH, 0, winreg.KEY_SET_VALUE)
    if enable:
        winreg.SetValueEx(key, STARTUP_REG_NAME, 0, winreg.REG_SZ,
                          _launch_command("--background"))
    else:
        try:
            winreg.DeleteValue(key, STARTUP_REG_NAME)
        except FileNotFoundError:
            pass
    winreg.CloseKey(key)


# ── Toast click activation ───────────────────────────────────────────


def register_show_protocol():
    """Register the sptchecker:// scheme so clicking a toast reaches this app.

    Rewritten on every launch rather than once, for the same reason
    refresh_startup_if_stale exists: the stored command holds an absolute exe
    path, and a user who moves or reinstalls the app would otherwise leave
    Windows pointing every toast click at a path that is no longer there --
    which fails silently, since a click that goes nowhere looks identical to a
    toast that simply isn't clickable.
    """
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, PROTOCOL_REG_PATH) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:SPTChecker")
            # It's the presence of this value, not its content, that marks the
            # key as a URI scheme. Without it Windows ignores the handler.
            winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                              rf"{PROTOCOL_REG_PATH}\shell\open\command") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, _launch_command('"%1"'))
    except OSError:
        # Losing this costs a toast click, not the app -- never fail startup.
        pass


_EVENT_MODIFY_STATE = 0x0002
_WAIT_OBJECT_0 = 0x00000000
_INFINITE = 0xFFFFFFFF


def _kernel32():
    """kernel32 with the three event calls fully declared.

    The declarations are not optional. A HANDLE is 64-bit on a 64-bit build,
    but ctypes defaults every return type to C int -- so an undeclared
    CreateEventW silently truncates the handle to 32 bits, and the wait that
    follows blocks on a handle that isn't the event. It fails invisibly,
    because a truncated handle is usually still a plausible-looking number.
    """
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL,
                                 wintypes.LPCWSTR]
    k32.CreateEventW.restype = wintypes.HANDLE
    k32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    k32.OpenEventW.restype = wintypes.HANDLE
    k32.SetEvent.argtypes = [wintypes.HANDLE]
    k32.SetEvent.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.WaitForSingleObject.restype = wintypes.DWORD
    return k32


def signal_running_instance():
    """Ask an already-running copy to show its window. True if one heard.

    False means nothing was listening, which the caller has to tell apart from
    success: it means the app isn't running, so the click should start it
    rather than being swallowed.
    """
    try:
        k32 = _kernel32()
        handle = k32.OpenEventW(_EVENT_MODIFY_STATE, False, SHOW_EVENT_NAME)
        if not handle:
            return False
        try:
            return bool(k32.SetEvent(handle))
        finally:
            k32.CloseHandle(handle)
    except OSError:
        return False


def create_show_event():
    """Create the event other instances signal to ask us to show ourselves.

    A named event rather than a polled flag file or a socket: waiting costs
    nothing while idle, there's no firewall prompt for a localhost port, and
    nothing is left on disk to go stale if the app is killed. Returns None if
    it can't be created, which just means toast clicks won't reach a running
    instance -- Windows still starts a fresh one.
    """
    try:
        # Auto-reset and initially unsignalled, so each request wakes the wait
        # exactly once and resets itself with no bookkeeping here.
        return _kernel32().CreateEventW(None, False, False, SHOW_EVENT_NAME) or None
    except OSError:
        return None


def wait_for_show_request(handle):
    """Block until someone signals the show event. False if the wait broke,
    which the caller should treat as "stop waiting" rather than retry -- a
    failing wait returns instantly and would otherwise spin a core."""
    try:
        return _kernel32().WaitForSingleObject(handle, _INFINITE) == _WAIT_OBJECT_0
    except OSError:
        return False


# ── Toast notifications ──────────────────────────────────────────────

_TOAST_ICON = str((ASSETS_DIR / "icon_256.png").resolve())


def send_toast(title, body, launch_url=None):
    try:
        toast = Notification(
            app_id="SPTChecker",
            title=title,
            msg=body,
            duration="long",
            icon=_TOAST_ICON,
            # Clicking the toast body raises the app. The Forge link stays on
            # its own button: the body is the much larger target, and "show me
            # the thing that just notified me" is the commoner intent -- a
            # toast listing several mods has no single page to open anyway.
            launch=SHOW_URI,
        )
        if launch_url:
            toast.add_actions(label="View on Forge", launch=launch_url)
        toast.show()
    except Exception:
        pass


# ── Tray / window icon ──────────────────────────────────────────────


def load_app_icon():
    icon_path = ASSETS_DIR / "icon.png"
    if icon_path.exists():
        return Image.open(icon_path)
    return _fallback_icon()


def badge_icon(image, color=ACCENT_DANGER):
    """Return a copy of image with a notification dot added to the top-right corner."""
    img = image.convert("RGBA").copy()
    w, _h = img.size
    r = max(6, w // 5)
    cx, cy = w - r - 2, r + 2
    draw = ImageDraw.Draw(img)
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color, outline=_rgb(BG), width=2)
    return img


def _fallback_icon():
    img = Image.new("RGBA", (64, 64), _rgb(BG))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([4, 4, 60, 60], radius=12, fill=_rgb(CARD_BG),
                           outline=_rgb(ACCENT_NEW), width=3)
    draw.text((14, 8), "SPT", fill=_rgb(TEXT_BRIGHT))
    return img
