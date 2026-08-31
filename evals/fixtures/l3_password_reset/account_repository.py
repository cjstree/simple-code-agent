class AccountRepository:
    def __init__(self, passwords: dict[str, str]) -> None:
        self._passwords = passwords

    def update_password(self, user_id: str, password_hash: str) -> None:
        if user_id not in self._passwords:
            raise KeyError(user_id)
        self._passwords[user_id] = password_hash

    def password_hash(self, user_id: str) -> str:
        return self._passwords[user_id]
