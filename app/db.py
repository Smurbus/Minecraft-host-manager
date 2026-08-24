"""Petite couche de persistance SQLite : comptes utilisateurs et
invitations à usage unique.

Le fichier de base de données (``instance/app.db``) est généré
automatiquement au premier lancement et n'est jamais commité (voir
.gitignore). Il ne contient que des hachages de mots de passe (jamais de
mot de passe en clair).
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

    # ------------------------------------------------------------------
    # Utilisateurs
    # ------------------------------------------------------------------

    def seed_admin_from_config(self, username: str, password_hash: str) -> None:
        """Crée le compte admin initial depuis instance/config.py, une seule
        fois (si la table users est vide). Permet de ne rien casser pour les
        déploiements existants qui n'avaient qu'un seul compte en dur."""
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
        """Marque une invitation comme utilisée de façon atomique.

        Retourne False si le token n'existe pas, est expiré, ou a déjà été
        utilisé entre-temps (protection contre une double soumission
        concurrente du même lien).
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
