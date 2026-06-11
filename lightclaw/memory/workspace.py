"""SQLite workspace: conversation history + full-text searchable notes."""

from __future__ import annotations

import json
import os
import time
from typing import Any

import aiosqlite

from lightclaw.config import Config, get_config

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS conversations (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT    NOT NULL DEFAULT 'default',
    role      TEXT    NOT NULL,
    content   TEXT    NOT NULL,
    ts        REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_conv_thread ON conversations(thread_id, ts);

CREATE TABLE IF NOT EXISTS notes (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    key     TEXT    NOT NULL UNIQUE,
    value   TEXT    NOT NULL,
    tags    TEXT    NOT NULL DEFAULT '[]',
    ts      REAL    NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    key, value, tags,
    content=notes,
    content_rowid=id
);

CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
    INSERT INTO notes_fts(rowid, key, value, tags)
    VALUES (new.id, new.key, new.value, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, key, value, tags)
    VALUES ('delete', old.id, old.key, old.value, old.tags);
END;

CREATE TRIGGER IF NOT EXISTS notes_au AFTER UPDATE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, key, value, tags)
    VALUES ('delete', old.id, old.key, old.value, old.tags);
    INSERT INTO notes_fts(rowid, key, value, tags)
    VALUES (new.id, new.key, new.value, new.tags);
END;
"""


class Workspace:
    def __init__(self, config: Config | None = None) -> None:
        cfg = config or get_config()
        self._db_path = cfg.db_path
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    async def __aenter__(self) -> "Workspace":
        await self.open()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    # --- conversation history ---

    async def add_message(
        self, role: str, content: str, thread_id: str = "default"
    ) -> None:
        await self._db.execute(
            "INSERT INTO conversations(thread_id, role, content, ts) VALUES (?,?,?,?)",
            (thread_id, role, content, time.time()),
        )
        await self._db.commit()

    async def get_history(
        self, thread_id: str = "default", limit: int = 50
    ) -> list[dict[str, str]]:
        cur = await self._db.execute(
            "SELECT role, content FROM conversations "
            "WHERE thread_id=? ORDER BY ts DESC LIMIT ?",
            (thread_id, limit),
        )
        rows = await cur.fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    async def clear_history(self, thread_id: str = "default") -> None:
        await self._db.execute(
            "DELETE FROM conversations WHERE thread_id=?", (thread_id,)
        )
        await self._db.commit()

    # --- notes / persistent memory ---

    async def remember(
        self, key: str, value: str, tags: list[str] | None = None
    ) -> None:
        tags_json = json.dumps(tags or [])
        await self._db.execute(
            "INSERT INTO notes(key, value, tags, ts) VALUES(?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
            "tags=excluded.tags, ts=excluded.ts",
            (key, value, tags_json, time.time()),
        )
        await self._db.commit()

    async def recall(self, key: str) -> str | None:
        cur = await self._db.execute(
            "SELECT value FROM notes WHERE key=?", (key,)
        )
        row = await cur.fetchone()
        return row["value"] if row else None

    async def forget(self, key: str) -> bool:
        cur = await self._db.execute("DELETE FROM notes WHERE key=?", (key,))
        await self._db.commit()
        return cur.rowcount > 0

    async def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        cur = await self._db.execute(
            "SELECT n.key, n.value, n.tags FROM notes n "
            "JOIN notes_fts f ON n.id = f.rowid "
            "WHERE notes_fts MATCH ? ORDER BY rank LIMIT ?",
            (query, limit),
        )
        rows = await cur.fetchall()
        return [
            {
                "key": r["key"],
                "value": r["value"],
                "tags": json.loads(r["tags"]),
            }
            for r in rows
        ]

    async def list_notes(self, limit: int = 100) -> list[dict[str, Any]]:
        cur = await self._db.execute(
            "SELECT key, value, tags FROM notes ORDER BY ts DESC LIMIT ?",
            (limit,),
        )
        rows = await cur.fetchall()
        return [
            {"key": r["key"], "value": r["value"], "tags": json.loads(r["tags"])}
            for r in rows
        ]
