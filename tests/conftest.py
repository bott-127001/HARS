"""Shared fixtures: in-memory Mongo, patched env, DataManager reset."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from typing import Any

import pytest

os.environ.setdefault("JWT_SECRET", "test-secret-key-at-least-32-characters-long-ok")
os.environ.setdefault("DASHBOARD_USERNAME", "testuser")
os.environ.setdefault("DASHBOARD_PASSWORD", "testpass")
os.environ.setdefault("MONGODB_URI", "mongodb://127.0.0.1:27017")
os.environ.setdefault("MONGODB_DB_NAME", "hars_test_db")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def fake_db(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[Any, None]:
    """Wire Motor-compatible fake DB into backend.db for the duration of the test."""
    from tests.fake_mongo import FakeMotorDb

    store = FakeMotorDb()

    async def _connect() -> Any:
        import backend.db as db

        db._client = object()  # noqa: SLF001
        db._db = store  # noqa: SLF001
        return store

    async def _close() -> None:
        import backend.db as db

        db._client = None  # noqa: SLF001
        db._db = None  # noqa: SLF001

    monkeypatch.setattr("backend.db.connect_mongo_with_retries", _connect)
    monkeypatch.setattr("backend.db.close_mongo", _close)

    import backend.db as db

    db._client = object()
    db._db = store  # noqa: SLF001
    yield store
    db._client = None
    db._db = None  # noqa: SLF001


@pytest.fixture
def reset_mgr(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Return fresh DataManager and re-bind scheduler/main singleton if needed."""
    from backend.data_manager import DataManager, mgr

    fresh = DataManager()
    # Copy minimal: patch module-level mgr used everywhere
    keys = [k for k in fresh.__dict__ if not k.startswith("_")]
    for k in keys:
        setattr(mgr, k, getattr(fresh, k))
    return mgr
