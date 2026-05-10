"""Docker-based launcher for NapCat + QCE plugin.

Workflow (mac / linux / wsl host):
    1. `ensure_installed()` downloads QCE plugin zip and prepares the stack
       directory `~/.chat-analyzer/qq-stack/`.
    2. `start()` runs `docker compose up -d`, then tails `napcat` logs in a
       background task, scraping the QCE access token and writing it into
       `config.qq`.
    3. `stop()` / `status()` / `logs()` are self-explanatory.

Why Docker: mac has no native NapCat build, and running QQNT desktop +
NapCat injection out-of-band is brittle. The `mlikiowa/napcat-docker` image
runs headless QQNT, QCE rides on top as a plugin, and the whole thing is
isolated from the host.
"""

from __future__ import annotations

import asyncio
import io
import logging
import platform
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any

import httpx

from app.core.config import load_config, save_config

log = logging.getLogger(__name__)

STACK_DIR = Path.home() / ".chat-analyzer" / "qq-stack"
PLUGINS_DIR = STACK_DIR / "plugins"
CONFIG_DIR = STACK_DIR / "config"
FRONTEND_DIR = STACK_DIR / "qce-v4-tool"
COMPOSE_FILE = STACK_DIR / "docker-compose.yml"
VERSION_FILE = STACK_DIR / ".installed-version"

CONTAINER_NAME = "chat-analyzer-napcat"
NAPCAT_WEBUI_PORT = 6099  # NapCat's own web login UI (QR code lives here)
QCE_PORT = 40653

RELEASE_API = "https://api.github.com/repos/shuakami/qq-chat-exporter/releases/latest"
NAPCAT_IMAGE = "mlikiowa/napcat-docker:latest"

# Regex for QCE printing `Access Token: <token>` on startup. The exact format
# may shift across versions so we match loosely.
TOKEN_PATTERNS = [
    re.compile(r"(?:access[_\s-]?token|qce[_\s-]?token)[\s:：=]+([A-Za-z0-9_\-]{8,})", re.I),
    re.compile(r"Token[\s:：=]+([A-Za-z0-9_\-]{16,})"),
]

# Module-level state. There's only one stack per process; keep it simple.
_log_task: asyncio.Task | None = None


class LauncherError(RuntimeError):
    pass


# ---- Docker detection ------------------------------------------------------


async def _run(cmd: list[str], *, check: bool = True, timeout: float = 30.0) -> tuple[int, str, str]:
    """Run a shell command, return (code, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise LauncherError(f"命令超时: {' '.join(cmd)}")
    out = stdout.decode("utf-8", errors="replace")
    err = stderr.decode("utf-8", errors="replace")
    if check and proc.returncode != 0:
        raise LauncherError(f"{' '.join(cmd)} 失败:\n{err or out}")
    return proc.returncode or 0, out, err


async def check_docker() -> dict[str, Any]:
    """Probe Docker CLI + Daemon readiness. Never raises."""
    result: dict[str, Any] = {"installed": False, "daemon": False, "compose": False}
    if not shutil.which("docker"):
        result["error"] = "未检测到 docker 命令，请先安装 Docker Desktop"
        return result
    result["installed"] = True
    try:
        code, out, _ = await _run(["docker", "version", "--format", "{{.Server.Version}}"], check=False, timeout=5)
        result["daemon"] = code == 0 and out.strip() != ""
        if result["daemon"]:
            result["server_version"] = out.strip()
        else:
            result["error"] = "Docker Daemon 未运行，请打开 Docker Desktop"
    except LauncherError as e:
        result["error"] = str(e)
        return result
    code, _, _ = await _run(["docker", "compose", "version"], check=False, timeout=5)
    result["compose"] = code == 0
    if not result["compose"]:
        result["error"] = "docker compose 插件不可用"
    result["arch"] = platform.machine()
    return result


# ---- Installation ----------------------------------------------------------


async def _fetch_latest_release() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(RELEASE_API, headers={"Accept": "application/vnd.github+json"})
        r.raise_for_status()
        return r.json()


def _find_asset(release: dict[str, Any], name_hint: str) -> dict[str, Any]:
    for a in release.get("assets", []):
        if name_hint in a.get("name", ""):
            return a
    raise LauncherError(f"Release 里找不到 {name_hint}")


async def _download_to(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=600, follow_redirects=True) as client:
        async with client.stream("GET", url) as r:
            r.raise_for_status()
            with dest.open("wb") as f:
                async for chunk in r.aiter_bytes(chunk_size=64 * 1024):
                    f.write(chunk)


def _write_compose() -> None:
    compose_yaml = f"""services:
  napcat:
    image: {NAPCAT_IMAGE}
    container_name: {CONTAINER_NAME}
    restart: unless-stopped
    environment:
      - ACCOUNT=0
      - MODE=shell
      - WEBUI_PORT={NAPCAT_WEBUI_PORT}
    ports:
      - "127.0.0.1:{NAPCAT_WEBUI_PORT}:{NAPCAT_WEBUI_PORT}"
      - "127.0.0.1:{QCE_PORT}:{QCE_PORT}"
    volumes:
      - ./config:/app/napcat/config
      - ./plugins:/app/napcat/plugins
      - ./qce-v4-tool:/app/napcat/static/qce-v4-tool
      - ./qq-data:/app/.config/QQ
