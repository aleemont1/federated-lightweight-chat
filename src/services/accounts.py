"""
Orchestrator accounts. A username is claimed by its first login, which sets the password.
Passwords are stored as salted scrypt hashes (stdlib, memory-hard).
"""

import hashlib
import hmac
import os
import secrets
import sqlite3
from contextlib import closing

MIN_PASSWORD_LENGTH = 8


def _hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)


class Accounts:
    """Username/password store backed by SQLite."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS accounts (username TEXT PRIMARY KEY, salt BLOB, hash BLOB)"
            )
            conn.commit()

    def verify(self, username: str, password: str) -> bool:
        """True if the account exists and the password matches."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute("SELECT salt, hash FROM accounts WHERE username = ?", (username,)).fetchone()
        return row is not None and hmac.compare_digest(_hash(password, row[0]), row[1])

    def login(self, username: str, password: str) -> bool:
        """
        Verifies the password, or claims the username if nobody has it yet.
        Raises ValueError if a new account's password is too short.
        """
        salt = secrets.token_bytes(16)
        with closing(sqlite3.connect(self.db_path)) as conn:
            if conn.execute("SELECT 1 FROM accounts WHERE username = ?", (username,)).fetchone() is None:
                if len(password) < MIN_PASSWORD_LENGTH:
                    raise ValueError(f"Choose a password of at least {MIN_PASSWORD_LENGTH} characters.")
                # OR IGNORE: if a concurrent first login won the race, fall through to verify().
                conn.execute(
                    "INSERT OR IGNORE INTO accounts VALUES (?, ?, ?)", (username, salt, _hash(password, salt))
                )
                conn.commit()
        return self.verify(username, password)
