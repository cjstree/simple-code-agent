from account_repository import AccountRepository
from account_service import AccountService
from audit_log import AuditEvent, AuditLog
from session_store import SessionStore


def test_password_reset_revokes_every_session_and_records_audit_event() -> None:
    accounts = AccountRepository({"user-1": "old-hash"})
    sessions = SessionStore()
    sessions.add("user-1", "first")
    sessions.add("user-1", "second")
    audit_log = AuditLog()

    AccountService(accounts, sessions, audit_log).reset_password("user-1", "new-hash")

    assert sessions.active_tokens("user-1") == []
    assert audit_log.events == (AuditEvent("password_reset", "user-1"),)


def test_password_reset_still_updates_the_password() -> None:
    accounts = AccountRepository({"user-1": "old-hash"})
    service = AccountService(accounts, SessionStore(), AuditLog())

    service.reset_password("user-1", "new-hash")

    assert accounts.password_hash("user-1") == "new-hash"
