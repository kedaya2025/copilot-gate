"""SQLite-backed API key store with full lifecycle management.

Keys are persisted to a SQLite database file so they survive container
restarts. The store is thread-safe and supports hot creation, revocation,
status tracking (last-used, usage count), and querying — all without
restarting the container.

Schema (table: api_keys):
    id            TEXT PRIMARY KEY       -- short identifier (key_xxxx)
    key           TEXT NOT NULL UNIQUE   -- full key string (sk-copilot-...)
    name          TEXT NOT NULL          -- human-readable label
    status        TEXT NOT NULL          -- 'active' | 'revoked'
    created_at    INTEGER NOT NULL       -- unix timestamp
    revoked_at    INTEGER                -- null if not revoked
    last_used_at  INTEGER                -- null if never used
    usage_count   INTEGER NOT NULL DEFAULT 0
"""

import os
import secrets
import sqlite3
import threading
import time
from typing import Optional

# Default path for the database file. Can be overridden via DATA_DIR env var.
_DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.getcwd(), "data"))
_DB_PATH = os.path.join(_DATA_DIR, "api_keys.db")

_lock = threading.Lock()


def _get_conn() -> sqlite3.Connection:
    """Open a connection with row factory for dict-like access."""
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_db():
    """Create the table if it doesn't exist."""
    os.makedirs(_DATA_DIR, exist_ok=True)
    with _lock:
        conn = _get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
                id            TEXT PRIMARY KEY,
                key           TEXT NOT NULL UNIQUE,
                name          TEXT NOT NULL,
                status        TEXT NOT NULL DEFAULT 'active',
                created_at    INTEGER NOT NULL,
                revoked_at    INTEGER,
                last_used_at  INTEGER,
                usage_count   INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_status ON api_keys(status)")
        conn.commit()
        conn.close()


# Initialize on module import
_ensure_db()


def _row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)


def generate_key(name: str = "") -> dict:
    """Generate a new API key, persist it, and return the full key record."""
    key_id = f"key_{secrets.token_hex(4)}"
    raw_key = f"sk-copilot-{secrets.token_urlsafe(32)}"
    now = int(time.time())
    record = {
        "id": key_id,
        "key": raw_key,
        "name": name or f"Key created {time.strftime('%Y-%m-%d %H:%M', time.localtime(now))}",
        "status": "active",
        "created_at": now,
        "revoked_at": None,
        "last_used_at": None,
        "usage_count": 0,
    }
    with _lock:
        conn = _get_conn()
        conn.execute(
            """INSERT INTO api_keys (id, key, name, status, created_at, revoked_at, last_used_at, usage_count)
               VALUES (?, ?, ?, ?, ?, NULL, NULL, 0)""",
            (record["id"], record["key"], record["name"], record["status"], record["created_at"]),
        )
        conn.commit()
        conn.close()
    return record


def revoke_key(key_id: str) -> bool:
    """Revoke a key by its id. Returns True if found and revoked."""
    now = int(time.time())
    with _lock:
        conn = _get_conn()
        cur = conn.execute(
            "UPDATE api_keys SET status = 'revoked', revoked_at = ? WHERE id = ? AND status = 'active'",
            (now, key_id),
        )
        conn.commit()
        affected = cur.rowcount
        conn.close()
    return affected > 0


def is_valid_key(raw_key: str, bootstrap_key: str = "") -> bool:
    """Check if a raw key string is valid and update usage stats.

    A key is valid if it matches:
    1. The bootstrap key from the API_KEY env var (if set), or
    2. Any active key in the database.

    For database keys, last_used_at and usage_count are updated.
    """
    if not raw_key:
        return False
    if bootstrap_key and raw_key == bootstrap_key:
        return True
    now = int(time.time())
    with _lock:
        conn = _get_conn()
        row = conn.execute(
            "SELECT id FROM api_keys WHERE key = ? AND status = 'active'",
            (raw_key,),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE api_keys SET last_used_at = ?, usage_count = usage_count + 1 WHERE id = ?",
                (now, row["id"]),
            )
            conn.commit()
            conn.close()
            return True
        conn.close()
    return False


def list_keys(active_only: bool = False) -> list[dict]:
    """Return keys from the database.

    Args:
        active_only: If True, only return active keys.
    """
    with _lock:
        conn = _get_conn()
        if active_only:
            rows = conn.execute("SELECT * FROM api_keys WHERE status = 'active' ORDER BY created_at DESC").fetchall()
        else:
            rows = conn.execute("SELECT * FROM api_keys ORDER BY created_at DESC").fetchall()
        conn.close()
    return [_row_to_dict(r) for r in rows]


def get_key(key_id: str) -> Optional[dict]:
    """Return a single key by its id, or None if not found."""
    with _lock:
        conn = _get_conn()
        row = conn.execute("SELECT * FROM api_keys WHERE id = ?", (key_id,)).fetchone()
        conn.close()
    return _row_to_dict(row) if row else None


def key_stats() -> dict:
    """Return aggregate statistics about keys."""
    with _lock:
        conn = _get_conn()
        total = conn.execute("SELECT COUNT(*) as c FROM api_keys").fetchone()["c"]
        active = conn.execute("SELECT COUNT(*) as c FROM api_keys WHERE status = 'active'").fetchone()["c"]
        revoked = conn.execute("SELECT COUNT(*) as c FROM api_keys WHERE status = 'revoked'").fetchone()["c"]
        total_usage = conn.execute("SELECT COALESCE(SUM(usage_count), 0) as s FROM api_keys").fetchone()["s"]
        conn.close()
    return {
        "total": total,
        "active": active,
        "revoked": revoked,
        "total_requests": total_usage,
    }


def mask_key(key: str) -> str:
    """Mask a key for display, showing only the first 12 and last 4 characters."""
    if len(key) <= 20:
        return key[:8] + "..." if len(key) > 8 else "***"
    return key[:12] + "..." + key[-4:]
