from account_repository import AccountRepository
from audit_log import AuditLog
from session_store import SessionStore


class AccountService:
    def __init__(
        self,
        accounts: AccountRepository,
        sessions: SessionStore,
        audit_log: AuditLog,
    ) -> None:
        self._accounts = accounts
        self._sessions = sessions
        self._audit_log = audit_log

    def reset_password(self, user_id: str, new_password_hash: str) -> None:
        self._accounts.update_password(user_id, new_password_hash)
        self._sessions.revoke_all(user_id)
