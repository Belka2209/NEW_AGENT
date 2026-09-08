from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5-coder:14b"
    max_agent_steps: int = 12
    terminal_timeout: int = 45

    model_config = SettingsConfigDict(
        env_file=ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def workspace_dir(self) -> Path:
        path = ROOT / "workspace"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def db_path(self) -> Path:
        data = ROOT / "data"
        data.mkdir(parents=True, exist_ok=True)
        return data / "agent.db"

    @property
    def web_dir(self) -> Path:
        return ROOT / "web"


settings = Settings()
