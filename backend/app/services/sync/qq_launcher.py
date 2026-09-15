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
import json
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
QCE_DATA_DIR = STACK_DIR / "qce-data"
SECURITY_FILE = QCE_DATA_DIR / "security.json"  # QCE 的权威 accessToken 落在这里
COMPOSE_FILE = STACK_DIR / "docker-compose.yml"
VERSION_FILE = STACK_DIR / ".installed-version"

# QCE v5 release packs as a single npm-style plugin folder.
QCE_PLUGIN_NAME = "napcat-plugin-qce"

CONTAINER_NAME = "chat-analyzer-napcat"
NAPCAT_WEBUI_PORT = 6099  # NapCat's own web login UI (QR code lives here)
QCE_PORT = 40653

RELEASE_REPO = "https://api.github.com/repos/shuakami/qq-chat-exporter/releases"
RELEASE_API = f"{RELEASE_REPO}/latest"
NAPCAT_IMAGE = "mlikiowa/napcat-docker:latest"

# 钉住 QCE 版本。v6 起插件改成预编译二进制 + 独立 NapCat bridge，当前镜像的
# NapCat 4.18.1 加载不起来；v5.x 的插件自带 node_modules，与本项目客户端匹配。
# 置为 None 则跟随 releases/latest。
QCE_PINNED_VERSION: str | None = "v5.5.79"

