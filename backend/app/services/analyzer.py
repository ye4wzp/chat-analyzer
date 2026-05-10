import asyncio
import json
import uuid
from datetime import datetime, timezone

import aiosqlite
import httpx

# Cached system local tz so we can pin naive timestamps to a consistent offset.
# Naive ISO strings come from wx-cli (no tz tag) so we treat them as local time;
# Telegram strings are already aware; QQ stores raw Unix epoch as digits.
_LOCAL_TZ = datetime.now().astimezone().tzinfo or timezone.utc

from app.core.config import load_config
from app.core.database import DB_PATH
from app.services.pre_filter import filter_messages

WINDOW_GAP_SECONDS = 1800  # 30 min
MAX_WINDOW_SIZE = 100
OVERLAP_SIZE = 5

CLASSIFY_PROMPT = """从以下对话中提取有价值的知识点，忽略闲聊、水词、表情、问候等无实质内容的消息。

提取标准（只提取以下类型）：
- 技术方案、工具推荐、操作步骤
- 问题解决方案、踩坑经验
- 重要决策、结论、共识
- 资源链接、参考资料
- 待办事项、行动计划

对每个知识点返回：
- title: 简短标题（10字以内）
- content: 知识点内容（保留原文关键信息）
- tags: 标签数组（如 ["技术", "工具"]）
- source_indices: 来源消息的 index 数组
- urgency: 重要程度 1-5

返回 JSON 数组，如无有价值内容则返回空数组 []：
[{{"title": "...", "content": "...", "tags": [...], "source_indices": [0,1], "urgency": 3}}]

仅返回 JSON，不要其他内容。

对话片段：
{messages}"""

EXTEND_PROMPT = """你是一个知识扩展助手。基于以下从聊天中提取的知识点，补充背景知识、相关资源和延伸内容。

知识点：
{knowledge}

请补充：
1. 背景解释（如果是技术术语或方案，解释其原理）
2. 相关资源（官方文档、最佳实践等）
3. 注意事项或常见误区
4. 与其他相关概念的联系

用简洁的中文输出，Markdown 格式。"""

SUMMARY_PROMPT = """总结以下对话片段的要点。

要求：
- 用简洁的中文，列出关键话题、待办事项和重要决策
- 如果有待办事项，明确标注负责人和截止时间（如有）
- 最后给整体重要程度打分（1-5）

格式：
## 总结
[2-3句话概括对话主题]

## 关键要点
- 要点1
- 要点2

## 待办事项（如有）
- [ ] 待办1

## 重要程度：X/5

对话片段：
{messages}"""


def split_into_windows(messages: list[dict]) -> list[list[dict]]:
    """Split messages into conversation windows by time gap."""
    if not messages:
        return []

    windows = []
    current = [messages[0]]

    for msg in messages[1:]:
        prev_ts = _parse_ts(current[-1].get("timestamp", ""))
        cur_ts = _parse_ts(msg.get("timestamp", ""))

        if prev_ts and cur_ts and (cur_ts - prev_ts).total_seconds() > WINDOW_GAP_SECONDS:
            windows.append(current)
            current = [msg]
        else:
            current.append(msg)

    if current:
        windows.append(current)

    # Split oversized windows with overlap
    result = []
    for w in windows:
        if len(w) <= MAX_WINDOW_SIZE:
            result.append(w)
        else:
            for i in range(0, len(w), MAX_WINDOW_SIZE - OVERLAP_SIZE):
                sub = w[i : i + MAX_WINDOW_SIZE]
                if sub:
                    result.append(sub)

    return result


def _parse_ts(ts_str: str) -> datetime | None:
    """Always return tz-aware datetime so cross-platform deltas don't crash.

    Three formats coexist in the messages table:
      - ISO with tz   (Telegram: '...+08:00')   → aware as-is
      - ISO without tz (WeChat: '2026-04-10T17:07:07') → assume local tz
      - Unix epoch as digits (QQ: '1769758739') → UTC
    """
    if ts_str is None:
        return None
    s = str(ts_str).strip()
    if not s:
        return None
    if s.isdigit():
        try:
            ts = int(s)
            if ts > 10**12:  # millisecond epoch
                ts //= 1000
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_LOCAL_TZ)
    return dt


