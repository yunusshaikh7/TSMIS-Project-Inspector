"""Pure Win32/ctypes helpers for the desktop window: the own-window lookup
(PID-matched, so we never touch another process's same-titled window), the
window icon, and the last-resort message box. No GUI state here."""
import ctypes
import os


def find_own_window(title):
    from ctypes import wintypes
    u32 = ctypes.windll.user32
    u32.GetWindowThreadProcessId.restype = wintypes.DWORD
    u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    u32.GetWindowTextLengthW.restype = ctypes.c_int
    u32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    u32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    my_pid = os.getpid()
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _lparam):
        pid = wintypes.DWORD(0)
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value != my_pid:
            return True
        n = u32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        u32.GetWindowTextW(hwnd, buf, n + 1)
        if buf.value == title:
            found.append(hwnd)
            return False
        return True

    u32.EnumWindows(_cb, 0)
    return found[0] if found else None


def set_window_icon(hwnd, ico_path):
    from ctypes import wintypes
    u32 = ctypes.windll.user32
    u32.LoadImageW.restype = wintypes.HANDLE
    u32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    LR_LOADFROMFILE, IMAGE_ICON, WM_SETICON = 0x10, 1, 0x80
    for which, size in ((0, 16), (1, 32)):
        hicon = u32.LoadImageW(None, str(ico_path), IMAGE_ICON, size, size, LR_LOADFROMFILE)
        if hicon:
            u32.SendMessageW(hwnd, WM_SETICON, which, hicon)


def message_box(text, title, flags=0x10):          # 0x10 = MB_ICONERROR
    ctypes.windll.user32.MessageBoxW(0, text, title, flags)
