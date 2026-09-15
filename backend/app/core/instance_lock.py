"""Single-instance guard.

Task progress, the stats-refresh lock and the scheduler all live in process
memory, so two server processes on one data directory (``uvicorn --workers 2``,
or launching the app twice) would double-run syncs and double-count stats. We
take an advisory ``flock`` on ``<data>/server.lock`` at startup and refuse to
start if another live process holds it. The lock is released by the OS when
the process exits, so a crash never leaves it stuck.
"""
from __future__ import annotations

import fcntl
import os
from pathlib import Path

_handle = None  # keep the fd alive for the process lifetime


class AnotherInstanceRunning(RuntimeError):
    pass


def acquire(data_dir: Path) -> None:
    global _handle
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "server.lock"
    fh = open(path, "a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.seek(0)
        other = fh.read().strip() or "未知"
        fh.close()
        raise AnotherInstanceRunning(
            f"数据目录 {data_dir} 已被另一个 Chat Analyzer 进程占用（PID {other}）。"
            "本应用必须以单进程运行：不要使用 uvicorn --workers，也不要重复启动。"
        )
    fh.seek(0)
    fh.truncate()
    fh.write(str(os.getpid()))
    fh.flush()
    _handle = fh


def release() -> None:
    global _handle
    if _handle is not None:
        try:
            fcntl.flock(_handle.fileno(), fcntl.LOCK_UN)
            _handle.close()
        finally:
            _handle = None
