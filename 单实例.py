"""小鲸鱼进程单实例锁：命名 Mutex + 可选端口锁。

Mutex 名固定清晰，避免后台多开空转吃 CPU。
已有实例时 acquire 返回 False；调用方可聚焦旧窗或直接退出。
"""
from __future__ import annotations

import atexit
import ctypes
import socket
from ctypes import wintypes

ERROR_ALREADY_EXISTS = 183

# 清晰命名，便于任务管理器 / 调试对照
MUTEX_VOICE_SETTINGS = 'Local\\XiaoJingYu_VoiceSettings'
MUTEX_VOICE_LOOP = 'Local\\XiaoJingYu_VoiceLoop'
MUTEX_SCREEN_VISION = 'Local\\XiaoJingYu_ScreenVision'
MUTEX_BUG_CATCHER = 'Local\\XiaoJingYu_BugCatcher'

# 兼容旧端口锁（双保险）
PORT_VOICE_LOOP = 19389
PORT_SCREEN_VISION = 19390
PORT_VOICE_SETTINGS = 19391

_kernel32 = ctypes.windll.kernel32
_kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.CreateMutexW.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL
_kernel32.GetLastError.restype = wintypes.DWORD


class SingleInstance:
    """持有命名 Mutex；可选再绑本地端口。进程退出时自动释放。"""

    def __init__(self, mutex_name: str, port: int | None = None):
        self.mutex_name = mutex_name
        self.port = port
        self.handle = None
        self.sock = None
        self.owned = False

    def acquire(self) -> bool:
        handle = _kernel32.CreateMutexW(None, True, self.mutex_name)
        if not handle:
            return False
        if _kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            _kernel32.CloseHandle(handle)
            return False
        self.handle = handle
        if self.port is not None:
            sock = socket.socket()
            try:
                sock.bind(('127.0.0.1', int(self.port)))
            except OSError:
                _kernel32.CloseHandle(handle)
                self.handle = None
                try:
                    sock.close()
                except OSError:
                    pass
                return False
            self.sock = sock
        self.owned = True
        atexit.register(self.release)
        return True

    def release(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
        if self.handle:
            try:
                _kernel32.CloseHandle(self.handle)
            except Exception:
                pass
            self.handle = None
        self.owned = False


def try_acquire(mutex_name: str, port: int | None = None) -> SingleInstance | None:
    lock = SingleInstance(mutex_name, port)
    if lock.acquire():
        return lock
    return None
