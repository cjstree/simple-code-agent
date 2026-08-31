from account_repository import AccountRepository
from account_service import AccountService
from audit_log import AuditEvent, AuditLog
from session_store import SessionStore


def test_reset_revokes_more_than_two_sessions_and_audits_once() -> None:
    accounts = AccountRepository({"user-9": "before"})
    sessions = SessionStore()
    for token in ("one", "two", "three"):
        sessions.add("user-9", token)
    audit_log = AuditLog()

    AccountService(accounts, sessions, audit_log).reset_password("user-9", "after")

    assert sessions.active_tokens("user-9") == []
    assert audit_log.events == (AuditEvent("password_reset", "user-9"),)


def test_reset_leaves_other_accounts_and_sessions_unchanged() -> None:
    accounts = AccountRepository({"user-1": "old", "user-2": "keep"})
    sessions = SessionStore()
    sessions.add("user-1", "reset-me")
    sessions.add("user-2", "keep-me")

    AccountService(accounts, sessions, AuditLog()).reset_password("user-1", "new")

    assert accounts.password_hash("user-2") == "keep"
    assert sessions.active_tokens("user-2") == ["keep-me"]
