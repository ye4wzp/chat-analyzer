import httpx
from fastapi import APIRouter, HTTPException

from app.core.config import load_config, save_config
from app.models.config import ConfigUpdate

router = APIRouter()


def _redact(data: dict) -> dict:
    if data.get("llm", {}).get("api_key"):
        data["llm"]["api_key"] = "********"
    if data.get("qq", {}).get("token"):
        data["qq"]["token"] = "********"
    if data.get("telegram", {}).get("session_string"):
        data["telegram"]["session_string"] = "********"
    return data


@router.get("/api/config")
async def get_config():
    return _redact(load_config().model_dump())


@router.put("/api/config")
async def update_config(body: ConfigUpdate):
    cfg = load_config()

    if body.filter_mode:
        cfg.chat_filter.mode = body.filter_mode
    if body.add_chat and body.add_chat not in cfg.chat_filter.chats:
        cfg.chat_filter.chats.append(body.add_chat)
    if body.remove_chat and body.remove_chat in cfg.chat_filter.chats:
        cfg.chat_filter.chats.remove(body.remove_chat)
    if body.add_vip and body.add_vip not in cfg.vip_contacts:
        cfg.vip_contacts.append(body.add_vip)
    if body.remove_vip and body.remove_vip in cfg.vip_contacts:
        cfg.vip_contacts.remove(body.remove_vip)
    if body.budget is not None:
        cfg.daily_token_budget = body.budget
    if body.daily_token_budget is not None:
        cfg.daily_token_budget = body.daily_token_budget
    if body.budget_action is not None:
        cfg.budget_action = body.budget_action
    if body.vip_contacts is not None:
        cfg.vip_contacts = body.vip_contacts
    if body.llm_provider:
        cfg.llm.provider = body.llm_provider
    if body.llm_api_url is not None:
        cfg.llm.api_url = body.llm_api_url
    if body.llm_model is not None:
        cfg.llm.model = body.llm_model
    if body.llm_api_key is not None and body.llm_api_key != "********":
        cfg.llm.api_key = body.llm_api_key
    if body.qq_enabled is not None:
        cfg.qq.enabled = body.qq_enabled
    if body.qq_host is not None:
        cfg.qq.host = body.qq_host
    if body.qq_port is not None:
        cfg.qq.port = body.qq_port
    if body.qq_token is not None and body.qq_token != "********":
        cfg.qq.token = body.qq_token
    if body.telegram_enabled is not None:
        cfg.telegram.enabled = body.telegram_enabled

    save_config(cfg)
    return _redact(cfg.model_dump())


@router.get("/api/llm/models")
async def get_llm_models():
    cfg = load_config()
    if cfg.llm.provider != "openai_compatible":
        return {"provider": "claude_cli", "models": []}
    url = cfg.llm.api_url.replace("localhost", "127.0.0.1")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{url}/models",
                headers={"Authorization": f"Bearer {cfg.llm.api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()
            models = [m["id"] for m in data.get("data", [])]
            return {"provider": "openai_compatible", "models": models}
    except Exception as e:
        raise HTTPException(502, f"无法连接到 LLM 服务: {e}")


@router.get("/api/llm/test")
async def test_llm_connection():
    cfg = load_config()
    if cfg.llm.provider != "openai_compatible":
        return {"status": "ok", "provider": "claude_cli"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{cfg.llm.api_url.replace('localhost', '127.0.0.1')}/models",
                headers={"Authorization": f"Bearer {cfg.llm.api_key}"},
            )
            resp.raise_for_status()
            return {"status": "ok", "provider": "openai_compatible"}
    except Exception as e:
        raise HTTPException(502, f"连接失败: {e}")
