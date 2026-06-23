import ctypes
import sys
import winreg
from pathlib import Path

from PIL import Image, ImageDraw
from winotify import Notification

from .config import STARTUP_REG_NAME, STARTUP_REG_PATH

# ── Dark title bar ─────────────────────────────────────────────────────


def set_dark_title_bar(window):
    try:
        window.update()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1)
        for attr in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)
            ) == 0:
                break
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


def set_startup_enabled(enable):
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_REG_PATH, 0, winreg.KEY_SET_VALUE)
    if enable:
        exe = sys.executable
        if exe.endswith("python.exe"):
            exe = exe.replace("python.exe", "pythonw.exe")
        script = str((Path(__file__).parent.parent / "main.py").resolve())
        winreg.SetValueEx(key, STARTUP_REG_NAME, 0, winreg.REG_SZ,
                          f'"{exe}" "{script}" --background')
    else:
        try:
            winreg.DeleteValue(key, STARTUP_REG_NAME)
        except FileNotFoundError:
            pass
    winreg.CloseKey(key)


# ── Toast notifications ──────────────────────────────────────────────


def send_toast(title, body, launch_url=None):
    try:
        toast = Notification(
            app_id="SPT Mod Checker",
            title=title,
            msg=body,
            duration="long",
        )
        if launch_url:
            toast.add_actions(label="View on Forge", launch=launch_url)
        toast.show()
    except Exception:
        pass


# ── Tray icon ────────────────────────────────────────────────────────


def create_tray_image():
    img = Image.new("RGBA", (64, 64), (26, 26, 36, 255))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([4, 4, 60, 60], radius=10, fill=(37, 37, 53, 255),
                           outline=(76, 175, 80, 255), width=3)
    draw.text((14, 8), "SPT", fill=(238, 238, 244, 255))
    return img
