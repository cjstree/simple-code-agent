from dataclasses import dataclass


@dataclass
class Session:
    token: str
    active: bool = True


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, list[Session]] = {}

    def add(self, user_id: str, token: str) -> None:
        self._sessions.setdefault(user_id, []).append(Session(token))

    def revoke_all(self, user_id: str) -> None:
        sessions = self._sessions.get(user_id, [])
        if sessions:
            sessions[0].active = False

    def active_tokens(self, user_id: str) -> list[str]:
        return [
            session.token
            for session in self._sessions.get(user_id, [])
            if session.active
        ]
