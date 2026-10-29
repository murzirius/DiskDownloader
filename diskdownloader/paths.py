"""Filesystem safeguards adapted from YaDiskDownloader (MIT, murzirius)."""
import ctypes
import hashlib
import os
import re
import stat
import unicodedata
from pathlib import Path

from .errors import AppError


def component(name):
    name = unicodedata.normalize("NFC", str(name))
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).rstrip(" .") or "unnamed"
    if re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])", name.split(".")[0]):
        name = "_" + name
    stem, ext = os.path.splitext(name)
    return stem.encode("utf-8")[:170].decode("utf-8", "ignore") + ext[:25]


def suffix(name, identity):
    stem, ext = os.path.splitext(name)
    return stem + "~" + hashlib.sha256(identity.encode()).hexdigest()[:12] + ext


def linked(p):
    return p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction()) or bool(
        getattr(p.lstat(), "st_file_attributes", 0) & 0x400)


def safe_path(root, relative="", *, mkdir=False):
    root = Path(root).absolute()
    if not isinstance(relative, str):
        raise AppError("unsafe_path", source="local")
    parts = relative.split("/") if relative else []
    if any(not p or p in (".", "..") or ":" in p or "\\" in p or p.endswith((".", " ")) for p in parts):
        raise AppError("unsafe_path", source="local")
    for current in [*reversed(root.parents), root]:
        if os.path.lexists(current) and linked(current):
            raise AppError("unsafe_path", source="local")
    if mkdir:
        root.mkdir(parents=True, exist_ok=True)
    current = root
    for index, part in enumerate(parts):
        current = current / part
        if os.path.lexists(current) and linked(current):
            raise AppError("unsafe_path", source="local")
        if mkdir and index < len(parts) - 1:
            current.mkdir(exist_ok=True)
    return current


def regular_open(path, mode="rb"):
    path = Path(path)
    if os.path.lexists(path) and linked(path):
        raise AppError("unsafe_path", source="local")
    flags = os.O_RDONLY if mode == "rb" else os.O_RDWR
    if mode == "xb":
        flags |= os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or (mode != "rb" and info.st_nlink != 1):
        os.close(fd)
        raise AppError("unsafe_path", source="local")
    return os.fdopen(fd, mode)


def publish(part, target):
    if os.name == "nt":
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        move = kernel.MoveFileExW
        move.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD)
        move.restype = wintypes.BOOL
        if not move(str(part), str(target), 8):
            raise ctypes.WinError(ctypes.get_last_error())
    else:
        os.link(part, target)
        os.unlink(part)
        fd = os.open(Path(target).parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def set_modified(path, stamp):
    if os.name != "nt":
        os.utime(path, (stamp, stamp), follow_symlinks=False)
        return
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    handle = create(str(path),0x100,7,None,3,0x200000,None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    try:
        ticks = int((stamp + 11644473600) * 10000000)
        if not 0 <= ticks < 2 ** 64:
            raise ValueError("timestamp out of range")
        written = wintypes.FILETIME(ticks & 0xffffffff, ticks >> 32)
        set_time = kernel.SetFileTime
        set_time.argtypes = (wintypes.HANDLE,ctypes.c_void_p,ctypes.c_void_p,ctypes.POINTER(wintypes.FILETIME))
        set_time.restype = wintypes.BOOL
        if not set_time(handle,None,None,ctypes.byref(written)):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        close(handle)
