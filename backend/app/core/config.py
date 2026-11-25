import json
import os
from pathlib import Path
from typing import Literal
from pydantic import BaseModel

# Data lives outside the repo. Override with CHAT_ANALYZER_HOME to move it to
# another disk / machine (copy the whole directory, then point this at it).
BASE_DIR = Path(os.environ.get("CHAT_ANALYZER_HOME") or (Path.home() / ".chat-analyzer")).expanduser()
DATA_DIR = BASE_DIR / "data"
CONFIG_PATH = BASE_DIR / "config.json"


class PreFilterConfig(BaseModel):
    skip_content: list[str] = [
        r"^[哈嘿呵哦嗯啊噢]{2,}$",
        r"^[👍👌🎉😂🤣😅🙏❤️💪😎🤝\s]+$",
        r"^(在吗|在不在|嗯|哦|好|好的|ok|OK|收到|嗯嗯|行|可以|对|是的)$",
        r"^\[.*\]$",
    ]
    skip_msg_types: list[str] = ["system", "sticker"]  # cross-platform kinds (messages.kind), not raw msg_type
    min_content_length: int = 2


class ChatRef(BaseModel):
    platform: str
    chat_id: str

    def key(self) -> tuple[str, str]:
        return (self.platform, self.chat_id)


class ChatFilterConfig(BaseModel):
    """Analysis scope. `refs` is the real key; `chats` (display names) is the
    legacy form, resolved to refs at startup and kept only until it's empty."""
    mode: str = "whitelist"  # whitelist: 只分析列出的群 / blacklist: 分析除列出的以外的群
    chats: list[str] = []    # legacy: 群聊名称列表（会在启动时迁移到 refs）
    refs: list[ChatRef] = []


class LLMConfig(BaseModel):
    provider: str = "claude_cli"            # "claude_cli" | "openai_compatible"
    api_url: str = "http://localhost:1234/v1"
    model: str = ""
    api_key: str = "lm-studio"
    embedding_model: str = ""                # "" disables semantic features; otherwise OpenAI-compat /v1/embeddings model id


class WeChatConfig(BaseModel):
    """wx-cli reads whichever WeChat is logged in on this Mac and exposes no
    account identity, so multi-account users label the current one here. The
    label is stamped onto messages.account and becomes the 主号/多号 switch."""
    account: str = ""


class QQConfig(BaseModel):
    source: Literal["qce", "local"] = "qce"
    local_path: str = ""
    local_account: str = ""
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 40653
    token: str = ""
    uin: str = ""  # logged-in QQ number, used by launcher to enable quick login


class TelegramConfig(BaseModel):
    """Telethon account-API integration. session_string is account-equivalent — keep it secret."""
    enabled: bool = False
    api_id: int = 0
    api_hash: str = ""
    phone: str = ""
    session_string: str = ""
    username: str = ""  # cached display name from /api/telegram/status


class StorageConfig(BaseModel):
    keep_raw_text: bool = False  # keep raw_data JSON for plain-text WeChat/Telegram messages (≈4× content size)


class SyncScopeConfig(BaseModel):
    """What gets ingested at all. Filtering at the door keeps the DB, FTS index,
    backups and every rollup proportional to what you actually analyse."""
    exclude_channels: bool = True      # Telegram broadcast channels: one-way feeds, no conversation
    exclude_chats: list[ChatRef] = []  # never sync these (per platform + chat_id)


class SchedulerConfig(BaseModel):
    sync_enabled: bool = False
    sync_interval_minutes: int = 60
    analyze_enabled: bool = False
    analyze_interval_minutes: int = 120
    qq_enabled: bool = False
    qq_interval_minutes: int = 60
    telegram_enabled: bool = False
    telegram_interval_minutes: int = 60
    tag_enabled: bool = False
    tag_interval_minutes: int = 360
    tag_max_contacts: int = 50  # per scheduled run; a fresh DB shouldn't burn hundreds of LLM calls at once
    todo_remind_enabled: bool = False
    todo_remind_interval_minutes: int = 1440
    todo_remind_min_urgency: int = 4
    # Digest reports run on fixed cadences (7d / 30d) — toggles only.
    report_weekly_enabled: bool = False
    report_monthly_enabled: bool = False
    # Legacy run stamps (pre scheduler_state table). Read once for migration; never written anymore.
    last_sync_at: str | None = None
    last_analyze_at: str | None = None
    last_qq_sync_at: str | None = None
    last_telegram_sync_at: str | None = None
    last_tag_at: str | None = None


class SemanticTrigger(BaseModel):
    """Embedding-based inbox trigger: new messages whose vector is closer than
    `threshold` to `query` land in the inbox, keyword-free."""
    query: str
    threshold: float = 0.6


class Config(BaseModel):
    daily_token_budget: int = 200_000
    budget_action: str = "pause_and_notify"
    api_token: str = ""  # when set, /api/* requires X-API-Token (or ?token= for SSE)
    vip_contacts: list[str] = []
    # The user's own display names / nicknames across platforms. Used by the
    # @消息 page (mentions of me) alongside the auto-inferred identities.
    self_names: list[str] = []
    keywords: list[str] = []  # case-insensitive (ASCII) substring matches; new keywords backfill on next scan
    semantic_triggers: list[SemanticTrigger] = []
    chat_filter: ChatFilterConfig = ChatFilterConfig()
    pre_filter: PreFilterConfig = PreFilterConfig()
    llm: LLMConfig = LLMConfig()
    storage: StorageConfig = StorageConfig()
    sync_scope: SyncScopeConfig = SyncScopeConfig()
    wechat: WeChatConfig = WeChatConfig()
    qq: QQConfig = QQConfig()
    telegram: TelegramConfig = TelegramConfig()
    scheduler: SchedulerConfig = SchedulerConfig()


_api_token_cache: str | None = None


def load_config() -> Config:
    if CONFIG_PATH.exists():
        return Config(**json.loads(CONFIG_PATH.read_text()))
    return Config()


def save_config(cfg: Config) -> None:
    global _api_token_cache
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(cfg.model_dump_json(indent=2))
    _api_token_cache = cfg.api_token
    # Config holds api_key / token / session_string equivalent to account credentials.
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass


def get_api_token() -> str:
    """Cached token for the request middleware — avoids a disk read per request."""
    global _api_token_cache
    if _api_token_cache is None:
        _api_token_cache = load_config().api_token
    return _api_token_cache


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


async def migrate_chat_filter_names() -> int:
    """Resolve legacy chat_filter.chats (display names) to refs using the
    chats we actually have. Names that match exactly one chat move over;
    ambiguous or unknown names stay as names (still honoured at query time).
    Returns how many were migrated."""
    import aiosqlite
    from app.core import database

    cfg = load_config()
    if not cfg.chat_filter.chats:
        return 0
    moved = 0
    remaining: list[str] = []
    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        for name in cfg.chat_filter.chats:
            rows = await db.execute_fetchall(
                "SELECT DISTINCT platform, chat_id FROM stats_chats WHERE chat_name = ?", (name,)
            )
            if not rows:
                rows = await db.execute_fetchall(
                    "SELECT DISTINCT platform, chat_id FROM messages WHERE chat_name = ? LIMIT 2", (name,)
                )
            if len(rows) == 1:
                ref = ChatRef(platform=rows[0][0], chat_id=rows[0][1])
                if ref.key() not in {r.key() for r in cfg.chat_filter.refs}:
                    cfg.chat_filter.refs.append(ref)
                moved += 1
            else:
                remaining.append(name)
    if moved:
        cfg.chat_filter.chats = remaining
        save_config(cfg)
    return moved