# Regex for QCE printing token on startup. v5 prints `[QCE] Token: <40-char>`.
TOKEN_PATTERNS = [
    re.compile(r"\[QCE\]\s*Token[:\s：=]+([A-Za-z0-9_\-]{16,})"),
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
    url = f"{RELEASE_REPO}/tags/{QCE_PINNED_VERSION}" if QCE_PINNED_VERSION else RELEASE_API
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(url, headers={"Accept": "application/vnd.github+json"})
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
    cfg = load_config().qq
    account = cfg.uin or "0"
    compose_yaml = f"""services:
  napcat:
    image: {NAPCAT_IMAGE}
    container_name: {CONTAINER_NAME}
    restart: unless-stopped
    environment:
      - ACCOUNT={account}
      - MODE=shell
      - WEBUI_PORT={NAPCAT_WEBUI_PORT}
    ports:
      - "127.0.0.1:{NAPCAT_WEBUI_PORT}:{NAPCAT_WEBUI_PORT}"
      - "127.0.0.1:{QCE_PORT}:{QCE_PORT}"
    volumes:
      - ./config:/app/napcat/config
      - ./plugins:/app/napcat/plugins
      - ./qq-data:/app/.config/QQ
      - ./qce-data:/app/.qq-chat-exporter
"""
    COMPOSE_FILE.write_text(compose_yaml)


def _write_plugins_json() -> None:
    (CONFIG_DIR / "plugins.json").write_text(
        '{\n  "' + QCE_PLUGIN_NAME + '": true\n}\n'
    )


def _ensure_qce_security_disable_ipwhitelist() -> None:
    """QCE defaults disableIPWhitelist=false, which blocks access via Docker
    port-forward (source IP becomes the bridge gateway). Pre-seed the file so
    the toggle persists across container restarts via the qce-data bind mount."""
    QCE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    sec_path = QCE_DATA_DIR / "security.json"
    try:
        data = json.loads(sec_path.read_text()) if sec_path.exists() else {}
    except Exception:
        data = {}
    if data.get("disableIPWhitelist") is True:
        return
    data["disableIPWhitelist"] = True
    sec_path.write_text(json.dumps(data, indent=2))


def _extract_all(zf: zipfile.ZipFile, dest: Path) -> None:
    """extractall() 丢弃 zip 里存的 Unix 权限位。QCE v6 起插件带的是预编译
    server 二进制（bin/linux-x64/qce-server, 0755），丢了执行位就起不来。

    每个条目都校验解压路径落在 dest 之内再 chmod：防 zip-slip（`../` 或绝对路径
    条目名把 chmod 打到宿主任意文件上）。"""
    dest_resolved = dest.resolve()
    zf.extractall(dest)
    for info in zf.infolist():
        if info.is_dir():
            continue
        target = (dest / info.filename).resolve()
        if not target.is_relative_to(dest_resolved):
            raise LauncherError(f"插件包含越界路径条目，已拒绝: {info.filename}")
        mode = (info.external_attr >> 16) & 0o777
        if mode:
            target.chmod(mode)


def _extract_zip(zip_bytes: bytes, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        _extract_all(zf, dest)


def _stage_plugin(plugin_zip: bytes) -> None:
    """Synchronous: extract the plugin zip and atomically swap it into place.
    Runs in a worker thread (extract + move of a node_modules tree is heavy).

    Stages a complete new directory before swapping in, so a running QCE never
    sees a half-populated plugin dir (GET fine, on-demand require → 500)."""
    plugin_dest = PLUGINS_DIR / QCE_PLUGIN_NAME
    staged = PLUGINS_DIR / f".{QCE_PLUGIN_NAME}.staged"
    retired = PLUGINS_DIR / f".{QCE_PLUGIN_NAME}.retired"
    for path in (staged, retired):
        if path.exists():
            shutil.rmtree(path)

    with zipfile.ZipFile(io.BytesIO(plugin_zip)) as zf:
        tmp = STACK_DIR / ".plugin-extract"
        if tmp.exists():
            shutil.rmtree(tmp)
        _extract_all(zf, tmp)

    # The zip may be rooted (package.json at top) or wrapped in one folder.
    plugin_src = _find_plugin_root(tmp)
    if not plugin_src:
        raise LauncherError("插件包结构异常：找不到 package.json")
    shutil.move(str(plugin_src), staged)
    shutil.rmtree(tmp, ignore_errors=True)

    if plugin_dest.exists():
        plugin_dest.rename(retired)
    staged.rename(plugin_dest)
    shutil.rmtree(retired, ignore_errors=True)


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
        # QCE v5 ships the plugin as a single npm package directory:
        # package.json + index.mjs + node_modules + webui at the zip root.
        plugin_asset = _find_asset(release, "napcat-plugin-qce.zip")
        async with httpx.AsyncClient(timeout=600, follow_redirects=True) as client:
            r = await client.get(plugin_asset["browser_download_url"])
            r.raise_for_status()
            plugin_zip = r.content

        # Unzip + move thousands of node_modules files off the event loop so the
        # install doesn't freeze /api/events heartbeats and other in-flight work.
        await asyncio.to_thread(_stage_plugin, plugin_zip)

    # compose + plugin config (always rewritten; cheap, keeps in sync)
    _write_compose()
    _write_plugins_json()
    _ensure_qce_security_disable_ipwhitelist()
    VERSION_FILE.write_text(version)

    return {
        "version": version,
        "already_installed": already,
        "stack_dir": str(STACK_DIR),
        # 换入新目录只影响尚未加载的模块，运行中的实例仍是旧版；要真正生效得重启容器。
        "restart_required": not already and await _container_state() == "running",
    }


def _find_plugin_root(root: Path) -> Path | None:
    """Locate the directory containing the plugin's package.json."""
    for pkg in root.rglob("package.json"):
        try:
            import json
            data = json.loads(pkg.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("name") == QCE_PLUGIN_NAME:
            return pkg.parent
    # Fallback: zip rooted directly with package.json at top.
    if (root / "package.json").exists():
        return root
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
    asyncio.create_task(_watch_token_file())
    return await status()


async def stop() -> dict[str, Any]:
    if not COMPOSE_FILE.exists():
        return await status()
    await _run(["docker", "compose", "-f", str(COMPOSE_FILE), "down"], check=False, timeout=60)
    global _log_task
    if _log_task and not _log_task.done():
        _log_task.cancel()
    return await status()


def _redact_tokens(text: str) -> str:
    """Mask QCE access tokens before returning logs to any client — otherwise
    /api/qq/launcher/logs leaks the exact value /api/config redacts."""
    for pat in TOKEN_PATTERNS:
        text = pat.sub(lambda m: m.group(0).replace(m.group(1), "********"), text)
    return text


async def logs(tail: int = 200) -> str:
    state = await _container_state()
    if state is None:
        return "(容器未创建)"
    code, out, _ = await _run(
        ["docker", "logs", "--tail", str(tail), CONTAINER_NAME],
        check=False, timeout=10,
    )
    return _redact_tokens(out) if code == 0 else "(读取日志失败)"


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


def _token_from_disk() -> str | None:
    """QCE 的权威凭据是 security.json 里的 accessToken。启动日志打的那行可能是
    轮换前的旧值（实测出现过），拿它去请求会静默 403，所以优先读文件。"""
    try:
        return json.loads(SECURITY_FILE.read_text(encoding="utf-8")).get("accessToken") or None
    except Exception:
        return None


async def _watch_token_file(timeout: float = 120.0) -> None:
    """QCE 起来后才写 security.json，值也可能在启动过程中轮换；盯一段时间。"""
    seen: str | None = None
    for _ in range(max(1, int(timeout // 3))):
        token = _token_from_disk()
        if token and token != seen:
            seen = token
            _persist_token(token)
        await asyncio.sleep(3)


def _persist_token(token: str) -> None:
    token = _token_from_disk() or token  # 文件优先，日志仅兜底
    cfg = load_config()
    if cfg.qq.token == token:
        return
    cfg.qq.token = token
    cfg.qq.host = "127.0.0.1"
    cfg.qq.port = QCE_PORT
    save_config(cfg)
    log.info("QCE token persisted (len=%d)", len(token))
