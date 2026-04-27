import tomllib
import zipfile
from pathlib import Path

import pytest

from app.core.settings import Settings


def test_staging_runtime_config_accepts_fake_email_with_safe_secrets() -> None:
    settings = Settings(
        APP_ENV="staging",
        APP_SECRET_KEY="staging-secret",
        INTERNAL_API_BEARER_TOKEN="staging-internal-token",
        DATABASE_URL="postgresql+psycopg://user:password@example.neon.tech/ytpipe?sslmode=require",
        GOOGLE_CLIENT_ID="google-client-id",
        GOOGLE_CLIENT_SECRET="google-client-secret",
        GOOGLE_REDIRECT_URI="https://ytpipe-staging.onrender.com/auth/callback",
        EMAIL_DELIVERY_MODE="fake",
    )

    settings.validate_runtime_config()


def test_non_local_runtime_config_rejects_placeholder_secrets() -> None:
    settings = Settings(
        APP_ENV="staging",
        APP_SECRET_KEY="replace-me",
        INTERNAL_API_BEARER_TOKEN="replace-me-internal",
        DATABASE_URL="postgresql+psycopg://user:password@example.neon.tech/ytpipe?sslmode=require",
        GOOGLE_CLIENT_ID="google-client-id",
        GOOGLE_CLIENT_SECRET="google-client-secret",
        GOOGLE_REDIRECT_URI="https://ytpipe-staging.onrender.com/auth/callback",
        EMAIL_DELIVERY_MODE="fake",
    )

    with pytest.raises(RuntimeError) as exc_info:
        settings.validate_runtime_config()

    message = str(exc_info.value)
    assert "APP_SECRET_KEY must not use placeholder" in message
    assert "INTERNAL_API_BEARER_TOKEN must not use placeholder" in message


def test_non_local_runtime_config_requires_https_google_redirect_uri() -> None:
    settings = Settings(
        APP_ENV="staging",
        APP_SECRET_KEY="staging-secret",
        INTERNAL_API_BEARER_TOKEN="staging-internal-token",
        DATABASE_URL="postgresql+psycopg://user:password@example.neon.tech/ytpipe?sslmode=require",
        GOOGLE_CLIENT_ID="google-client-id",
        GOOGLE_CLIENT_SECRET="google-client-secret",
        GOOGLE_REDIRECT_URI="http://127.0.0.1:8000/auth/callback",
        EMAIL_DELIVERY_MODE="fake",
    )

    with pytest.raises(RuntimeError, match="GOOGLE_REDIRECT_URI must be an absolute HTTPS URL"):
        settings.validate_runtime_config()


def test_non_local_runtime_config_rejects_unknown_app_env() -> None:
    settings = Settings(
        APP_ENV="prod",
        APP_SECRET_KEY="production-secret",
        INTERNAL_API_BEARER_TOKEN="production-internal-token",
        DATABASE_URL="postgresql+psycopg://user:password@example.neon.tech/ytpipe?sslmode=require",
        GOOGLE_CLIENT_ID="google-client-id",
        GOOGLE_CLIENT_SECRET="google-client-secret",
        GOOGLE_REDIRECT_URI="https://ytpipe.onrender.com/auth/callback",
        EMAIL_DELIVERY_MODE="resend",
        RESEND_API_KEY="resend-key",
        RESEND_FROM_EMAIL="noreply@example.com",
    )

    with pytest.raises(RuntimeError, match="APP_ENV must be one of"):
        settings.validate_runtime_config()


def test_non_local_runtime_config_requires_database_ssl() -> None:
    settings = Settings(
        APP_ENV="staging",
        APP_SECRET_KEY="staging-secret",
        INTERNAL_API_BEARER_TOKEN="staging-internal-token",
        DATABASE_URL="postgresql+psycopg://user:password@example.neon.tech/ytpipe",
        GOOGLE_CLIENT_ID="google-client-id",
        GOOGLE_CLIENT_SECRET="google-client-secret",
        GOOGLE_REDIRECT_URI="https://ytpipe-staging.onrender.com/auth/callback",
        EMAIL_DELIVERY_MODE="fake",
    )

    with pytest.raises(RuntimeError, match="DATABASE_URL must require SSL"):
        settings.validate_runtime_config()


