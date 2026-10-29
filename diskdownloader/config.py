import locale
import os
import sys
from pathlib import Path


def system_language():
    if sys.platform == "win32":
        import ctypes
        return "ru" if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3ff == 0x19 else "en"
    values = [os.environ.get(k, "") for k in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG")]
    try:
        values.append(locale.getlocale()[0] or "")
    except ValueError:
        pass
    return "ru" if next((v for v in values if v), "en").lower().startswith("ru") else "en"


def state_directory():
    if os.environ.get("DISKDOWNLOADER_STATE"):
        result = Path(os.environ["DISKDOWNLOADER_STATE"])
    elif sys.platform == "win32":
        result = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "DiskDownloader"
    elif sys.platform == "darwin":
        result = Path.home() / "Library/Application Support/DiskDownloader"
    else:
        result = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "DiskDownloader"
    result.mkdir(parents=True, exist_ok=True, mode=0o700)
    return result
