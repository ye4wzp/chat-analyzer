import asyncio
import json
import logging
import sys
import uuid
from datetime import datetime, timezone

import aiosqlite
import httpx

# Route analyzer logs through stderr at INFO so they show up under uvicorn's
# default logging config. Helpful for debugging "why did I get 0 items?".
logger = logging.getLogger("app.analyzer")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("[analyzer] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

# Cached system local tz so we can pin naive timestamps to a consistent offset.
# Naive ISO strings come from wx-cli (no tz tag) so we treat them as local time;
# Telegram strings are already aware; QQ stores raw Unix epoch as digits.
_LOCAL_TZ = datetime.now().astimezone().tzinfo or timezone.utc

from app.core.config import load_config
from app.core.database import DB_PATH
from app.services.pre_filter import filter_messages

WINDOW_GAP_SECONDS = 1800  # 30 min
MAX_WINDOW_SIZE = 40
OVERLAP_SIZE = 5
MAX_MSG_CONTENT_CHARS = 300  # cap per-message content so one long forward doesn't blow the prompt


async def _record_usage(
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    purpose: str,
    task_id: str | None = None,
) -> None:
    """Best-effort write to llm_usage table. Failures are swallowed so a bad
    write never aborts an analyze run."""
    try:
        async with aiosqlite.connect(str(DB_PATH), timeout=60) as db:
            await db.execute("PRAGMA busy_timeout=30000")
            await db.execute(
                "INSERT INTO llm_usage (provider, model, prompt_tokens, completion_tokens, purpose, task_id)"
                " VALUES (?,?,?,?,?,?)",
                (provider, model, int(prompt_tokens), int(completion_tokens), purpose, task_id),
            )
            await db.commit()
    except Exception as e:
        logger.warning("usage record failed: %s", e)

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
        if len(content) > MAX_MSG_CONTENT_CHARS:
            content = content[:MAX_MSG_CONTENT_CHARS] + "...(截断)"
        ts = msg.get("timestamp", "")
        lines.append(f"[{i}] [{ts}] {sender}: {content}")
    return "\n".join(lines)


class TokenBudgetExceeded(Exception):
    pass


class AnalyzerService:
    def __init__(self):
        self.cfg = load_config()
        self.last_llm_error: str | None = None
        self.llm_call_count = 0
        self.llm_fail_count = 0

    async def _tokens_used_today(self) -> int:
        async with aiosqlite.connect(str(DB_PATH), timeout=60) as db:
            rows = await db.execute_fetchall(
                """SELECT COALESCE(SUM(prompt_tokens + completion_tokens), 0) AS total
                   FROM llm_usage
                   WHERE DATE(timestamp, 'localtime') = DATE('now', 'localtime')"""
            )
        return int(rows[0][0]) if rows else 0

    async def _check_budget(self, estimated_tokens: int) -> None:
        if self.cfg.daily_token_budget <= 0:
            return

        used_today = await self._tokens_used_today()
        if used_today + estimated_tokens > self.cfg.daily_token_budget:
            if self.cfg.budget_action == "warn":
                logger.warning(
                    "daily token budget would be exceeded: %d + %d > %d",
                    used_today, estimated_tokens, self.cfg.daily_token_budget,
                )
                return
            raise TokenBudgetExceeded(
                f"Daily token budget exceeded: {used_today}/{self.cfg.daily_token_budget}"
            )

    async def _call_llm(self, prompt: str, purpose: str = "") -> str | None:
        """Call LLM via configured provider. Returns raw text response."""
        llm = self.cfg.llm

        if llm.provider == "openai_compatible":
            return await self._call_openai(prompt, llm, purpose)

        if llm.provider == "codex_cli":
            return await self._call_codex_cli(prompt, purpose)

        # Default: claude CLI
        return await self._call_claude_cli(prompt, purpose)

    async def _call_claude_cli(self, prompt: str, purpose: str = "") -> str | None:
        self.llm_call_count += 1
        proc = await asyncio.create_subprocess_exec(
            "claude", "-p", "--output-format", "text", prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            self.llm_fail_count += 1
            self.last_llm_error = "claude CLI exit nonzero"
            return None
        out = stdout.decode().strip()
        # Claude CLI doesn't surface usage; estimate from char counts (rough
        # 1 token ≈ 3 chars for Chinese) so the budget bar at least moves.
        await _record_usage(
            "claude_cli", "claude", len(prompt) // 3, len(out) // 3, purpose,
        )
        return out

    async def _call_codex_cli(self, prompt: str, purpose: str = "") -> str | None:
        """Call OpenAI Codex CLI in non-interactive mode.

        We use --output-last-message to capture only the final assistant reply
        (codex prints session metadata + a token-usage footer to stdout that
        would otherwise need parsing). --skip-git-repo-check avoids requiring
        a git repo at the cwd; -s read-only locks down filesystem access.
        """
        import os
        import tempfile

        self.llm_call_count += 1
        out_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, prefix="codex_out_"
        )
        out_path = out_file.name
        out_file.close()

        # If user set a model, pass it through; otherwise rely on codex defaults.
        cmd = [
            "codex", "exec",
            "--skip-git-repo-check",
            "-s", "read-only",
            "--color", "never",
            "--output-last-message", out_path,
        ]
        if self.cfg.llm.model:
            cmd.extend(["-m", self.cfg.llm.model])
        cmd.append(prompt)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
            except asyncio.TimeoutError:
                proc.kill()
                self.llm_fail_count += 1
                self.last_llm_error = "codex CLI timeout after 600s"
                return None

            if proc.returncode != 0:
                self.llm_fail_count += 1
                err = stderr.decode("utf-8", errors="replace")[-300:]
                self.last_llm_error = f"codex CLI exit {proc.returncode}: {err}"
                logger.warning("codex CLI failed: %s", err)
                return None

            try:
                with open(out_path, "r", encoding="utf-8") as f:
                    out = f.read().strip()
            except OSError as e:
                self.llm_fail_count += 1
                self.last_llm_error = f"读取 codex 输出失败: {e}"
                return None

            if not out:
                self.llm_fail_count += 1
                self.last_llm_error = "codex 返回空内容"
                return None

            # Codex doesn't expose token counts via this interface. Estimate
            # for budget tracking parity with claude_cli.
            await _record_usage(
                "codex_cli", self.cfg.llm.model or "codex-default",
                len(prompt) // 3, len(out) // 3, purpose,
            )
            return out
        finally:
            try:
                os.unlink(out_path)
            except OSError:
                pass

    async def _call_openai(self, prompt: str, llm, purpose: str = "") -> str | None:
        self.llm_call_count += 1
        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                api_url = llm.api_url.replace("localhost", "127.0.0.1")
                # 600s — local CPU inference on long prompts can exceed 2 min easily.
                async with httpx.AsyncClient(timeout=600) as client:
                    resp = await client.post(
                        f"{api_url}/chat/completions",
                        headers={"Authorization": f"Bearer {llm.api_key}"},
                        json={
                            "model": llm.model,
                            "messages": [{"role": "user", "content": prompt}],
                            "temperature": 0.3,
                        },
                    )
                    if resp.status_code != 200:
                        self.llm_fail_count += 1
                        body = resp.text[:300]
                        self.last_llm_error = f"HTTP {resp.status_code}: {body}"
                        logger.warning(
                            "LLM %s returned %d: %s (prompt len=%d)",
                            llm.model, resp.status_code, body, len(prompt),
                        )
                        return None
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"].strip()
                    usage = data.get("usage") or {}
                    await _record_usage(
                        llm.provider,
                        llm.model,
                        int(usage.get("prompt_tokens") or len(prompt) // 3),
                        int(usage.get("completion_tokens") or len(content) // 3),
                        purpose,
                    )
                    return content
            except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.WriteTimeout) as e:
                if attempt < max_retries:
                    wait = 5 * (attempt + 1)
                    logger.warning("LLM timeout (attempt %d/%d), retrying in %ds: %s",
                                   attempt + 1, max_retries + 1, wait, e)
                    await asyncio.sleep(wait)
                    continue
                self.llm_fail_count += 1
                self.last_llm_error = f"ReadTimeout after {max_retries + 1} attempts"
                logger.warning("LLM call timed out after %d attempts", max_retries + 1)
                return None
            except Exception as e:
                self.llm_fail_count += 1
                err_msg = str(e) or type(e).__name__
                self.last_llm_error = err_msg
                logger.warning("LLM call failed: %s", err_msg)
                return None

    def _parse_llm_response(self, raw: str | None, window_size: int) -> list[dict]:
        if not raw:
            logger.info("LLM returned empty/None response")
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
        except json.JSONDecodeError as e:
            logger.warning("LLM JSON parse failed: %s; raw[:200]=%s", e, raw[:200])
            return []

    async def extract_knowledge(self, window: list[dict]) -> list[dict]:
        text = _format_window(window)
        prompt = CLASSIFY_PROMPT.format(messages=text)
        await self._check_budget(len(text) // 3 + 500)
        raw = await self._call_llm(prompt, purpose="extract")
        items = self._parse_llm_response(raw, len(window))
        # attach source message ids
        for item in items:
            indices = item.pop("source_indices", [])
            item["source_message_ids"] = json.dumps([
                window[i]["id"] for i in indices
                if isinstance(i, int) and 0 <= i < len(window)
            ], separators=(",", ":"))
        return items

    async def extend_knowledge(self, item: dict) -> str:
        prompt = EXTEND_PROMPT.format(knowledge=f"**{item['title']}**\n{item['content']}")
        await self._check_budget(len(item['content']) // 3 + 300)
        return await self._call_llm(prompt, purpose="extend") or ""

    async def analyze_messages(self, messages: list[dict], on_progress=None) -> tuple[list[dict], str]:
        """Extract knowledge items from messages. Returns (knowledge_items, summary).

        Sets self.last_llm_error to the most recent LLM failure message (if any),
        so callers can surface "LLM 报错" instead of a misleading "0 知识点"."""
        passed, noise = filter_messages(messages)
        batch_id = uuid.uuid4().hex[:8]
        all_items = []
        windows = split_into_windows(passed)
        total = len(windows)
        logger.info("in=%d passed=%d noise=%d windows=%d", len(messages), len(passed), len(noise), total)
        self.last_llm_error = None

        for i, window in enumerate(windows):
            if on_progress and total > 0:
                pct = 30 + int(60 * i / total)
                on_progress(pct, f"分析窗口 {i+1}/{total}...")
            try:
                items = await self.extract_knowledge(window)
                logger.info("window %d/%d size=%d -> %d items", i+1, total, len(window), len(items))
                for item in items:
                    item["batch_id"] = batch_id
                    item["source_chat"] = window[0].get("chat_name", "") if window else ""
                all_items.extend(items)
            except TokenBudgetExceeded:
                logger.warning("token budget exceeded at window %d/%d", i+1, total)
                self.last_llm_error = "Daily token budget exceeded"
                break

        summary = await self._summarize(messages)
        logger.info("done: %d items total, %d/%d llm fails", len(all_items), self.llm_fail_count, self.llm_call_count)
        return all_items, summary

    async def _summarize(self, messages: list[dict]) -> str:
        """Generate an overall summary of the messages."""
        if not messages:
            return ""
        # Cap at 30 most-recent so the summary prompt fits even on small (4k) contexts.
        text = _format_window(messages[:30])
        prompt = SUMMARY_PROMPT.format(messages=text)
        await self._check_budget(len(text) // 3 + 300)
        raw = await self._call_llm(prompt, purpose="summarize")
        return raw or ""
