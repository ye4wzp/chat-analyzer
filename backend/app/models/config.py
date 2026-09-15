from typing import Optional

from pydantic import BaseModel

from app.core.config import SemanticTrigger


class ConfigUpdate(BaseModel):
    filter_mode: Optional[str] = None
    add_chat: Optional[str] = None
    remove_chat: Optional[str] = None
    add_vip: Optional[str] = None
    remove_vip: Optional[str] = None
    budget: Optional[int] = None
    daily_token_budget: Optional[int] = None
    budget_action: Optional[str] = None
    vip_contacts: Optional[list[str]] = None
    self_names: Optional[list[str]] = None
    wechat_account: Optional[str] = None  # label for the currently logged-in WeChat (多号)
    llm_provider: Optional[str] = None
    llm_api_url: Optional[str] = None
    llm_model: Optional[str] = None
    llm_api_key: Optional[str] = None
    llm_embedding_model: Optional[str] = None
    qq_enabled: Optional[bool] = None
    qq_host: Optional[str] = None
    qq_port: Optional[int] = None
    qq_token: Optional[str] = None  # "********" means keep current
    telegram_enabled: Optional[bool] = None  # session_string is set by login flow only
    add_keyword: Optional[str] = None
    remove_keyword: Optional[str] = None
    keywords: Optional[list[str]] = None
    semantic_triggers: Optional[list[SemanticTrigger]] = None
    api_token: Optional[str] = None  # "********" means keep current
