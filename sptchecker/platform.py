import ctypes
import sys
import winreg
from pathlib import Path

from PIL import Image, ImageDraw
from winotify import Notification

from .config import (
    ACCENT_DANGER, ACCENT_NEW, ASSETS_DIR, BG, CARD_BG, STARTUP_REG_NAME,
    STARTUP_REG_PATH, TEXT_BRIGHT,
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


def set_startup_enabled(enable):
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_REG_PATH, 0, winreg.KEY_SET_VALUE)
    if enable:
        if getattr(sys, "frozen", False):
            cmd = f'"{sys.executable}" --background'
        else:
            exe = sys.executable
            if exe.endswith("python.exe"):
                exe = exe.replace("python.exe", "pythonw.exe")
            script = str((Path(__file__).parent.parent / "main.py").resolve())
            cmd = f'"{exe}" "{script}" --background'
        winreg.SetValueEx(key, STARTUP_REG_NAME, 0, winreg.REG_SZ, cmd)
    else:
        try:
            winreg.DeleteValue(key, STARTUP_REG_NAME)
        except FileNotFoundError:
            pass
    winreg.CloseKey(key)


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
