"""API-level tests for the web UI surface (/api/chat, /api/reset, /)."""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

# Add backend directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("GEMINI_API_KEY", "test-dummy-key")  # agent import guard

from fastapi.testclient import TestClient  # noqa: E402

from main import APP_VERSION, app  # noqa: E402

client = TestClient(app)


def _session() -> str:
    return f"test-{uuid.uuid4().hex[:8]}"


class TestStaticUi:
    def test_index_served(self) -> None:
        res = client.get("/")
        assert res.status_code == 200
        assert "Namma Transit" in res.text
        assert 'app.js' in res.text

    def test_static_assets_served(self) -> None:
        assert client.get("/static/style.css").status_code == 200
        assert client.get("/static/app.js").status_code == 200


class TestChatApi:
    def test_pre_routed_auto_fare(self) -> None:
        res = client.post(
            "/api/chat", json={"message": "auto fare for 7 km", "session_id": _session()}
        )
        assert res.status_code == 200
        assert "*₹105*" in res.json()["reply"]

    def test_pre_routed_metro(self) -> None:
        res = client.post(
            "/api/chat",
            json={"message": "metro fare from majestic to whitefield", "session_id": _session()},
        )
        assert res.status_code == 200
        assert "₹90" in res.json()["reply"]

    def test_help_menu(self) -> None:
        res = client.post("/api/chat", json={"message": "help", "session_id": _session()})
        assert res.status_code == 200
        assert "Transport Options" in res.json()["reply"]

    def test_empty_message_rejected(self) -> None:
        res = client.post("/api/chat", json={"message": "   ", "session_id": _session()})
        assert res.status_code == 400

    def test_blank_session_rejected(self) -> None:
        res = client.post("/api/chat", json={"message": "hi", "session_id": " "})
        assert res.status_code == 400

    def test_missing_fields_rejected(self) -> None:
        assert client.post("/api/chat", json={"message": "hi"}).status_code == 422


class TestResetApi:
    def test_reset_ok(self) -> None:
        sid = _session()
        client.post("/api/chat", json={"message": "help", "session_id": sid})
        res = client.post("/api/reset", json={"session_id": sid})
        assert res.status_code == 200
        assert res.json() == {"ok": True}


class TestHealth:
    def test_version_and_pool(self) -> None:
        data = client.get("/health").json()
        assert data["version"] == APP_VERSION
        assert len(data["model_pool"]) >= 3
