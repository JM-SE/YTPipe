from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.settings import Settings
from app.core.settings import get_settings
from app.db import base  # noqa: F401

settings = get_settings()


def get_engine_kwargs(settings: Settings) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "future": True,
        "pool_pre_ping": True,
    }
    if settings.app_env.strip().lower() != "local":
        kwargs["pool_recycle"] = 300
    return kwargs


engine = create_engine(settings.database_url, **get_engine_kwargs(settings))
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
