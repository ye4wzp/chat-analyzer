import aiosqlite

from app.core.config import DATA_DIR
from app.core.time_utils import normalize_timestamp

DB_PATH = DATA_DIR / "chat.db"
TIMESTAMP_MIGRATION_BATCH_SIZE = 5000

_RESOURCES_DDL = """
-- Extracted links / files / mini-programs / payments, one row per item.
CREATE TABLE IF NOT EXISTS resources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL,
    account TEXT NOT NULL DEFAULT '',
    platform TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    chat_name TEXT,
    sender_name TEXT,
    timestamp TEXT NOT NULL,
    kind TEXT NOT NULL,              -- 'link' | 'file' | 'miniprogram' | 'redpacket' | 'transfer'
    url TEXT,
    title TEXT,
    -- Dedup key deliberately excludes title: indexing 500k titles doubled the
    -- table's footprint, and one message yields at most one row per (kind, url).
    UNIQUE(message_id, kind, url)
);
CREATE INDEX IF NOT EXISTS idx_res_kind_ts ON resources(kind, timestamp);
CREATE INDEX IF NOT EXISTS idx_res_chat_ts ON resources(platform, chat_id, timestamp);
"""

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS wechat_articles (
    url TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    source TEXT NOT NULL,
    saved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS wechat_archive_accounts (
    chat_id TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_id INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS wechat_archive_items (
    chat_id TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    error TEXT,
    cooldown_until INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(chat_id, url)
);
CREATE INDEX IF NOT EXISTS idx_archive_url ON wechat_archive_items(url);
CREATE INDEX IF NOT EXISTS idx_archive_cooldown ON wechat_archive_items(cooldown_until);
CREATE INDEX IF NOT EXISTS idx_archive_pending ON wechat_archive_items(message_id DESC) WHERE error IS NULL;

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

CREATE INDEX IF NOT EXISTS idx_wechat_archive_messages ON messages(chat_id, id)
WHERE platform='wechat' AND chat_id GLOB 'gh_*';
CREATE INDEX IF NOT EXISTS idx_msg_platform_ts ON messages(platform, timestamp);
CREATE INDEX IF NOT EXISTS idx_msg_chat_ts ON messages(chat_id, timestamp);
-- Standalone timestamp index for the unfiltered hot paths: the default message
-- list (ORDER BY timestamp DESC) and the dashboard's N-day window, neither of
-- which carries a platform/chat prefix to use the composite indexes above.
CREATE INDEX IF NOT EXISTS idx_msg_ts ON messages(timestamp);

-- People, not chat windows. A person owns identities across platforms:
--   kind='chat'   value=chat_id      — the private chat with them
--   kind='sender' value=sender_key   — how they appear inside groups
--                 chat_id=''         →  anywhere on that platform (WeChat has no
--                 wxid in groups, so a nickname is platform-wide); a specific
--                 group id scopes the identity to that group only.
-- VIP / pre-filter / profile aggregation all resolve through this table.
-- (Replaces contact_aliases; the old rows are migrated on first start.)
CREATE TABLE IF NOT EXISTS persons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    notes TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS person_identities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    kind TEXT NOT NULL,               -- 'chat' | 'sender'
    value TEXT NOT NULL,
    chat_id TEXT NOT NULL DEFAULT '', -- sender scope; '' = platform-wide
    display_name TEXT,
    source TEXT DEFAULT 'manual',     -- 'manual' | 'auto' | 'migrated'
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(platform, kind, value, chat_id)
);

CREATE INDEX IF NOT EXISTS idx_identity_person ON person_identities(person_id);

-- One cursor per downstream consumer of the message log (stats rollups,
-- kind backfill, ...). messages.id is monotonic, so "id > last" is a complete
-- incremental cursor; core/consumers.py wraps the read-process-advance loop.
CREATE TABLE IF NOT EXISTS consumers (
    name TEXT PRIMARY KEY,
    last_message_id INTEGER NOT NULL DEFAULT 0,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

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

CREATE TABLE IF NOT EXISTS qq_local_cursor (
    account TEXT NOT NULL, chat_type TEXT NOT NULL, peer TEXT NOT NULL,
    chat_id TEXT NOT NULL, ts INTEGER NOT NULL, msg_id INTEGER NOT NULL,
    PRIMARY KEY(account, chat_type, peer)
);

CREATE TABLE IF NOT EXISTS sync_state (
    platform TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    last_msg_id TEXT,
    last_timestamp DATETIME,
    -- 旧历史回补断点（页码）。NULL = 该会话历史已翻到底。
    backfill_page INTEGER,
    backfill_cutoff INTEGER,
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

-- Local contact notes and review decisions. AI drafts never write effective notes/tags.
CREATE TABLE IF NOT EXISTS contact_dossiers (
    platform TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    short_note TEXT NOT NULL DEFAULT '',
    detail_note TEXT NOT NULL DEFAULT '',
    short_locked INTEGER NOT NULL DEFAULT 0,
    detail_locked INTEGER NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 0,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    contact_sender_key TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(platform, chat_id)
);
CREATE TABLE IF NOT EXISTS contact_dossier_drafts (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    base_version INTEGER NOT NULL,
    contact_sender_key TEXT NOT NULL,
    coverage_json TEXT NOT NULL,
    items_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    reviewed_at TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dossier_drafts_contact ON contact_dossier_drafts(platform, chat_id, created_at);
CREATE TABLE IF NOT EXISTS contact_dossier_rejections (
    platform TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    source_ids TEXT NOT NULL,
    PRIMARY KEY(platform, chat_id, fingerprint)
);

-- Durable full-history snapshots and per-call checkpoints; never auto-resume AI.
CREATE TABLE IF NOT EXISTS contact_dossier_runs (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    contact_sender_key TEXT NOT NULL,
    base_version INTEGER NOT NULL,
    current_json TEXT NOT NULL,
    library_json TEXT NOT NULL,
    model_json TEXT NOT NULL,
    coverage_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    stage TEXT NOT NULL DEFAULT 'extract',
    processed INTEGER NOT NULL DEFAULT 0,
    completed_batches INTEGER NOT NULL DEFAULT 0,
    total_batches INTEGER NOT NULL,
    task_id TEXT,
    error TEXT,
    draft_id TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dossier_runs_contact ON contact_dossier_runs(platform,chat_id,created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_dossier_runs_active ON contact_dossier_runs(platform,chat_id)
    WHERE status IN ('pending','running');
CREATE TABLE IF NOT EXISTS contact_dossier_units (
    run_id TEXT NOT NULL,
    unit_key TEXT NOT NULL,
    stage TEXT NOT NULL,
    input_json TEXT NOT NULL,
    result_json TEXT,
    completed_messages INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(run_id,unit_key)
);

-- Analyze checkpoint, mirroring sync_state: advances whenever messages are
-- PROCESSED (even if zero items came out), decoupled from what the user saves.
CREATE TABLE IF NOT EXISTS analysis_state (
    platform TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    last_analyzed_ts DATETIME,
    -- id of the last processed message AT last_analyzed_ts. Together they form a
    -- (timestamp, id) cursor so a LIMIT boundary inside a same-second burst can't
    -- skip the unprocessed remainder. NULL = legacy checkpoint (pre-upgrade):
    -- fall back to strict `> ts` until the next run records a concrete id.
    last_analyzed_id INTEGER,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (platform, chat_id)
);

-- One row per analyze run; extracted items wait in pending_items until the
-- user reviews them. Both survive restarts (the old in-memory pending store
-- lost scheduled runs' output after 1h).
CREATE TABLE IF NOT EXISTS analysis_batches (
    task_id TEXT PRIMARY KEY,
    summary TEXT,
    status TEXT DEFAULT 'pending',   -- 'pending' | 'confirmed' | 'discarded'
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pending_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    category TEXT,
    urgency INTEGER DEFAULT 3,
    tags TEXT,
    source_chat TEXT,
    source_message_ids TEXT,
    batch_id TEXT,
    status TEXT DEFAULT 'pending',   -- 'pending' | 'saved' | 'rejected'
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_pending_task ON pending_items(task_id, status);

-- Scheduler run stamps. Config keeps the enabled/interval settings; run STATE
-- lives here so the scheduler loop never rewrites config.json.
CREATE TABLE IF NOT EXISTS scheduler_state (
    job TEXT PRIMARY KEY,
    last_run_at TEXT,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Generated weekly/monthly digests (services/digest.py). content is Markdown;
-- period_start/end are local-naive ISO matching messages.timestamp.
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period TEXT NOT NULL,            -- 'weekly' | 'monthly' | 'yearly'
    period_start DATETIME NOT NULL,
    period_end DATETIME NOT NULL,
    content TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ---------------------------------------------------------------------------
-- Pre-aggregated statistics (services/stats.py). Every table is keyed on
-- (account, platform, chat_id, ...) and refreshed incrementally by walking
-- messages.id past stats_state.last_message_id, so overview/heatmap/member
-- queries never scan the multi-million-row messages table.
-- `kind` is the cross-platform message kind (see stats.classify_kind).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats_daily (
    account TEXT NOT NULL DEFAULT '',
    platform TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    day TEXT NOT NULL,               -- YYYY-MM-DD (local)
    kind TEXT NOT NULL,
    n INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (account, platform, chat_id, day, kind)
);
CREATE INDEX IF NOT EXISTS idx_stats_daily_day ON stats_daily(day);

CREATE TABLE IF NOT EXISTS stats_hourly (
    account TEXT NOT NULL DEFAULT '',
    platform TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    hour INTEGER NOT NULL,
    n INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (account, platform, chat_id, hour)
);

-- Per-chat rollup: latest name/type, totals, first/last activity.
CREATE TABLE IF NOT EXISTS stats_chats (
    account TEXT NOT NULL DEFAULT '',
    platform TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    chat_name TEXT,
    chat_type TEXT,
    n INTEGER NOT NULL DEFAULT 0,
    first_ts TEXT,
    last_ts TEXT,
    PRIMARY KEY (account, platform, chat_id)
);

-- sender_key = sender_id when the platform provides one, else sender_name
-- (wx-cli group messages carry only nicknames).
CREATE TABLE IF NOT EXISTS stats_sender (
    account TEXT NOT NULL DEFAULT '',
    platform TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    sender_key TEXT NOT NULL,
    sender_name TEXT,
    month TEXT NOT NULL,             -- YYYY-MM
    n INTEGER NOT NULL DEFAULT 0,
    first_ts TEXT,
    last_ts TEXT,
    PRIMARY KEY (account, platform, chat_id, sender_key, month)
);

CREATE TABLE IF NOT EXISTS stats_sender_hourly (
    account TEXT NOT NULL DEFAULT '',
    platform TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    sender_key TEXT NOT NULL,
    hour INTEGER NOT NULL,
    n INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (account, platform, chat_id, sender_key, hour)
);

{resources_ddl}

CREATE TABLE IF NOT EXISTS stats_state (
    key TEXT PRIMARY KEY,
    value TEXT
);
""".replace("{resources_ddl}", _RESOURCES_DDL)

# Set by init_db(). False when the SQLite build lacks FTS5 — search and
# trigger scans then fall back to LIKE.
FTS_ENABLED = False


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
        await _init_fts(db)
        await db.commit()


async def _init_fts(db: aiosqlite.Connection) -> None:
    """External-content FTS5 index over messages.content, trigram-tokenized so
    CJK substring queries (>= 3 chars) work. Kept in sync by triggers; rebuilt
    when row counts diverge (first run, or writes made while FTS was broken)."""
    global FTS_ENABLED
    try:
        await db.execute(
            """CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                   content, content='messages', content_rowid='id', tokenize='trigram')"""
        )
        await db.execute(
            """CREATE TRIGGER IF NOT EXISTS messages_fts_ai AFTER INSERT ON messages BEGIN
                   INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
               END"""
        )
        await db.execute(
            """CREATE TRIGGER IF NOT EXISTS messages_fts_ad AFTER DELETE ON messages BEGIN
                   INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
               END"""
        )
        await db.execute(
            """CREATE TRIGGER IF NOT EXISTS messages_fts_au AFTER UPDATE OF content ON messages BEGIN
                   INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
                   INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
               END"""
        )
        msg_count = (await db.execute_fetchall("SELECT COUNT(*) FROM messages"))[0][0]
        fts_count = (await db.execute_fetchall("SELECT COUNT(*) FROM messages_fts"))[0][0]
        if msg_count != fts_count:
            await db.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")
        FTS_ENABLED = True
    except Exception as e:  # SQLite built without FTS5/trigram
        FTS_ENABLED = False
        import logging
        logging.getLogger("app.database").warning("FTS5 unavailable, falling back to LIKE: %s", e)


async def _migrate_db(db: aiosqlite.Connection) -> None:
    columns = await db.execute_fetchall("PRAGMA table_info(messages)")
    message_columns = {row[1] for row in columns}
    if "source_id" not in message_columns:
        await db.execute("ALTER TABLE messages ADD COLUMN source_id TEXT")
    # messages.account: which of the user's own accounts (per platform) the
    # message was pulled through. NULL/'' = the default/only account. Powers
    # the 主号/多号 switch; stats tables carry it as a dimension.
    if "account" not in message_columns:
        await db.execute("ALTER TABLE messages ADD COLUMN account TEXT")
    # messages.kind: cross-platform message kind, classified once at ingest
    # (services/stats.classify_kind) so every consumer reads one vocabulary
    # instead of re-interpreting per-platform msg_type strings. NULL rows are
    # legacy and get backfilled by the 'kind_backfill' consumer in the background.
    if "kind" not in message_columns:
        await db.execute("ALTER TABLE messages ADD COLUMN kind TEXT")
    # messages.tz_offset: minutes east of UTC of the machine at ingest time.
    # timestamp is local-naive; without this, syncing while travelling skews
    # the hour-of-day statistics silently.
    if "tz_offset" not in message_columns:
        await db.execute("ALTER TABLE messages ADD COLUMN tz_offset INTEGER")

    # knowledge_items: stable provenance. source_chat is a display name and
    # rots on rename; (source_platform, source_chat_id) is the real key.
    knowledge_cols = {row[1] for row in await db.execute_fetchall("PRAGMA table_info(knowledge_items)")}
    if "source_chat_id" not in knowledge_cols:
        await db.execute("ALTER TABLE knowledge_items ADD COLUMN source_platform TEXT")
        await db.execute("ALTER TABLE knowledge_items ADD COLUMN source_chat_id TEXT")
    await _backfill_knowledge_sources(db)
    await _migrate_aliases_to_persons(db)

    sync_columns = {row[1] for row in await db.execute_fetchall("PRAGMA table_info(sync_state)")}
    if "backfill_page" not in sync_columns:
        await db.execute("ALTER TABLE sync_state ADD COLUMN backfill_page INTEGER")
    if "backfill_cutoff" not in sync_columns:
        await db.execute("ALTER TABLE sync_state ADD COLUMN backfill_cutoff INTEGER")

    state_columns = {row[1] for row in await db.execute_fetchall("PRAGMA table_info(analysis_state)")}
    if state_columns and "last_analyzed_id" not in state_columns:
        # NULL for existing rows → strict `> ts` until the next run stamps a real id.
        await db.execute("ALTER TABLE analysis_state ADD COLUMN last_analyzed_id INTEGER")

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

    # resources: v1 keyed UNIQUE(message_id, kind, url, title) which indexed
    # every title string (~200MB at 500k rows). The table is derived data, so
    # drop and recreate; the stats version bump triggers a rebuild on startup.
    for idx in await db.execute_fetchall("PRAGMA index_list(resources)"):
        if idx[2] and idx[1].startswith("sqlite_autoindex"):  # unique autoindex
            cols = await db.execute_fetchall(f"PRAGMA index_info({idx[1]})")
            if len(cols) == 4:
                await db.execute("DROP TABLE resources")
                await db.executescript(_RESOURCES_DDL)
                await db.execute("DELETE FROM stats_state WHERE key = 'version'")
            break

    # Unreviewed batches older than 30 days are stale; drop them so scheduled
    # runs nobody reviews don't grow the table unbounded.
    await db.execute(
        """DELETE FROM pending_items WHERE task_id IN
           (SELECT task_id FROM analysis_batches WHERE created_at < datetime('now', '-30 days'))"""
    )
    await db.execute("DELETE FROM analysis_batches WHERE created_at < datetime('now', '-30 days')")


async def _backfill_knowledge_sources(db: aiosqlite.Connection) -> None:
    """Resolve (source_platform, source_chat_id) for legacy knowledge rows from
    their first source message id; fall back to matching the stored chat name."""
    import json

    rows = await db.execute_fetchall(
        """SELECT id, source_chat, source_message_ids FROM knowledge_items
           WHERE source_chat_id IS NULL AND (source_message_ids IS NOT NULL OR source_chat IS NOT NULL)"""
    )
    for kid, name, raw_ids in rows:
        found = None
        try:
            ids = [int(x) for x in json.loads(raw_ids or "[]") if str(x).isdigit()]
        except (ValueError, TypeError):
            ids = []
        if ids:
            hit = await db.execute_fetchall(
                "SELECT platform, chat_id FROM messages WHERE id = ? LIMIT 1", (ids[0],)
            )
            if hit:
                found = (hit[0][0], hit[0][1])
        if found is None and name:
            hit = await db.execute_fetchall(
                """SELECT platform, chat_id FROM messages WHERE chat_name = ?
                   ORDER BY timestamp DESC LIMIT 1""",
                (name,),
            )
            if hit:
                found = (hit[0][0], hit[0][1])
        if found:
            await db.execute(
                "UPDATE knowledge_items SET source_platform = ?, source_chat_id = ? WHERE id = ?",
                (found[0], found[1], kid),
            )


async def _migrate_aliases_to_persons(db: aiosqlite.Connection) -> None:
    """One-time move of contact_aliases (canonical_name → platform ids) into
    persons / person_identities. The old table is dropped afterwards so there
    is a single source of truth for identity."""
    exists = await db.execute_fetchall(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'contact_aliases'"
    )
    if not exists:
        return
    rows = await db.execute_fetchall(
        "SELECT canonical_name, platform, platform_id, platform_name FROM contact_aliases ORDER BY id"
    )
    person_ids: dict[str, int] = {}
    for canonical, platform, platform_id, platform_name in rows:
        pid = person_ids.get(canonical)
        if pid is None:
            hit = await db.execute_fetchall("SELECT id FROM persons WHERE name = ?", (canonical,))
            if hit:
                pid = int(hit[0][0])
            else:
                cur = await db.execute("INSERT INTO persons (name) VALUES (?)", (canonical,))
                pid = int(cur.lastrowid)
            person_ids[canonical] = pid
        await db.execute(
            """INSERT OR IGNORE INTO person_identities
               (person_id, platform, kind, value, chat_id, display_name, source)
               VALUES (?, ?, 'chat', ?, '', ?, 'migrated')""",
            (pid, platform, platform_id, platform_name),
        )
    await db.execute("DROP TABLE contact_aliases")


async def _normalize_existing_timestamps(db: aiosqlite.Connection, table: str, column: str) -> None:
    """Convert legacy non-ISO timestamps (QQ epoch digits, tz-suffixed strings)
    to local-naive ISO so string comparison sorts correctly. Loops in batches
    until drained — a partial migration leaves the table in mixed formats, which
    breaks every `>`/`ORDER BY` on the column (analysis cursor, message list,
    dashboard window)."""
    while True:
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
        if not rows:
            return
        migrated = 0
        for row in rows:
            raw = row["ts"] if isinstance(row, aiosqlite.Row) else row[1]
            normalized = normalize_timestamp(raw)
            if normalized and normalized != raw:
                rowid = row["rowid"] if isinstance(row, aiosqlite.Row) else row[0]
                await db.execute(
                    f"UPDATE {table} SET {column}=? WHERE rowid=?",
                    (normalized, rowid),
                )
                migrated += 1
        # No row in this batch could be normalized further (e.g. unparseable
        # values that keep matching the filter) — stop to avoid an infinite loop.
        if migrated == 0:
            return
