"""§5 — FastAPI routes via TestClient."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from jose import jwt as jose_jwt

from backend.auth import ALGORITHM
from backend.config import settings


@pytest.fixture
def api_client(fake_db: Any, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(settings, "dashboard_username", "tu", raising=False)
    monkeypatch.setattr(settings, "dashboard_password", "tp", raising=False)
    monkeypatch.setattr(settings, "jwt_secret", "test-secret-key-at-least-32-characters-long-ok", raising=False)

    import backend.main as main

    monkeypatch.setattr(main, "start_scheduler", lambda: None)
    monkeypatch.setattr(main, "shutdown_scheduler", lambda: None)
    monkeypatch.setattr(main, "_warmup_bootstrap_cache", AsyncMock())

    with TestClient(main.app) as c:
        yield c


def _token() -> str:
    from backend.auth import create_access_token

    return create_access_token()


def test_health_no_auth(api_client: TestClient) -> None:
    r = api_client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "cache_ready" in data and isinstance(data["cache_ready"], bool)


def test_health_head_no_auth(api_client: TestClient) -> None:
    """UptimeRobot and similar monitors often probe with HEAD."""
    r = api_client.head("/api/health")
    assert r.status_code == 200
    assert r.content == b""


def test_login_ok_and_jwt_12h(api_client: TestClient) -> None:
    from datetime import datetime, timezone

    r = api_client.post(
        "/api/login",
        data={"username": "tu", "password": "tp"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 200
    tok = r.json()["token"]
    payload = jose_jwt.decode(tok, settings.jwt_secret, algorithms=[ALGORITHM])
    assert "exp" in payload
    ttl = payload["exp"] - int(datetime.now(timezone.utc).timestamp())
    assert 12 * 3600 - 120 <= ttl <= 12 * 3600 + 120


def test_login_wrong_password(api_client: TestClient) -> None:
    r = api_client.post(
        "/api/login",
        data={"username": "tu", "password": "bad"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 401


def test_login_missing_fields(api_client: TestClient) -> None:
    r = api_client.post("/api/login", data={})
    assert r.status_code == 422


def test_status_auth_and_ready(monkeypatch: pytest.MonkeyPatch, api_client: TestClient) -> None:
    from backend import data_manager

    m = data_manager.mgr
    m.cache_state = "READY"
    m.regime = "MEAN_REVERTING"
    m.h_idx = 0.4
    m.h_vix = 0.41
    m.nifty_price = 24_000.0
    m.vix_price = 12.0
    m.last_updated = None

    r = api_client.get("/api/status", headers={"Authorization": f"Bearer {_token()}"})
    assert r.status_code == 200
    j = r.json()
    assert j["nifty_price"] == 24_000.0
    assert j["h_idx"] == 0.4
    assert j["cache_ready"] is True
    assert j["regime"] == "MEAN_REVERTING"


def test_status_warming_shows_unknown_regime_on_api(monkeypatch: pytest.MonkeyPatch, api_client: TestClient) -> None:
    """Implementation maps cache not-READY to regime UNKNOWN in JSON (UI uses PENDING separately)."""
    from backend import data_manager

    m = data_manager.mgr
    m.cache_state = "WARMING_UP"
    m.regime = "MEAN_REVERTING"
    m.h_idx = 0.4
    m.nifty_price = 1.0

    r = api_client.get("/api/status", headers={"Authorization": f"Bearer {_token()}"})
    assert r.status_code == 200
    j = r.json()
    assert j["cache_ready"] is False
    assert j["regime"] == "UNKNOWN"
    assert j["nifty_price"] is None


def test_status_401(api_client: TestClient) -> None:
    assert api_client.get("/api/status").status_code == 401


def test_scan_ready_sort_and_signal(monkeypatch: pytest.MonkeyPatch, api_client: TestClient) -> None:
    import pandas as pd

    from backend import data_manager
    from backend.hars_engine import HARSStrategyEngine

    m = data_manager.mgr
    m.cache_state = "READY"
    m.regime = "MEAN_REVERTING"
    m.active_stocks = [{"symbol": "A"}, {"symbol": "B"}, {"symbol": "C"}]
    m.gap_cache = {"A": {"gap_pct": 1.0}, "B": {"gap_pct": 1.0}, "C": {"gap_pct": 1.0}}
    idx = pd.date_range("2026-01-01", periods=25, freq="5min")
    for sym, sp in [("A", 2.5e6), ("B", 2.0e6), ("C", 1.0e6)]:
        m.rolling_cache[sym] = pd.DataFrame(
            {
                "open": [100.0] * 25,
                "high": [102.0 if sym != "B" else 108.0] * 25,
                "low": [99.0 if sym != "B" else 90.0] * 25,
                "close": [100.0] * 25,
                "volume": [1e6] * 24 + [sp],
            },
            index=idx,
        )
    eng = HARSStrategyEngine()
    pool = m.build_stock_engine_pool()
    sig = eng.get_signals("MEAN_REVERTING", pool)
    assert sig is not None
    m.last_signal = sig

    r = api_client.get("/api/scan", headers={"Authorization": f"Bearer {_token()}"})
    assert r.status_code == 200
    rows = r.json()
    assert rows[0]["result"] == "SIGNAL"
    assert rows[0]["symbol"] == sig["symbol"]
    rest = rows[1:]
    scores_rest = [x["compliance_score"] for x in rest]
    assert scores_rest == sorted(scores_rest, reverse=True)
    hit = [x for x in rows if x["result"] == "SIGNAL"]
    assert len(hit) == 1 and hit[0]["symbol"] == sig["symbol"]

    m.cache_state = "WARMING_UP"
    r2 = api_client.get("/api/scan", headers={"Authorization": f"Bearer {_token()}"})
    for row in r2.json():
        assert row["rvol"] is None
        assert row["result"] == "—"


def test_history_order_and_win_label(api_client: TestClient, fake_db: Any) -> None:
    coll = fake_db["trade_history"]

    async def seed() -> None:
        await coll.insert_many(
            [
                {
                    "date": "2026-01-01",
                    "symbol": "OLD",
                    "direction": "LONG",
                    "entry": 1,
                    "tp": 2,
                    "sl": 0.5,
                    "exit_price": 1,
                    "regime": "X",
                    "outcome": "EOD",
                    "status": "BREAKEVEN",
                },
                {
                    "date": "2026-02-01",
                    "symbol": "NEW",
                    "direction": "LONG",
                    "entry": 1,
                    "tp": 2,
                    "sl": 0.5,
                    "exit_price": 2,
                    "regime": "X",
                    "outcome": "TP_HIT",
                    "status": "WIN",
                },
                {
                    "date": "2026-01-15",
                    "symbol": "MID",
                    "direction": "LONG",
                    "entry": 1,
                    "tp": 2,
                    "sl": 0.5,
                    "exit_price": 0.5,
                    "regime": "X",
                    "outcome": "SL_HIT",
                    "status": "LOSS",
                },
            ]
        )

    asyncio.run(seed())
    r = api_client.get("/api/history", headers={"Authorization": f"Bearer {_token()}"})
    assert r.status_code == 200
    rows = r.json()
    assert [x["date"] for x in rows] == ["2026-02-01", "2026-01-15", "2026-01-01"]
    win = next(x for x in rows if x["status"] == "WIN")
    assert win["outcome"] == "TP_HIT"


def test_admin_refresh_instruments(api_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.main as main

    called = {"n": 0}

    async def stub() -> None:
        called["n"] += 1

    monkeypatch.setattr(main, "instruments_refresh", stub)
    r = api_client.post("/api/admin/refresh-instruments", headers={"Authorization": f"Bearer {_token()}"})
    assert r.status_code == 200
    assert called["n"] == 1


def test_admin_refresh_401(api_client: TestClient) -> None:
    assert api_client.post("/api/admin/refresh-instruments").status_code == 401
