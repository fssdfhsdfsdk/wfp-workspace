"""协作式取消与进程树终止。

Implements: DR-35, DR-36, DR-39, DR-59, DR-64
非 builtin 动作在独立进程组/Job Object 中执行；取消 3s 温和终止、10s 强杀。
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from typing import Any

from wfp.errors import WfpError


class Cancelled(WfpError):
    code = "WFP-E-CANCELLED"
    http_status = 409


class CancelToken:
    def __init__(self) -> None:
        self._ev = threading.Event()

    def cancel(self) -> None:
        self._ev.set()

    @property
    def cancelled(self) -> bool:
        return self._ev.is_set()

    def check(self) -> None:
        if self._ev.is_set():
            raise Cancelled("运行已取消")

    def wait(self, timeout: float) -> bool:
        return self._ev.wait(timeout)


def popen_kwargs(*, console: str = "hidden") -> dict[str, Any]:
    """DR-36：默认无窗口、无交互 stdin；POSIX 用 setsid 便于 killpg。"""
    kw: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
    }
    if sys.platform == "win32":
        creationflags = 0x08000000  # CREATE_NO_WINDOW
        if console != "visible":
            creationflags |= 0x00000008  # DETACHED_PROCESS（无黑窗）
        kw["creationflags"] = creationflags
    else:
        kw["start_new_session"] = console != "visible"
    return kw


class ProcessHandle:
    """子进程句柄；terminate_tree 保证父/子/孙进程都被回收（DR-35）。"""

    def __init__(self, proc: subprocess.Popen[str]) -> None:
        self.proc = proc
        self.pid = proc.pid

    def _children(self) -> list[int]:
        try:
            import psutil  # type: ignore
            p = psutil.Process(self.pid)
            return [c.pid for c in p.children(recursive=True)]
        except Exception:  # noqa: BLE001 - psutil 为可选兜底
            return []

    def terminate_tree(self, *, grace: float = 3.0, force_after: float = 10.0) -> None:
        if self.proc.poll() is not None:
            return
        kids = self._children()
        if sys.platform == "win32":
            try:
                self.proc.terminate()
            except OSError:
                pass
            subprocess.run(["taskkill", "/PID", str(self.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        else:
            try:
                os.killpg(os.getpgid(self.pid), 15)  # SIGTERM
            except (ProcessLookupError, PermissionError, OSError):
                self.proc.terminate()
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline and self.proc.poll() is None:
            time.sleep(0.02)
        if self.proc.poll() is None:
            if sys.platform != "win32":
                try:
                    os.killpg(os.getpgid(self.pid), 9)  # SIGKILL
                except (ProcessLookupError, PermissionError, OSError):
                    self.proc.kill()
            else:
                self.proc.kill()
            for kid in kids:
                try:
                    os.kill(kid, 9)
                except OSError:
                    pass
        try:
            self.proc.wait(timeout=max(0.1, force_after - grace))
        except subprocess.TimeoutExpired:
            self.proc.kill()

    @property
    def alive(self) -> bool:
        return self.proc.poll() is None


__all__ = ["CancelToken", "Cancelled", "ProcessHandle", "popen_kwargs"]
