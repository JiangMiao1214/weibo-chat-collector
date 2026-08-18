from functools import lru_cache
from pathlib import Path

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    app_env: str = "development"
    database_path: Path = PROJECT_ROOT / "data" / "weibo_chat_collector.sqlite3"
    attachments_dir: Path = PROJECT_ROOT / "data" / "attachments"
    imports_dir: Path = PROJECT_ROOT / "data" / "imports"
    weibo_api_auth_dir: Path = PROJECT_ROOT / "data" / "auth"
    default_collection_days: int = 7
    delete_mode: str = "soft"
    weibo_api_timezone: str = "Asia/Shanghai"
    weibo_api_page_size: int = 20
    weibo_api_max_pages: int = 500
    weibo_api_page_delay_min_seconds: float = 3.0
    weibo_api_page_delay_max_seconds: float = 8.0
    weibo_api_long_rest_every_pages: int = 20
    weibo_api_long_rest_min_seconds: float = 30.0
    weibo_api_long_rest_max_seconds: float = 60.0
    weibo_api_request_timeout_seconds: float = 30.0
    # Accepted only so databases with the former .env template keep starting.
    # These values are intentionally unused: all errors now stop immediately.
    weibo_api_page_delay_seconds: float | None = None
    weibo_api_max_retries: int | None = None
    weibo_api_retry_base_seconds: float | None = None

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="forbid",
    )

    @field_validator(
        "database_path",
        "attachments_dir",
        "imports_dir",
        "weibo_api_auth_dir",
        mode="after",
    )
    @classmethod
    def resolve_project_path(cls, value: Path) -> Path:
        if value.is_absolute():
            return value
        return (PROJECT_ROOT / value).resolve()

    @model_validator(mode="after")
    def validate_collection_limits(self) -> "Settings":
        if self.weibo_api_page_size < 1 or self.weibo_api_max_pages < 1:
            raise ValueError("Weibo API page size and page limit must be positive")
        if self.weibo_api_request_timeout_seconds <= 0:
            raise ValueError("Weibo API request timeout must be positive")
        if self.weibo_api_long_rest_every_pages < 1:
            raise ValueError("Weibo API long-rest page interval must be positive")
        delay_ranges = (
            (
                self.weibo_api_page_delay_min_seconds,
                self.weibo_api_page_delay_max_seconds,
                "page delay",
            ),
            (
                self.weibo_api_long_rest_min_seconds,
                self.weibo_api_long_rest_max_seconds,
                "long rest",
            ),
        )
        for minimum, maximum, label in delay_ranges:
            if minimum < 0 or maximum < minimum:
                raise ValueError(f"Weibo API {label} range is invalid")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
