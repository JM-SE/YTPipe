from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


GOOGLE_OAUTH_SCOPES = (
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/youtube.readonly",
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    app_name: str = Field(default="ytpipe", alias="APP_NAME")
    app_env: str = Field(default="local", alias="APP_ENV")
    app_host: str = Field(default="127.0.0.1", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")
    app_secret_key: str = Field(default="replace-me", alias="APP_SECRET_KEY")
    internal_api_bearer_token: str = Field(default="replace-me-internal", alias="INTERNAL_API_BEARER_TOKEN")
    poll_quota_daily_budget: int = Field(default=500, alias="POLL_QUOTA_DAILY_BUDGET")
    poll_quota_safety_stop_enabled: bool = Field(default=True, alias="POLL_QUOTA_SAFETY_STOP_ENABLED")
    database_url: str = Field(
        default="postgresql+psycopg://postgres:postgres@localhost:5432/ytpipe",
        alias="DATABASE_URL",
    )
    google_client_id: str = Field(default="", alias="GOOGLE_CLIENT_ID")
    google_client_secret: str = Field(default="", alias="GOOGLE_CLIENT_SECRET")
    google_redirect_uri: str = Field(default="http://127.0.0.1:8000/auth/callback", alias="GOOGLE_REDIRECT_URI")

    @property
    def google_oauth_scopes(self) -> tuple[str, ...]:
        return GOOGLE_OAUTH_SCOPES

    @property
    def google_client_config(self) -> dict[str, dict[str, str | list[str]]]:
        return {
            "web": {
                "client_id": self.google_client_id,
                "client_secret": self.google_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [self.google_redirect_uri],
            }
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
