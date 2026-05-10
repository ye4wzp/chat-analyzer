import json

import aiosqlite


def parse_source_message_ids(raw: object) -> list[int]:
    if isinstance(raw, list):
        values = raw
    elif isinstance(raw, str):
        try:
            values = json.loads(raw)
        except json.JSONDecodeError:
            return []
    else:
        return []

    result = []
    for value in values:
        try:
            result.append(int(value))
        except (TypeError, ValueError):
            continue
    return result


def category_for_knowledge(item: dict) -> str:
    tags = [str(t).lower() for t in item.get("tags", [])]
    text = f"{item.get('title', '')} {item.get('content', '')}".lower()
    todo_markers = ("todo", "待办", "行动", "任务", "action")
    if any(marker in tag for marker in todo_markers for tag in tags) or any(marker in text for marker in todo_markers):
        return "todo"
    return "important"


async def save_knowledge_items(db: aiosqlite.Connection, items: list[dict]) -> int:
    saved = 0
    for item in items:
        cursor = await db.execute(
            """INSERT INTO knowledge_items (title, content, source_chat, source_message_ids, tags, batch_id)
               VALUES (?,?,?,?,?,?)""",
            (
                item.get("title", ""),
                item.get("content", ""),
                item.get("source_chat", ""),
                item.get("source_message_ids", "[]"),
                json.dumps(item.get("tags", []), ensure_ascii=False),
                item.get("batch_id", ""),
            ),
        )
        saved += 1
        knowledge_id = cursor.lastrowid
        category = category_for_knowledge(item)
        urgency = int(item.get("urgency") or 3)
        summary = item.get("content", "")
        source_ids = parse_source_message_ids(item.get("source_message_ids", "[]"))
        for message_id in source_ids:
            await db.execute(
                """INSERT INTO analysis_results
                   (message_id, category, urgency, summary, action_items, key_entities, batch_id)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(message_id) DO UPDATE SET
                       category=excluded.category,
                       urgency=MAX(analysis_results.urgency, excluded.urgency),
                       summary=excluded.summary,
                       action_items=excluded.action_items,
                       key_entities=excluded.key_entities,
                       batch_id=excluded.batch_id,
                       analyzed_at=CURRENT_TIMESTAMP""",
                (
                    message_id,
                    category,
                    urgency,
                    summary,
                    summary if category == "todo" else None,
                    json.dumps({
                        "knowledge_id": knowledge_id,
                        "tags": item.get("tags", []),
                    }, ensure_ascii=False),
                    item.get("batch_id", ""),
                ),
            )
    return saved
