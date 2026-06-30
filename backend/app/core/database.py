import aiosqlite

from app.core.config import DATA_DIR
from app.core.time_utils import normalize_timestamp

DB_PATH = DATA_DIR / "chat.db"
TIMESTAMP_MIGRATION_BATCH_SIZE = 5000

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

-- Contact tag dictionary. Tags are platform-agnostic and reusable across
-- contacts. source='preset' are user-defined; source='ai' are LLM-proposed
-- new tags that stay status='pending' until the user approves them (flipped to
-- 'active' on first confirm). Only 'active' tags are offered to the LLM.
CREATE TABLE IF NOT EXISTS contact_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    color TEXT,
    source TEXT DEFAULT 'preset',   -- 'preset' | 'ai'
    status TEXT DEFAULT 'active',   -- 'active' | 'pending'
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Tag <-> contact assignment, keyed on (platform, chat_id) like chat_profiles.
-- AI suggestions land as status='suggested'; the review step flips selected
-- rows to 'confirmed'. Manual adds go straight to 'confirmed'.
CREATE TABLE IF NOT EXISTS contact_tag_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    tag_id INTEGER NOT NULL REFERENCES contact_tags(id) ON DELETE CASCADE,
    confidence REAL,
    reason TEXT,
    source TEXT DEFAULT 'ai',        -- 'ai' | 'manual'
    status TEXT DEFAULT 'suggested', -- 'suggested' | 'confirmed'
    batch_id TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(platform, chat_id, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_tag_links_contact ON contact_tag_links(platform, chat_id);
CREATE INDEX IF NOT EXISTS idx_tag_links_status ON contact_tag_links(status);
"""


async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(str(DB_PATH), timeout=60)
    db.row_factory = aiosqlite.Row
    # Ensure WAL mode on every connection (idempotent, persists in file).
    # busy_timeout tells SQLite to retry for N ms before raising "database is locked".
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA synchronous=NORMAL")
    await db.execute("PRAGMA busy_timeout=30000")
    return db


class connect_db:
    """Async context manager for DB connections with WAL + busy_timeout.

    Usage:
        async with connect_db() as db:
            ...

    All sync services should use this instead of raw aiosqlite.connect().
    """

    def __init__(self, *, row_factory: bool = True, timeout: float = 60):
        self._row_factory = row_factory
        self._timeout = timeout
        self._db: aiosqlite.Connection | None = None

    async def __aenter__(self) -> aiosqlite.Connection:
        self._db = await aiosqlite.connect(str(DB_PATH), timeout=self._timeout)
        if self._row_factory:
            self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.execute("PRAGMA busy_timeout=30000")
        return self._db

    async def __aexit__(self, *exc: object) -> None:
        if self._db:
            await self._db.close()


async def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(DB_PATH), timeout=60) as db:
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

    await _normalize_existing_timestamps(db, "messages", "timestamp")
    await _normalize_existing_timestamps(db, "sync_state", "last_timestamp")

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

    # analysis_results.done: 0/1 completion flag for the todo board. Only
    # category='todo' rows are surfaced there; default 0 = open.
    analysis_columns = await db.execute_fetchall("PRAGMA table_info(analysis_results)")
    if "done" not in {row[1] for row in analysis_columns}:
        await db.execute("ALTER TABLE analysis_results ADD COLUMN done INTEGER DEFAULT 0")

    # knowledge_items.embedding: BLOB holding numpy float32 array, populated by
    # services/embedder.py. NULL = not yet indexed.
    knowledge_columns = await db.execute_fetchall("PRAGMA table_info(knowledge_items)")
    if "embedding" not in {row[1] for row in knowledge_columns}:
        await db.execute("ALTER TABLE knowledge_items ADD COLUMN embedding BLOB")
    if "embedding_model" not in {row[1] for row in knowledge_columns}:
        # Track which model produced the vector — re-embed on model change.
        await db.execute("ALTER TABLE knowledge_items ADD COLUMN embedding_model TEXT")


async def _normalize_existing_timestamps(db: aiosqlite.Connection, table: str, column: str) -> None:
    rows = await db.execute_fetchall(
        f"""SELECT rowid, {column} AS ts
            FROM {table}
            WHERE {column} IS NOT NULL
              AND (
                  CAST({column} AS TEXT) NOT GLOB '????-??-??T??:??:??*'
                  OR CAST({column} AS TEXT) GLOB '*Z'
                  OR CAST({column} AS TEXT) GLOB '*+??:??'
                  OR CAST({column} AS TEXT) GLOB '*-??:??'
              )
            ORDER BY rowid
            LIMIT ?""",
        (TIMESTAMP_MIGRATION_BATCH_SIZE,),
    )
    for row in rows:
        raw = row["ts"] if isinstance(row, aiosqlite.Row) else row[1]
        normalized = normalize_timestamp(raw)
        if normalized and normalized != raw:
            rowid = row["rowid"] if isinstance(row, aiosqlite.Row) else row[0]
            await db.execute(
                f"UPDATE {table} SET {column}=? WHERE rowid=?",
                (normalized, rowid),
            )
