"""Contact tagging. Reads a contact's recent messages and asks the LLM to tag
them, preferring the user's existing tag library and proposing new tags only
when nothing fits. LLM plumbing (provider routing, budget, usage, JSON parsing)
is reused from AnalyzerService — this module only owns the tagging prompt and
the suggested/confirmed persistence."""
import aiosqlite

from app.services.analyzer import AnalyzerService, _format_window

MAX_TAGS_PER_CONTACT = 5
MIN_MESSAGES = 3  # too few messages = no signal; skip rather than hallucinate

TAG_PROMPT = """你是联系人标签助手。根据下面与某位联系人的聊天记录，为这位联系人打标签，用于关系分类。

已有标签库（优先从中选最贴切的，可多选）：
{tag_library}

规则：
- 优先复用上面标签库里的标签
- 标签库覆盖不到时才新建标签，新标签要简短通用（2-6 字，便于复用），别用长句
- 每位联系人最多 {max_tags} 个标签，只打有把握的；证据不足就少打或返回空
- 依据聊天内容判断，不要臆测

对每个标签返回：
- name: 标签名
- confidence: 置信度 0-1
- reason: 一句话依据（15 字内）

返回 JSON 数组，无合适标签返回 []：
[{{"name": "同事", "confidence": 0.9, "reason": "常聊工作排期"}}]

仅返回 JSON，不要其他内容。

联系人：{contact_name}
聊天记录：
{messages}"""

INSIGHT_PROMPT = """以下是被打上「{tag_name}」标签的 {count} 位联系人最近的聊天记录。请总结这一群人的共同动态。

要求：
- 用简洁中文，给出这群人近期共同关心的话题、值得关注的动向、需要跟进的事项
- 如有明显的个体差异或重点联系人，简要点出
- 不要逐条复述消息，要提炼

格式：
## {tag_name} · 群体洞察
[2-3 句话概括这群人近期整体动态]

## 共同话题
- 话题1
- 话题2

## 值得跟进
- 事项（如有）

聊天记录（按联系人分组）：
{messages}"""


class TaggerService:
    def __init__(self):
        self.analyzer = AnalyzerService()

    @property
    def last_llm_error(self) -> str | None:
        return self.analyzer.last_llm_error

    async def suggest_tags(
        self, contact_name: str, messages: list[dict], active_tags: list[str]
    ) -> list[dict]:
        """Return normalized tag suggestions for one contact. Pure (no DB)."""
        if len(messages) < MIN_MESSAGES:
            return []
        text = _format_window(messages)
        library = "、".join(active_tags) if active_tags else "（暂无预设标签，可自由创建）"
        prompt = TAG_PROMPT.format(
            tag_library=library,
            max_tags=MAX_TAGS_PER_CONTACT,
            contact_name=contact_name or "未知",
            messages=text,
        )
        await self.analyzer._check_budget(len(text) // 3 + 300)
        raw = await self.analyzer._call_llm(prompt, purpose="tag")
        return self._normalize(self.analyzer._parse_llm_response(raw, 0), active_tags)

    async def summarize_group(self, tag_name: str, contacts: list[dict]) -> str:
        """Aggregate insight across contacts sharing a tag. `contacts` is a list
        of {name, messages:[...]}. Returns the LLM summary text (or "")."""
        if not contacts:
            return ""
        blocks = []
        for c in contacts:
            body = _format_window(c["messages"])
            blocks.append(f"【{c['name']}】\n{body}")
        text = "\n\n".join(blocks)
        prompt = INSIGHT_PROMPT.format(tag_name=tag_name, count=len(contacts), messages=text)
        await self.analyzer._check_budget(len(text) // 3 + 400)
        return await self.analyzer._call_llm(prompt, purpose="tag_insight") or ""

    @staticmethod
    def _normalize(items: list, active_tags: list[str]) -> list[dict]:
        active = {t.lower() for t in active_tags}
        out: list[dict] = []
        seen: set[str] = set()
        for it in items:
            if not isinstance(it, dict):
                continue
            name = str(it.get("name", "")).strip()
            key = name.lower()
            if not name or key in seen:
                continue
            seen.add(key)
            try:
                conf = float(it.get("confidence", 0.5))
            except (TypeError, ValueError):
                conf = 0.5
            out.append({
                "name": name,
                "confidence": round(max(0.0, min(1.0, conf)), 2),
                "reason": str(it.get("reason", ""))[:50],
                "is_new": key not in active,  # decided here, not trusted from the LLM
            })
            if len(out) >= MAX_TAGS_PER_CONTACT:
                break
        return out


async def get_or_create_tag(
    db: aiosqlite.Connection, name: str, *, source: str, status: str, color: str | None = None
) -> int:
    """Return the tag id for `name`, inserting it if absent. Existing tags keep
    their current source/status — we never downgrade a preset/active tag."""
    row = await (await db.execute("SELECT id FROM contact_tags WHERE name=?", (name,))).fetchone()
    if row:
        return row[0]
    cursor = await db.execute(
        "INSERT INTO contact_tags (name, color, source, status) VALUES (?,?,?,?)",
        (name, color, source, status),
    )
    return cursor.lastrowid


async def persist_suggestions(
    db: aiosqlite.Connection, platform: str, chat_id: str, suggestions: list[dict], batch_id: str
) -> int:
    """Upsert suggested links for a contact. New tag names become pending AI
    tags. A link that is already 'confirmed' is left untouched."""
    written = 0
    for s in suggestions:
        tag_id = await get_or_create_tag(
            db, s["name"],
            source="ai" if s["is_new"] else "preset",
            status="pending" if s["is_new"] else "active",
        )
        await db.execute(
            """INSERT INTO contact_tag_links
                   (platform, chat_id, tag_id, confidence, reason, source, status, batch_id)
               VALUES (?,?,?,?,?,'ai','suggested',?)
               ON CONFLICT(platform, chat_id, tag_id) DO UPDATE SET
                   confidence=excluded.confidence,
                   reason=excluded.reason,
                   batch_id=excluded.batch_id
               WHERE contact_tag_links.status='suggested'""",
            (platform, chat_id, tag_id, s["confidence"], s["reason"], batch_id),
        )
        written += 1
    return written
