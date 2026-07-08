from functools import lru_cache
from urllib.parse import urlparse

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
    mobile_api_bearer_token: str = Field(default="replace-me-mobile", alias="MOBILE_API_BEARER_TOKEN")
    poll_quota_daily_budget: int = Field(default=500, alias="POLL_QUOTA_DAILY_BUDGET")
    poll_quota_safety_stop_enabled: bool = Field(default=True, alias="POLL_QUOTA_SAFETY_STOP_ENABLED")
    push_notifications_enabled: bool = Field(default=False, alias="PUSH_NOTIFICATIONS_ENABLED")
    expo_push_endpoint: str = Field(
        default="https://exp.host/--/api/v2/push/send",
        alias="EXPO_PUSH_ENDPOINT",
    )
    expo_push_receipts_enabled: bool = Field(default=False, alias="EXPO_PUSH_RECEIPTS_ENABLED")
    expo_push_receipts_endpoint: str = Field(
        default="https://exp.host/--/api/v2/push/getReceipts",
        alias="EXPO_PUSH_RECEIPTS_ENDPOINT",
    )
    expo_access_token: str = Field(default="", alias="EXPO_ACCESS_TOKEN")
    email_delivery_mode: str = Field(default="fake", alias="EMAIL_DELIVERY_MODE")
    resend_api_key: str = Field(default="", alias="RESEND_API_KEY")
    resend_from_email: str = Field(default="", alias="RESEND_FROM_EMAIL")
    telegram_notifications_enabled: bool = Field(default=False, alias="TELEGRAM_NOTIFICATIONS_ENABLED")
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", alias="TELEGRAM_CHAT_ID")
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

    def validate_runtime_config(self) -> None:
        """Fail clearly for unsafe non-local runtime configuration."""
        app_env = self.app_env.strip().lower()
        allowed_envs = {"local", "staging", "production"}
        if app_env not in allowed_envs:
            raise RuntimeError(
                "Unsafe runtime configuration: "
                f"APP_ENV must be one of {sorted(allowed_envs)}, got `{self.app_env}`."
            )
        if app_env == "local":
            return

        errors: list[str] = []
        self._require_non_placeholder("APP_SECRET_KEY", self.app_secret_key, "replace-me", errors)
        self._require_non_placeholder(
            "INTERNAL_API_BEARER_TOKEN",
            self.internal_api_bearer_token,
            "replace-me-internal",
            errors,
        )
        self._require_non_placeholder(
            "MOBILE_API_BEARER_TOKEN",
            self.mobile_api_bearer_token,
            "replace-me-mobile",
            errors,
        )
        self._require_non_placeholder("DATABASE_URL", self.database_url, "", errors)
        self._require_non_placeholder("GOOGLE_CLIENT_ID", self.google_client_id, "", errors)
        self._require_non_placeholder("GOOGLE_CLIENT_SECRET", self.google_client_secret, "", errors)
        self._require_non_placeholder("GOOGLE_REDIRECT_URI", self.google_redirect_uri, "", errors)

        if self.database_url and not self.database_url.startswith("postgresql+psycopg://"):
            errors.append("DATABASE_URL must use the SQLAlchemy psycopg scheme `postgresql+psycopg://`.")
        if "localhost" in self.database_url or "127.0.0.1" in self.database_url:
            errors.append("DATABASE_URL must not point at localhost outside local development.")
        if "sslmode=require" not in self.database_url:
            errors.append("DATABASE_URL must require SSL outside local development, e.g. include `sslmode=require`.")

        redirect = urlparse(self.google_redirect_uri)
        if redirect.scheme != "https" or not redirect.netloc:
            errors.append("GOOGLE_REDIRECT_URI must be an absolute HTTPS URL outside local development.")

        email_mode = self.email_delivery_mode.strip().lower()
        if app_env == "production":
            if email_mode != "resend":
                errors.append("EMAIL_DELIVERY_MODE must be `resend` when APP_ENV=production.")
            self._require_non_placeholder("RESEND_API_KEY", self.resend_api_key, "replace-me", errors)
            self._require_non_placeholder("RESEND_FROM_EMAIL", self.resend_from_email, "replace-me", errors)
        elif email_mode not in {"fake", "resend"}:
            errors.append("EMAIL_DELIVERY_MODE must be either `fake` or `resend`.")

        if errors:
            formatted = "; ".join(errors)
            raise RuntimeError(f"Unsafe runtime configuration: {formatted}")

    @staticmethod
    def _require_non_placeholder(name: str, value: str, placeholder: str, errors: list[str]) -> None:
        normalized = value.strip()
        if not normalized:
            errors.append(f"{name} is required.")
        elif placeholder and normalized == placeholder:
            errors.append(f"{name} must not use placeholder value `{placeholder}`.")


@lru_cache
def get_settings() -> Settings:
    return Settings()
