import json
import os
from pathlib import Path
from pydantic import BaseModel

BASE_DIR = Path.home() / ".chat-analyzer"
DATA_DIR = BASE_DIR / "data"
CONFIG_PATH = BASE_DIR / "config.json"


class PreFilterConfig(BaseModel):
    skip_content: list[str] = [
        r"^[哈嘿呵哦嗯啊噢]{2,}$",
        r"^[👍👌🎉😂🤣😅🙏❤️💪😎🤝\s]+$",
        r"^(在吗|在不在|嗯|哦|好|好的|ok|OK|收到|嗯嗯|行|可以|对|是的)$",
        r"^\[.*\]$",
    ]
    skip_msg_types: list[str] = ["system", "sticker"]
    min_content_length: int = 2


class ChatFilterConfig(BaseModel):
    mode: str = "whitelist"  # whitelist: 只分析列出的群 / blacklist: 分析除列出的以外的群
    chats: list[str] = []    # 群聊名称列表


class LLMConfig(BaseModel):
    provider: str = "claude_cli"            # "claude_cli" | "openai_compatible"
    api_url: str = "http://localhost:1234/v1"
    model: str = ""
    api_key: str = "lm-studio"
    embedding_model: str = ""                # "" disables semantic features; otherwise OpenAI-compat /v1/embeddings model id


class QQConfig(BaseModel):
    """NapCat-QCE HTTP API integration."""
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
    keywords: list[str] = []  # case-insensitive (ASCII) substring matches; new keywords backfill on next scan
    semantic_triggers: list[SemanticTrigger] = []
    chat_filter: ChatFilterConfig = ChatFilterConfig()
    pre_filter: PreFilterConfig = PreFilterConfig()
    llm: LLMConfig = LLMConfig()
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
