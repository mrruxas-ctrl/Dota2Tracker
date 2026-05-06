from __future__ import annotations

import sqlite3
import threading
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def normalize_id(value: str | None) -> str:
    if value is None:
        return ""
    return " ".join(str(value).strip().split()).casefold()


def extract_digits(value: str | None) -> str:
    matches = re.findall(r"\d+", str(value or ""))
    if not matches:
        return ""
    return max(matches, key=len)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class TrackerDatabase:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self.init_schema()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS players (
                    id_key TEXT PRIMARY KEY,
                    steam_id_text TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    seen_count INTEGER NOT NULL DEFAULT 1,
                    steam_level INTEGER,
                    level_checked_at TEXT
                );

                CREATE TABLE IF NOT EXISTS ignored_ids (
                    id_key TEXT PRIMARY KEY,
                    raw_text TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                """
            )
            self._ensure_column_locked("players", "steam_level", "INTEGER")
            self._ensure_column_locked("players", "level_checked_at", "TEXT")
            self._conn.commit()

    def _ensure_column_locked(self, table: str, column: str, definition: str) -> None:
        rows = self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        existing = {str(row["name"]) for row in rows}
        if column not in existing:
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def upsert_player(self, raw_text: str) -> dict[str, Any]:
        digits = extract_digits(raw_text)
        key = normalize_id(digits)
        if not key:
            return {"status": "empty", "id_key": "", "steam_id_text": raw_text}

        with self._lock:
            if self._is_ignored_key_locked(key):
                return {"status": "ignored", "id_key": key, "steam_id_text": digits}

            now = utc_now()
            existing = self._conn.execute(
                "SELECT id_key, seen_count FROM players WHERE id_key = ?",
                (key,),
            ).fetchone()
            if existing:
                self._conn.execute(
                    """
                    UPDATE players
                    SET updated_at = ?, last_seen_at = ?, seen_count = seen_count + 1
                    WHERE id_key = ?
                    """,
                    (now, now, key),
                )
                self._conn.commit()
                return {"status": "updated", "id_key": key, "steam_id_text": digits}

            self._conn.execute(
                """
                INSERT INTO players
                    (id_key, steam_id_text, note, created_at, updated_at, last_seen_at, seen_count)
                VALUES (?, ?, '', ?, ?, ?, 1)
                """,
                (key, digits, now, now, now),
            )
            self._conn.commit()
            return {"status": "inserted", "id_key": key, "steam_id_text": digits}

    def get_players(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT
                    id_key,
                    steam_id_text,
                    note,
                    created_at,
                    updated_at,
                    last_seen_at,
                    seen_count,
                    steam_level,
                    level_checked_at
                FROM players
                ORDER BY updated_at DESC, created_at DESC
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def update_player_note(self, id_key: str, note: str) -> None:
        key = normalize_id(id_key)
        with self._lock:
            self._conn.execute(
                "UPDATE players SET note = ?, updated_at = ? WHERE id_key = ?",
                (note, utc_now(), key),
            )
            self._conn.commit()

    def update_player_level(self, id_key: str, steam_level: int | None) -> None:
        key = normalize_id(id_key)
        if not key:
            return

        with self._lock:
            self._conn.execute(
                "UPDATE players SET steam_level = ?, level_checked_at = ? WHERE id_key = ?",
                (steam_level, utc_now(), key),
            )
            self._conn.commit()

    def delete_player(self, id_key: str) -> None:
        key = normalize_id(id_key)
        with self._lock:
            self._conn.execute("DELETE FROM players WHERE id_key = ?", (key,))
            self._conn.commit()

    def add_ignored(self, raw_text: str, note: str = "") -> dict[str, Any]:
        digits = extract_digits(raw_text)
        key = normalize_id(digits)
        if not key:
            return {"status": "empty", "id_key": "", "raw_text": raw_text}

        with self._lock:
            now = utc_now()
            existing = self._conn.execute(
                "SELECT id_key FROM ignored_ids WHERE id_key = ?",
                (key,),
            ).fetchone()
            if existing:
                self._conn.execute(
                    "UPDATE ignored_ids SET raw_text = ?, note = ? WHERE id_key = ?",
                    (digits, note, key),
                )
                self._conn.commit()
                return {"status": "updated", "id_key": key, "raw_text": digits}

            self._conn.execute(
                """
                INSERT INTO ignored_ids (id_key, raw_text, note, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (key, digits, note, now),
            )
            self._conn.commit()
            return {"status": "inserted", "id_key": key, "raw_text": digits}

    def get_ignored(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id_key, raw_text, note, created_at
                FROM ignored_ids
                ORDER BY created_at DESC
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def update_ignored_note(self, id_key: str, note: str) -> None:
        key = normalize_id(id_key)
        with self._lock:
            self._conn.execute(
                "UPDATE ignored_ids SET note = ? WHERE id_key = ?",
                (note, key),
            )
            self._conn.commit()

    def delete_ignored(self, id_key: str) -> None:
        key = normalize_id(id_key)
        with self._lock:
            self._conn.execute("DELETE FROM ignored_ids WHERE id_key = ?", (key,))
            self._conn.commit()

    def is_ignored(self, raw_text: str) -> bool:
        key = normalize_id(raw_text)
        with self._lock:
            return self._is_ignored_key_locked(key)

    def _is_ignored_key_locked(self, key: str) -> bool:
        if not key:
            return False
        row = self._conn.execute(
            "SELECT 1 FROM ignored_ids WHERE id_key = ? LIMIT 1",
            (key,),
        ).fetchone()
        return row is not None
