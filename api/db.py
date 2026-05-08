import time
import uuid
from pathlib import Path
from typing import Optional

import aiosqlite

DB_PATH = Path("forge.db")
_db: Optional[aiosqlite.Connection] = None

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS conversations (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL DEFAULT 'New Chat',
    system_prompt TEXT NOT NULL DEFAULT '',
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL,
    session_token TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    created_at      INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_conv
    ON messages(conversation_id, created_at);

CREATE INDEX IF NOT EXISTS idx_conversations_updated
    ON conversations(updated_at DESC);

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback (
    id         TEXT PRIMARY KEY,
    message    TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
"""

_SESSION_TTL_MS = 7 * 24 * 60 * 60 * 1000  # 7 days


async def init_db() -> None:
    global _db
    _db = await aiosqlite.connect(DB_PATH)
    _db.row_factory = aiosqlite.Row
    await _db.executescript(_SCHEMA)
    await _db.execute("PRAGMA synchronous=NORMAL")
    await _db.execute("PRAGMA cache_size=-32000")
    await _db.execute("PRAGMA temp_store=MEMORY")
    # Migrate existing DB: add session_token column if absent
    try:
        await _db.execute("ALTER TABLE conversations ADD COLUMN session_token TEXT NOT NULL DEFAULT ''")
        await _db.commit()
    except Exception:
        pass  # column already exists
    # Purge expired sessions on startup
    cutoff = int(time.time() * 1000) - _SESSION_TTL_MS
    await _db.execute("DELETE FROM sessions WHERE created_at < ?", (cutoff,))
    await _db.commit()


async def load_sessions() -> list[str]:
    async with _db.execute("SELECT token FROM sessions") as cur:
        return [r[0] for r in await cur.fetchall()]


async def save_session(token: str) -> None:
    await _db.execute("INSERT OR REPLACE INTO sessions VALUES (?, ?)", (token, _now()))
    await _db.commit()


async def delete_session(token: str) -> None:
    await _db.execute("DELETE FROM sessions WHERE token=?", (token,))
    await _db.commit()


async def close_db() -> None:
    global _db
    if _db:
        await _db.close()
        _db = None


def _now() -> int:
    return int(time.time() * 1000)


async def create_conversation(session_token: str, title: str = "New Chat", system_prompt: str = "") -> dict:
    now = _now()
    cid = str(uuid.uuid4())
    await _db.execute(
        "INSERT INTO conversations VALUES (?,?,?,?,?,?)",
        (cid, title, system_prompt, now, now, session_token),
    )
    await _db.commit()
    return {"id": cid, "title": title, "system_prompt": system_prompt,
            "created_at": now, "updated_at": now}


async def list_conversations(session_token: str) -> list[dict]:
    async with _db.execute(
        "SELECT id,title,system_prompt,created_at,updated_at FROM conversations "
        "WHERE session_token=? ORDER BY updated_at DESC LIMIT 100",
        (session_token,),
    ) as cur:
        return [dict(r) for r in await cur.fetchall()]


async def get_conversation(cid: str, session_token: str = "") -> Optional[dict]:
    async with _db.execute(
        "SELECT * FROM conversations WHERE id=?", (cid,)
    ) as cur:
        row = await cur.fetchone()
        if not row:
            return None
        conv = dict(row)
        if session_token and conv.get("session_token") != session_token:
            return None
        return conv


async def update_conversation(cid: str, **fields) -> None:
    if not fields:
        return
    fields["updated_at"] = _now()
    cols = ", ".join(f"{k}=?" for k in fields)
    await _db.execute(f"UPDATE conversations SET {cols} WHERE id=?",
                      (*fields.values(), cid))
    await _db.commit()


async def delete_conversation(cid: str, session_token: str) -> None:
    await _db.execute("DELETE FROM conversations WHERE id=? AND session_token=?",
                      (cid, session_token))
    await _db.commit()


async def get_messages(cid: str) -> list[dict]:
    async with _db.execute(
        "SELECT id,role,content,created_at FROM messages "
        "WHERE conversation_id=? ORDER BY created_at",
        (cid,),
    ) as cur:
        return [dict(r) for r in await cur.fetchall()]


async def add_message(cid: str, role: str, content: str) -> dict:
    now = _now()
    mid = str(uuid.uuid4())
    await _db.execute(
        "INSERT INTO messages VALUES (?,?,?,?,?)",
        (mid, cid, role, content, now),
    )
    await _db.execute(
        "UPDATE conversations SET updated_at=? WHERE id=?", (now, cid)
    )
    await _db.commit()
    return {"id": mid, "conversation_id": cid, "role": role,
            "content": content, "created_at": now}


async def save_feedback(message: str) -> None:
    await _db.execute(
        "INSERT INTO feedback VALUES (?,?,?)",
        (str(uuid.uuid4()), message, _now()),
    )
    await _db.commit()


async def truncate_from_message(cid: str, message_id: str) -> None:
    """Delete message_id and every message after it in the conversation."""
    async with _db.execute(
        "SELECT rowid FROM messages WHERE id=?", (message_id,)
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return
    await _db.execute(
        "DELETE FROM messages WHERE conversation_id=? AND rowid>=?",
        (cid, row[0]),
    )
    await _db.commit()
