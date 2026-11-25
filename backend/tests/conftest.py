"""Tests never use the user's accounts, chat store, subprocesses or network."""
import asyncio
import os
import socket
import tempfile

import pytest

_home = tempfile.TemporaryDirectory(prefix="chat-analyzer-tests-")
os.environ["CHAT_ANALYZER_HOME"] = _home.name


@pytest.fixture(autouse=True)
def isolate_side_effects(monkeypatch):
    from app.core import tasks
    tasks._tasks.clear()
    tasks._task_handles.clear()

    def no_network(*_args, **_kwargs):
        raise AssertionError("Tests must mock external connections")

    async def no_subprocess(*_args, **_kwargs):
        raise AssertionError("Tests must mock external processes")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", no_subprocess)
    yield
    tasks._tasks.clear()
    tasks._task_handles.clear()