"""
    COMPOSE_FILE.write_text(compose_yaml)


def _write_plugins_json() -> None:
    (CONFIG_DIR / "plugins.json").write_text(
        '{\n  "napcat-plugin-builtin": true,\n  "qq-chat-exporter": true\n}\n'
    )


def _extract_zip(zip_bytes: bytes, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        zf.extractall(dest)


async def _copy_builtin_plugin_from_image() -> None:
    """The napcat image ships a `napcat-plugin-builtin` which our bind-mount
    would shadow. Copy it out once so both plugins coexist."""
    dest = PLUGINS_DIR / "napcat-plugin-builtin"
    if dest.exists():
        return
    # Pull image first (idempotent, cheap after first time)
    await _run(["docker", "pull", NAPCAT_IMAGE], timeout=600)
    # Create a temporary container to copy from
    _, cid, _ = await _run(["docker", "create", NAPCAT_IMAGE], timeout=30)
    container_id = cid.strip()
    try:
        await _run(
            ["docker", "cp", f"{container_id}:/app/napcat/plugins/napcat-plugin-builtin", str(dest)],
            timeout=60,
        )
    finally:
        await _run(["docker", "rm", container_id], check=False, timeout=30)


async def ensure_installed(force: bool = False) -> dict[str, Any]:
    """Idempotent: download QCE plugin + prepare compose stack."""
    STACK_DIR.mkdir(parents=True, exist_ok=True)
    PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    release = await _fetch_latest_release()
    version = release.get("tag_name", "unknown")
    installed = VERSION_FILE.read_text().strip() if VERSION_FILE.exists() else ""
    already = installed == version and not force

    if not already:
        # 1. Plugin bundle → plugins/qq-chat-exporter/ + static/qce-v4-tool/
        plugin_asset = _find_asset(release, "napcat-plugin-qce.zip")
        async with httpx.AsyncClient(timeout=600, follow_redirects=True) as client:
            r = await client.get(plugin_asset["browser_download_url"])
            r.raise_for_status()
            plugin_zip = r.content

        # Clean stale install
        if (PLUGINS_DIR / "qq-chat-exporter").exists():
            shutil.rmtree(PLUGINS_DIR / "qq-chat-exporter")
        if FRONTEND_DIR.exists():
            shutil.rmtree(FRONTEND_DIR)

        # Layout inside the zip: either rooted or nested under a folder.
        # We detect by scanning top-level entries.
        with zipfile.ZipFile(io.BytesIO(plugin_zip)) as zf:
            names = zf.namelist()
            roots = {n.split("/", 1)[0] for n in names if n}
            # Extract directly; downstream code expects these paths
            tmp = STACK_DIR / ".plugin-extract"
            if tmp.exists():
                shutil.rmtree(tmp)
            zf.extractall(tmp)

        # Locate qq-chat-exporter and qce-v4-tool anywhere in the extracted tree
        plugin_src = _find_dir(tmp, "qq-chat-exporter")
        frontend_src = _find_dir(tmp, "qce-v4-tool")
        if not plugin_src:
            raise LauncherError("插件包中未找到 qq-chat-exporter 目录")
        shutil.move(str(plugin_src), PLUGINS_DIR / "qq-chat-exporter")
        if frontend_src:
            shutil.move(str(frontend_src), FRONTEND_DIR)
        else:
            FRONTEND_DIR.mkdir(parents=True, exist_ok=True)  # placeholder mount

        shutil.rmtree(tmp, ignore_errors=True)
        _ = roots  # keep for debug

    # 2. builtin plugin copy (independent of version; checks exist internally)
    await _copy_builtin_plugin_from_image()

    # 3. compose + plugin config (always rewritten; cheap, keeps in sync)
    _write_compose()
    _write_plugins_json()
    VERSION_FILE.write_text(version)

    return {
        "version": version,
        "already_installed": already,
        "stack_dir": str(STACK_DIR),
    }


def _find_dir(root: Path, name: str) -> Path | None:
    for p in root.rglob(name):
        if p.is_dir():
            return p
    return None


# ---- Runtime control -------------------------------------------------------


async def _container_state() -> str | None:
    code, out, _ = await _run(
        ["docker", "inspect", "-f", "{{.State.Status}}", CONTAINER_NAME],
        check=False, timeout=5,
    )
    if code != 0:
        return None
    return out.strip() or None


async def status() -> dict[str, Any]:
    docker = await check_docker()
    cfg = load_config().qq
    out: dict[str, Any] = {
        "docker": docker,
        "installed": COMPOSE_FILE.exists(),
        "installed_version": VERSION_FILE.read_text().strip() if VERSION_FILE.exists() else None,
        "container": None,
        "webui_url": f"http://127.0.0.1:{NAPCAT_WEBUI_PORT}",
        "qce_url": f"http://127.0.0.1:{QCE_PORT}",
        "token_captured": bool(cfg.token),
        "qq_enabled": cfg.enabled,
    }
    if docker.get("daemon"):
        out["container"] = await _container_state()
    return out


async def start() -> dict[str, Any]:
    if not COMPOSE_FILE.exists():
        raise LauncherError("尚未安装，请先调用 /api/qq/launcher/install")
    await _run(["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d"], timeout=120)
    _start_log_tail()
    return await status()


async def stop() -> dict[str, Any]:
    if not COMPOSE_FILE.exists():
        return await status()
    await _run(["docker", "compose", "-f", str(COMPOSE_FILE), "down"], check=False, timeout=60)
    global _log_task
    if _log_task and not _log_task.done():
        _log_task.cancel()
    return await status()


async def logs(tail: int = 200) -> str:
    state = await _container_state()
    if state is None:
        return "(容器未创建)"
    code, out, _ = await _run(
        ["docker", "logs", "--tail", str(tail), CONTAINER_NAME],
        check=False, timeout=10,
    )
    return out if code == 0 else "(读取日志失败)"


# ---- Token auto-capture ----------------------------------------------------


def _start_log_tail() -> None:
    global _log_task
    if _log_task and not _log_task.done():
        return
    _log_task = asyncio.create_task(_tail_logs())


async def _tail_logs() -> None:
    """Follow container logs and auto-capture the QCE token on first sighting."""
    # docker logs -f: blocks until the container exits. We pipe line-by-line.
    proc = await asyncio.create_subprocess_exec(
        "docker", "logs", "-f", "--tail", "50", CONTAINER_NAME,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert proc.stdout is not None

    try:
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace")
            token = _extract_token(text)
            if token:
                _persist_token(token)
                # Keep streaming — the user may see more useful logs, but we've done our job.
    except asyncio.CancelledError:
        proc.terminate()
        raise
    finally:
        try:
            proc.terminate()
        except ProcessLookupError:
            pass


def _extract_token(line: str) -> str | None:
    for pat in TOKEN_PATTERNS:
        m = pat.search(line)
        if m:
            return m.group(1)
    return None


def _persist_token(token: str) -> None:
    cfg = load_config()
    if cfg.qq.token == token:
        return
    cfg.qq.token = token
    cfg.qq.host = "127.0.0.1"
    cfg.qq.port = QCE_PORT
    save_config(cfg)
    log.info("QCE token captured from container logs (len=%d)", len(token))
