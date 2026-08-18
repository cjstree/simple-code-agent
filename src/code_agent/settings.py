"""Environment-backed settings owned by the standalone agent project."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Runtime configuration for the coding agent."""

    llm_api_key: str | None = None
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model_name: str | None = None
    llm_temperature: float = 0.2
    llm_max_tokens: int = 1024
    mcp_url: str | None = None
    mcp_connect_timeout: float = 3.0
    phoenix_enabled: bool = False
    phoenix_collector_endpoint: str = "http://localhost:6006/v1/traces"
    phoenix_project_name: str = "code-agent-dev"

    model_config = SettingsConfigDict(
        env_file=(".env", _PROJECT_ROOT / ".env"),
        extra="ignore",
    )


settings = Settings()
