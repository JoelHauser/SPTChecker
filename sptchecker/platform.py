import ctypes
import sys
import winreg
from pathlib import Path

from PIL import Image, ImageDraw
from winotify import Notification

from .config import ASSETS_DIR, STARTUP_REG_NAME, STARTUP_REG_PATH

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


def set_dark_title_bar(window, show=True):
    try:
        window.withdraw()
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1)
        for attr in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)
            ) == 0:
                break
        # DwmSetWindowAttribute alone doesn't always repaint the non-client
        # frame -- force Windows to redraw the title bar with the new value.
        ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, _SWP_FLAGS)
        if show:
            window.deiconify()
    except Exception:
        if show:
            window.deiconify()


_DWMWA_TRANSITIONS_FORCEDISABLED = 3


def disable_show_animation(window):
    """Stop Windows from playing its fade/expand animation when the window
    comes back from withdraw() -- that animation is what exposes an
    unpainted white backbuffer for a frame or two before Tk's own dark-themed
    repaint catches up, which is what looks like a "white flash" when
    restoring from the tray."""
    try:
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, _DWMWA_TRANSITIONS_FORCEDISABLED, ctypes.byref(value), ctypes.sizeof(value)
        )
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
            app_id="SPT Mod Checker",
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


def badge_icon(image, color="#e53935"):
    """Return a copy of image with a notification dot added to the top-right corner."""
    img = image.convert("RGBA").copy()
    w, _h = img.size
    r = max(6, w // 5)
    cx, cy = w - r - 2, r + 2
    draw = ImageDraw.Draw(img)
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color, outline=(26, 26, 36, 255), width=2)
    return img


def _fallback_icon():
    img = Image.new("RGBA", (64, 64), (26, 26, 36, 255))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([4, 4, 60, 60], radius=10, fill=(37, 37, 53, 255),
                           outline=(76, 175, 80, 255), width=3)
    draw.text((14, 8), "SPT", fill=(238, 238, 244, 255))
    return img
