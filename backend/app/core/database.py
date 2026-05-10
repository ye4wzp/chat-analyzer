import aiosqlite
from pathlib import Path

from app.core.config import DATA_DIR

DB_PATH = DATA_DIR / "chat.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    chat_name TEXT,
    chat_type TEXT,
    sender_id TEXT,
    sender_name TEXT,
    content TEXT,
    msg_type TEXT,
    timestamp DATETIME NOT NULL,
    source_id TEXT,
    raw_data TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_msg_platform_ts ON messages(platform, timestamp);
CREATE INDEX IF NOT EXISTS idx_msg_chat_ts ON messages(chat_id, timestamp);

CREATE TABLE IF NOT EXISTS contact_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_name TEXT NOT NULL,
    platform TEXT NOT NULL,
    platform_id TEXT NOT NULL,
    platform_name TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(platform, platform_id)
);

CREATE INDEX IF NOT EXISTS idx_alias_canonical ON contact_aliases(canonical_name);

CREATE TABLE IF NOT EXISTS analysis_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER REFERENCES messages(id),
    category TEXT,
    urgency INTEGER DEFAULT 3,
    summary TEXT,
    action_items TEXT,
    key_entities TEXT,
    batch_id TEXT,
    analyzed_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_analysis_category ON analysis_results(category);
CREATE INDEX IF NOT EXISTS idx_analysis_urgency ON analysis_results(urgency);

CREATE TABLE IF NOT EXISTS sync_state (
    platform TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    last_msg_id TEXT,
    last_timestamp DATETIME,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (platform, chat_id)
);

CREATE TABLE IF NOT EXISTS knowledge_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    source_chat TEXT,
    source_message_ids TEXT,
    tags TEXT,
    extended_content TEXT,
    batch_id TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Per-call LLM usage log. Powers Dashboard's daily-budget card and lets us see
-- which tasks/models burn the most. Keep raw counts; aggregate at query time.
CREATE TABLE IF NOT EXISTS llm_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    provider TEXT,
    model TEXT,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    task_id TEXT,
    purpose TEXT  -- "extract" | "summarize" | "extend" | "embed"
);

CREATE INDEX IF NOT EXISTS idx_usage_ts ON llm_usage(timestamp);

-- LLM-generated chat summary cache. Per (platform, chat_id) — keyed not on
-- chat_name because names can change. Regenerate on demand from the API; the
-- generated_at field lets the UI show "stale > 7d" warnings.
CREATE TABLE IF NOT EXISTS chat_profiles (
    platform TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    summary TEXT,
    summary_generated_at DATETIME,
    PRIMARY KEY (platform, chat_id)
);

-- Keyword triggers — every (keyword, message) match is one row. UNIQUE
-- prevents the rescan loop from creating duplicates. read=0 means unread; the
-- bell-icon badge in Layout polls COUNT(read=0).
CREATE TABLE IF NOT EXISTS keyword_triggers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    matched_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    read INTEGER DEFAULT 0,
    UNIQUE(keyword, message_id)
);

CREATE INDEX IF NOT EXISTS idx_trigger_unread ON keyword_triggers(read, matched_at);

-- Per-keyword high-water-mark. Scans only walk messages with id > last so
-- enabling a new keyword does an initial backfill but subsequent passes are O(new).
CREATE TABLE IF NOT EXISTS keyword_scan_state (
    keyword TEXT PRIMARY KEY,
    last_scanned_message_id INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_knowledge_batch ON knowledge_items(batch_id);
"""


async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(str(DB_PATH), timeout=30)
    db.row_factory = aiosqlite.Row
    return db


async def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(DB_PATH), timeout=30) as db:
        # WAL lets concurrent syncs (wechat + qq + telegram) write without
        # tripping over each other's locks. Set once; persists in the file.
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        await db.executescript(SCHEMA_SQL)
        await _migrate_db(db)
        await db.commit()


async def _migrate_db(db: aiosqlite.Connection) -> None:
    columns = await db.execute_fetchall("PRAGMA table_info(messages)")
    message_columns = {row[1] for row in columns}
    if "source_id" not in message_columns:
        await db.execute("ALTER TABLE messages ADD COLUMN source_id TEXT")

    await db.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_msg_unique_source
           ON messages(platform, chat_id, source_id)
           WHERE source_id IS NOT NULL"""
    )

    duplicate_analysis = await db.execute_fetchall(
        """SELECT message_id, MIN(id) keep_id
           FROM analysis_results
           WHERE message_id IS NOT NULL
           GROUP BY message_id
           HAVING COUNT(*) > 1"""
    )
    for message_id, keep_id in duplicate_analysis:
        await db.execute(
            "DELETE FROM analysis_results WHERE message_id=? AND id<>?",
            (message_id, keep_id),
        )

    await db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_analysis_message ON analysis_results(message_id)"
    )

    # knowledge_items.embedding: BLOB holding numpy float32 array, populated by
    # services/embedder.py. NULL = not yet indexed.
    knowledge_columns = await db.execute_fetchall("PRAGMA table_info(knowledge_items)")
    if "embedding" not in {row[1] for row in knowledge_columns}:
        await db.execute("ALTER TABLE knowledge_items ADD COLUMN embedding BLOB")
    if "embedding_model" not in {row[1] for row in knowledge_columns}:
        # Track which model produced the vector — re-embed on model change.
        await db.execute("ALTER TABLE knowledge_items ADD COLUMN embedding_model TEXT")
