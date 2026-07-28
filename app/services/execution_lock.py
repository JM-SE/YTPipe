from __future__ import annotations

from contextlib import contextmanager
import threading
from collections.abc import Iterator

from sqlalchemy import text
from sqlalchemy.orm import Session


EXECUTION_LOCK_KEY = 0x595450495045
_LOCAL_EXECUTION_LOCK = threading.Lock()


class ExecutionLockBusy(RuntimeError):
    pass


@contextmanager
def acquire_execution_lock(session: Session) -> Iterator[None]:
    """Hold the cross-entrypoint execution exclusion for external work.

    PostgreSQL uses a dedicated connection so the session-level advisory lock
    survives commits made by the worker. SQLite tests use a process-local
    fallback because SQLite does not provide PostgreSQL advisory locks.
    """
    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        if not _LOCAL_EXECUTION_LOCK.acquire(blocking=False):
            raise ExecutionLockBusy
        try:
            yield
        finally:
            _LOCAL_EXECUTION_LOCK.release()
        return

    connection = bind.connect()
    try:
        acquired = connection.scalar(
            text("SELECT pg_try_advisory_lock(:lock_key)"),
            {"lock_key": EXECUTION_LOCK_KEY},
        )
        if not acquired:
            raise ExecutionLockBusy
        try:
            yield
        finally:
            connection.execute(
                text("SELECT pg_advisory_unlock(:lock_key)"),
                {"lock_key": EXECUTION_LOCK_KEY},
            )
            connection.commit()
    finally:
        connection.close()
