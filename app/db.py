"""Small SQLite persistence layer: user accounts and single-use
invitations.

The database file (``instance/app.db``) is generated automatically on first
start and is never committed (see .gitignore). It contains only password
hashes (never plain-text passwords).
"""

from __future__ import annotations

import os
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from werkzeug.security import check_password_hash

ROLE_ADMIN = "admin"
ROLE_USER = "user"

EVENT_LOGIN = "login"
EVENT_START = "start"
EVENT_STOP = "stop"


def _db_path(instance_path: str) -> str:
    return os.path.join(instance_path, "app.db")


@dataclass
class User:
    id: int
    username: str
    password_hash: str
    role: str
    created_at: str

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN


@dataclass
class Invite:
    id: int
    token: str
    role: str
    created_by: Optional[str]
    created_at: str
    expires_at: str
    used_at: Optional[str]
    used_by: Optional[str]

    def is_valid(self) -> bool:
        if self.used_at is not None:
            return False
        expires = datetime.fromisoformat(self.expires_at)
        return datetime.now(timezone.utc) < expires


@dataclass
class ActivityEntry:
    id: int
    user_id: Optional[int]
    username: str
    event_type: str
    ip_address: Optional[str]
    details: Optional[str]
    created_at: str


class Database:
    def __init__(self, instance_path: str):
        self.path = _db_path(instance_path)
        os.makedirs(instance_path, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('admin', 'user')),
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS invites (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token TEXT UNIQUE NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('admin', 'user')),
                    created_by TEXT,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    used_at TEXT,
                    used_by TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS activity_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    username TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    ip_address TEXT,
                    details TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_activity_user_id ON activity_log(user_id)"
            )

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------

    def seed_admin_from_config(self, username: str, password_hash: str) -> None:
        """Creates the initial admin account from instance/config.py only
        once (if the users table is empty). This preserves existing
        deployments that previously had only one hard-coded account."""
        with self._connect() as conn:
            count = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
            if count > 0:
                return
            conn.execute(
                "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
                (username, password_hash, ROLE_ADMIN, datetime.now(timezone.utc).isoformat()),
            )

    def get_user_by_username(self, username: str) -> Optional[User]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            ).fetchone()
            return User(**dict(row)) if row else None

    def username_exists(self, username: str) -> bool:
        return self.get_user_by_username(username) is not None

    def create_user(self, username: str, password_hash: str, role: str) -> User:
        created_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
                (username, password_hash, role, created_at),
            )
            return User(
                id=cursor.lastrowid,
                username=username,
                password_hash=password_hash,
                role=role,
                created_at=created_at,
            )

    def verify_password(self, username: str, password: str) -> Optional[User]:
        user = self.get_user_by_username(username)
        if user is None:
            return None
        if not check_password_hash(user.password_hash, password):
            return None
        return user

    def list_users(self) -> list[User]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY created_at").fetchall()
            return [User(**dict(row)) for row in rows]

    def get_user_by_id(self, user_id: int) -> Optional[User]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            return User(**dict(row)) if row else None

    def count_admins(self) -> int:
        with self._connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) AS c FROM users WHERE role = ?", (ROLE_ADMIN,)
            ).fetchone()["c"]

    def update_user(self, user_id: int, username: str, role: str) -> None:
        """Renames an account and/or changes its role.

        Raises ValueError if the new username is already taken by another
        account. The "last administrator" safeguards are the caller's
        responsibility (app/admin.py), because it has access to the session
        and knows who is performing the operation.
        """
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id FROM users WHERE username = ? AND id != ?", (username, user_id)
            ).fetchone()
            if existing:
                raise ValueError(f"The username '{username}' is already taken.")
            conn.execute(
                "UPDATE users SET username = ?, role = ? WHERE id = ?",
                (username, role, user_id),
            )

    def set_password(self, user_id: int, password_hash: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id)
            )

    def delete_user(self, user_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))

    # ------------------------------------------------------------------
    # Activity log (logins + start/stop actions)
    # ------------------------------------------------------------------

    def log_activity(
        self,
        user_id: Optional[int],
        username: str,
        event_type: str,
        ip_address: Optional[str] = None,
        details: Optional[str] = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO activity_log (user_id, username, event_type, ip_address, details, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, username, event_type, ip_address, details, datetime.now(timezone.utc).isoformat()),
            )

    def list_activity_for_user(self, user_id: int, limit: int = 200) -> list[ActivityEntry]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM activity_log
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
            return [ActivityEntry(**dict(row)) for row in rows]

    # ------------------------------------------------------------------
    # Invitations
    # ------------------------------------------------------------------

    def create_invite(self, created_by: str, role: str, lifetime_hours: int) -> Invite:
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(hours=lifetime_hours)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO invites (token, role, created_by, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (token, role, created_by, now.isoformat(), expires_at.isoformat()),
            )
            return Invite(
                id=cursor.lastrowid,
                token=token,
                role=role,
                created_by=created_by,
                created_at=now.isoformat(),
                expires_at=expires_at.isoformat(),
                used_at=None,
                used_by=None,
            )

    def get_invite_by_token(self, token: str) -> Optional[Invite]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM invites WHERE token = ?", (token,)
            ).fetchone()
            return Invite(**dict(row)) if row else None

    def consume_invite(self, token: str, used_by: str) -> bool:
        """Marks an invitation as used atomically.

        Returns False if the token does not exist, has expired, or has
        already been used in the meantime (protection against concurrent
        double submission of the same link).
        """
        now = datetime.now(timezone.utc)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE invites
                SET used_at = ?, used_by = ?
                WHERE token = ? AND used_at IS NULL AND expires_at > ?
                """,
                (now.isoformat(), used_by, token, now.isoformat()),
            )
            return cursor.rowcount == 1

    def list_invites(self) -> list[Invite]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM invites ORDER BY created_at DESC"
            ).fetchall()
            return [Invite(**dict(row)) for row in rows]