def test_production_runtime_config_requires_resend_delivery() -> None:
    settings = Settings(
        APP_ENV="production",
        APP_SECRET_KEY="production-secret",
        INTERNAL_API_BEARER_TOKEN="production-internal-token",
        DATABASE_URL="postgresql+psycopg://user:password@example.neon.tech/ytpipe?sslmode=require",
        GOOGLE_CLIENT_ID="google-client-id",
        GOOGLE_CLIENT_SECRET="google-client-secret",
        GOOGLE_REDIRECT_URI="https://ytpipe.onrender.com/auth/callback",
        EMAIL_DELIVERY_MODE="fake",
    )

    with pytest.raises(RuntimeError) as exc_info:
        settings.validate_runtime_config()

    message = str(exc_info.value)
    assert "EMAIL_DELIVERY_MODE must be `resend`" in message
    assert "RESEND_API_KEY is required" in message
    assert "RESEND_FROM_EMAIL is required" in message


def test_production_runtime_config_rejects_resend_placeholders() -> None:
    settings = Settings(
        APP_ENV="production",
        APP_SECRET_KEY="production-secret",
        INTERNAL_API_BEARER_TOKEN="production-internal-token",
        DATABASE_URL="postgresql+psycopg://user:password@example.neon.tech/ytpipe?sslmode=require",
        GOOGLE_CLIENT_ID="google-client-id",
        GOOGLE_CLIENT_SECRET="google-client-secret",
        GOOGLE_REDIRECT_URI="https://ytpipe.onrender.com/auth/callback",
        EMAIL_DELIVERY_MODE="resend",
        RESEND_API_KEY="replace-me",
        RESEND_FROM_EMAIL="replace-me",
    )

    with pytest.raises(RuntimeError) as exc_info:
        settings.validate_runtime_config()

    message = str(exc_info.value)
    assert "RESEND_API_KEY must not use placeholder" in message
    assert "RESEND_FROM_EMAIL must not use placeholder" in message


def test_render_blueprint_declares_staging_service_and_secret_env_vars() -> None:
    render_yaml = Path("render.yaml").read_text(encoding="utf-8")

    assert "name: ytpipe-staging" in render_yaml
    assert "runtime: python" in render_yaml
    assert "plan: free" in render_yaml
    assert "buildCommand: pip install -e ." in render_yaml
    assert "startCommand: uvicorn app.main:app --host 0.0.0.0 --port $PORT" in render_yaml
    assert "healthCheckPath: /health" in render_yaml
    assert "key: APP_ENV" in render_yaml
    assert "value: staging" in render_yaml
    assert "key: EMAIL_DELIVERY_MODE" in render_yaml
    assert "value: fake" in render_yaml
    assert "sync: false" in render_yaml


def test_packaging_config_includes_runtime_and_migration_assets() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert "app*" in pyproject["tool"]["setuptools"]["packages"]["find"]["include"]
    data_files = pyproject["tool"]["setuptools"]["data-files"]
    assert "alembic.ini" in data_files["."]
    assert "alembic/env.py" in data_files["alembic"]
    assert "alembic/versions/20260422_0001_foundations_baseline.py" in data_files["alembic/versions"]
    assert "alembic/versions/20260423_0002_single_user_guard.py" in data_files["alembic/versions"]
    assert "alembic/versions/20260423_0003_user_channel_import_contract.py" in data_files["alembic/versions"]


def test_built_wheel_contains_app_and_migration_assets() -> None:
    wheels = sorted(Path("dist").glob("ytpipe-*.whl"))
    if not wheels:
        pytest.skip("Build artifact not found; run `python -m build` before this packaging smoke check.")

    with zipfile.ZipFile(wheels[-1]) as wheel:
        names = set(wheel.namelist())

    assert "app/main.py" in names
    assert "app/api/routes/status.py" in names
    assert any(name.endswith(".data/data/alembic.ini") for name in names)
    assert any(name.endswith(".data/data/alembic/env.py") for name in names)
    assert any(name.endswith(".data/data/alembic/versions/20260422_0001_foundations_baseline.py") for name in names)
    assert any(name.endswith(".data/data/alembic/versions/20260423_0002_single_user_guard.py") for name in names)
    assert any(name.endswith(".data/data/alembic/versions/20260423_0003_user_channel_import_contract.py") for name in names)
