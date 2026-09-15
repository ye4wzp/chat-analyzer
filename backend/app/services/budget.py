"""Daily token budget: usage recording, pre-call checks, in-flight reservation.

Every LLM and embedding call reserves its estimate before the request and
records real usage after. Reservations close the check-then-act race between
concurrent tasks (analyze + tag batch both passing the check before either's
usage row lands).
"""
import logging
from datetime import datetime, timezone

import aiosqlite

from app.core import database
from app.core.config import load_config

logger = logging.getLogger("app.budget")

_inflight = 0


class TokenBudgetExceeded(Exception):
    pass


def _local_day_start_utc() -> str:
    """UTC instant of local midnight, in llm_usage.timestamp's CURRENT_TIMESTAMP
    format. Used as a sargable lower bound so idx_usage_ts is actually hit —
    wrapping the column in DATE(timestamp,'localtime') defeated the index."""
    local_now = datetime.now().astimezone()
    local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_midnight.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


async def tokens_used_today() -> int:
    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        rows = await db.execute_fetchall(
            """SELECT COALESCE(SUM(prompt_tokens + completion_tokens), 0)
               FROM llm_usage WHERE timestamp >= ?""",
            (_local_day_start_utc(),),
        )
    return int(rows[0][0]) if rows else 0


async def check(estimated_tokens: int) -> None:
    """Raise TokenBudgetExceeded (or warn, per config) if today's usage plus
    in-flight reservations plus this call would exceed the budget."""
    cfg = load_config()
    if cfg.daily_token_budget <= 0:
        return
    used = await tokens_used_today()
    if used + _inflight + estimated_tokens > cfg.daily_token_budget:
        if cfg.budget_action == "warn":
            logger.warning(
                "daily token budget would be exceeded: %d used + %d in-flight + %d > %d",
                used, _inflight, estimated_tokens, cfg.daily_token_budget,
            )
            return
        raise TokenBudgetExceeded(
            f"Daily token budget exceeded: {used}/{cfg.daily_token_budget}"
        )


async def reserve(estimated_tokens: int) -> None:
    global _inflight
    await check(estimated_tokens)
    _inflight += estimated_tokens


def release(estimated_tokens: int) -> None:
    global _inflight
    _inflight = max(0, _inflight - estimated_tokens)


async def record(
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    purpose: str,
    task_id: str | None = None,
) -> None:
    """Best-effort write to llm_usage. Failures are swallowed so a bad write
    never aborts the calling task."""
    try:
        async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
            await db.execute("PRAGMA busy_timeout=30000")
            await db.execute(
                "INSERT INTO llm_usage (provider, model, prompt_tokens, completion_tokens, purpose, task_id)"
                " VALUES (?,?,?,?,?,?)",
                (provider, model, int(prompt_tokens), int(completion_tokens), purpose, task_id),
            )
            await db.commit()
    except Exception as e:
        logger.warning("usage record failed: %s", e)
