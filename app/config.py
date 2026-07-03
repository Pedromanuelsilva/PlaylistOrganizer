from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./data/app.db"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    validation_timeout_seconds: float = 10.0
    validation_concurrency: int = 3
    validation_retries: int = 1

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    def ensure_data_dir(self) -> None:
        if self.database_url.startswith("sqlite:///"):
            raw_path = self.database_url.removeprefix("sqlite:///")
            if raw_path.startswith("/"):
                db_path = Path(raw_path)
            else:
                db_path = Path(raw_path)
            db_path.parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_data_dir()
    return settings
