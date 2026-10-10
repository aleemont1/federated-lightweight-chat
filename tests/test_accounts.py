"""Unit tests for the orchestrator's account store (real SQLite file)."""

import sqlite3

import pytest

from src.services.accounts import Accounts


@pytest.fixture
def accounts(tmp_path):
    """A fresh account store in a not-yet-existing directory."""
    return Accounts(str(tmp_path / "data" / "accounts.db"))


def test_first_login_claims_username(accounts):
    """The first login sets the password; later logins must match it."""
    assert accounts.login("alice", "correct horse") is True
    assert accounts.login("alice", "correct horse") is True
    assert accounts.login("alice", "wrong password") is False


def test_verify_never_registers(accounts):
    """verify() (used by logout) doesn't create accounts."""
    assert accounts.verify("bob", "whatever123") is False
    assert accounts.login("bob", "a-new-password") is True


def test_short_password_rejected_only_for_new_accounts(accounts):
    """The length rule applies when claiming a name; a short guess on an existing one is just wrong."""
    with pytest.raises(ValueError, match="at least 8"):
        accounts.login("carol", "short")
    accounts.login("carol", "long enough")
    assert accounts.login("carol", "short") is False


def test_passwords_are_salted_hashes(accounts):
    """No plaintext on disk, and equal passwords get different hashes."""
    accounts.login("alice", "same password")
    accounts.login("bob", "same password")

    rows = sqlite3.connect(accounts.db_path).execute("SELECT salt, hash FROM accounts").fetchall()
    assert all(b"same password" not in salt + digest for salt, digest in rows)
    assert rows[0][1] != rows[1][1]
