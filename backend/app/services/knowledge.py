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

    if not isinstance(values, list):
        return []
    result = []
    for value in values:
        try:
            result.append(int(value))
        except (TypeError, ValueError):
            continue
    return result


def category_for_knowledge(item: dict) -> str:
    """LLM-emitted category wins; the tag heuristic is a fallback for items
    extracted before the prompt carried a category field."""
    cat = str(item.get("category") or "").strip().lower()
    if cat == "todo":
        return "todo"
    if cat in ("knowledge", "important"):
        return "important"
    tags = [str(t).lower() for t in item.get("tags", [])]
    todo_markers = ("todo", "待办", "行动", "任务", "action")
    if any(marker in tag for marker in todo_markers for tag in tags):
        return "todo"
    return "important"


def _merge_entities(existing_raw: str | None, knowledge_id: int, tags: list) -> str:
    """Accumulate provenance: a message cited by several knowledge items keeps
    every knowledge_id and the union of tags."""
    try:
        data = json.loads(existing_raw or "{}")
        if not isinstance(data, dict):
            data = {}
    except json.JSONDecodeError:
        data = {}
    ids = data.get("knowledge_ids") or []
    legacy = data.get("knowledge_id")
    if legacy and legacy not in ids:
        ids.insert(0, legacy)
    if knowledge_id not in ids:
        ids.append(knowledge_id)
    merged_tags = list(dict.fromkeys([*(data.get("tags") or []), *tags]))
    return json.dumps(
        {"knowledge_id": knowledge_id, "knowledge_ids": ids, "tags": merged_tags},
        ensure_ascii=False,
    )


async def _is_duplicate(db: aiosqlite.Connection, item: dict) -> bool:
    # Similar titles can describe different incidents or a later correction.
    rows = await db.execute_fetchall(
        "SELECT source_message_ids,source_platform,source_chat_id,title FROM knowledge_items WHERE content=?",
        (item.get("content", ""),))
    ids = set(parse_source_message_ids(item.get("source_message_ids")))
    for raw, platform, cid, title in rows:
        existing = set(parse_source_message_ids(raw))
        if ids and ids == existing and title == item.get("title", ""):
            return True
        if not ids and not existing and item.get("source_platform") and item.get("source_chat_id"):
            if (platform, cid, title) == (item["source_platform"], item["source_chat_id"], item.get("title", "")):
                return True
    return False


async def save_knowledge_items(db: aiosqlite.Connection, items: list[dict]) -> int:
    """Insert knowledge items and derived per-message analysis_results.

    Exact duplicates (same source set, title and content)
    are skipped — overlapping analysis windows produce them. A message already
    categorized 'todo' is never downgraded by a later non-todo item."""
    saved = 0
    for item in items:
        if await _is_duplicate(db, item):
            continue
        source_ids = parse_source_message_ids(item.get("source_message_ids", "[]"))
        # Stable provenance: (platform, chat_id) of the first source message.
        # The display name (source_chat) is kept for rendering only.
        src_platform, src_chat_id = item.get("source_platform"), item.get("source_chat_id")
        if (not src_platform or not src_chat_id) and source_ids:
            hit = await db.execute_fetchall(
                "SELECT platform, chat_id FROM messages WHERE id = ? LIMIT 1", (source_ids[0],)
            )
            if hit:
                src_platform, src_chat_id = hit[0][0], hit[0][1]
        cursor = await db.execute(
            """INSERT INTO knowledge_items
               (title, content, source_chat, source_platform, source_chat_id, source_message_ids, tags, batch_id)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                item.get("title", ""),
                item.get("content", ""),
                item.get("source_chat", ""),
                src_platform,
                src_chat_id,
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
        if not source_ids:
            continue

        placeholders = ",".join("?" * len(source_ids))
        existing_rows = await db.execute_fetchall(
            f"SELECT message_id, category, key_entities FROM analysis_results WHERE message_id IN ({placeholders})",
            source_ids,
        )
        existing = {row[0]: (row[1], row[2]) for row in existing_rows}

        for message_id in source_ids:
            prev_category, prev_entities = existing.get(message_id, (None, None))
            final_category = "todo" if prev_category == "todo" else category
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
                    final_category,
                    urgency,
                    summary,
                    summary if final_category == "todo" else None,
                    _merge_entities(prev_entities, knowledge_id, item.get("tags", [])),
                    item.get("batch_id", ""),
                ),
            )
    return saved