def _format_window(window: list[dict]) -> str:
    """Format a conversation window for the LLM prompt."""
    lines = []
    for i, msg in enumerate(window):
        sender = msg.get("sender_name") or msg.get("sender_id") or "Unknown"
        content = msg.get("content", "")
        ts = msg.get("timestamp", "")
        lines.append(f"[{i}] [{ts}] {sender}: {content}")
    return "\n".join(lines)


class TokenBudgetExceeded(Exception):
    pass


class AnalyzerService:
    def __init__(self):
        self.cfg = load_config()
        self.tokens_used_today = 0
        self._today = datetime.now().date()

    def _check_budget(self, estimated_tokens: int) -> None:
        today = datetime.now().date()
        if today != self._today:
            self.tokens_used_today = 0
            self._today = today

        if self.tokens_used_today + estimated_tokens > self.cfg.daily_token_budget:
            raise TokenBudgetExceeded(
                f"Daily token budget exceeded: {self.tokens_used_today}/{self.cfg.daily_token_budget}"
            )
        self.tokens_used_today += estimated_tokens

    async def _call_llm(self, prompt: str) -> str | None:
        """Call LLM via configured provider. Returns raw text response."""
        llm = self.cfg.llm

        if llm.provider == "openai_compatible":
            return await self._call_openai(prompt, llm)

        # Default: claude CLI
        return await self._call_claude_cli(prompt)

    async def _call_claude_cli(self, prompt: str) -> str | None:
        proc = await asyncio.create_subprocess_exec(
            "claude", "-p", "--output-format", "text", prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return None
        return stdout.decode().strip()

    async def _call_openai(self, prompt: str, llm) -> str | None:
        try:
            api_url = llm.api_url.replace("localhost", "127.0.0.1")
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    f"{api_url}/chat/completions",
                    headers={"Authorization": f"Bearer {llm.api_key}"},
                    json={
                        "model": llm.model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.3,
                    },
                )
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception:
            return None

    def _parse_llm_response(self, raw: str | None, window_size: int) -> list[dict]:
        if not raw:
            return []
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
        raw = raw.strip()
        start = raw.find("[")
        if start > 0:
            raw = raw[start:]
        end = raw.rfind("]")
        if end >= 0 and end < len(raw) - 1:
            raw = raw[: end + 1]
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return []

    async def extract_knowledge(self, window: list[dict]) -> list[dict]:
        text = _format_window(window)
        prompt = CLASSIFY_PROMPT.format(messages=text)
        self._check_budget(len(text) // 3 + 500)
        raw = await self._call_llm(prompt)
        items = self._parse_llm_response(raw, len(window))
        # attach source message ids
        for item in items:
            indices = item.pop("source_indices", [])
            item["source_message_ids"] = json.dumps([
                window[i]["id"] for i in indices
                if isinstance(i, int) and 0 <= i < len(window)
            ])
        return items

    async def extend_knowledge(self, item: dict) -> str:
        prompt = EXTEND_PROMPT.format(knowledge=f"**{item['title']}**\n{item['content']}")
        self._check_budget(len(item['content']) // 3 + 300)
        return await self._call_llm(prompt) or ""

    async def analyze_messages(self, messages: list[dict], on_progress=None) -> tuple[list[dict], str]:
        """Extract knowledge items from messages. Returns (knowledge_items, summary)."""
        passed, _ = filter_messages(messages)
        batch_id = uuid.uuid4().hex[:8]
        all_items = []
        windows = split_into_windows(passed)
        total = len(windows)

        for i, window in enumerate(windows):
            if on_progress and total > 0:
                pct = 30 + int(60 * i / total)
                on_progress(pct, f"分析窗口 {i+1}/{total}...")
            try:
                items = await self.extract_knowledge(window)
                for item in items:
                    item["batch_id"] = batch_id
                    item["source_chat"] = window[0].get("chat_name", "") if window else ""
                all_items.extend(items)
            except TokenBudgetExceeded:
                break

        summary = await self._summarize(messages)
        return all_items, summary

    async def _summarize(self, messages: list[dict]) -> str:
        """Generate an overall summary of the messages."""
        if not messages:
            return ""
        text = _format_window(messages[:80])  # limit to avoid token overflow
        prompt = SUMMARY_PROMPT.format(messages=text)
        self._check_budget(len(text) // 3 + 300)
        raw = await self._call_llm(prompt)
        return raw or ""
