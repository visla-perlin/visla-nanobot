"""WebUI session owner recording: visla_user_id on sessions and the list API."""

from __future__ import annotations

from pathlib import Path

import pytest

import nanobot.webui.session_list_index as session_list_index
from nanobot.session.manager import SessionManager
from nanobot.webui.inbound_commands import record_session_owner
from nanobot.webui.metadata import SESSION_OWNER_METADATA_KEY
from nanobot.webui.session_list_index import list_webui_sessions


@pytest.fixture(autouse=True)
def _isolate_webui_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    webui_dir = tmp_path / "webui"
    webui_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(session_list_index, "get_webui_dir", lambda: webui_dir)


def test_record_session_owner_persists_visla_user_id(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)

    record_session_owner(manager, "chat-1", "26")

    payload = manager.read_session_metadata("websocket:chat-1")
    assert payload is not None
    assert payload["metadata"][SESSION_OWNER_METADATA_KEY] == "26"


def test_record_session_owner_first_writer_wins(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)

    record_session_owner(manager, "chat-1", "26")
    record_session_owner(manager, "chat-1", "42")

    payload = manager.read_session_metadata("websocket:chat-1")
    assert payload is not None
    assert payload["metadata"][SESSION_OWNER_METADATA_KEY] == "26"


def test_record_session_owner_appends_without_touching_history(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    session = manager.get_or_create("websocket:chat-1")
    session.add_message("user", "existing history")
    manager.save(session)

    record_session_owner(manager, "chat-1", "26")

    restored = manager.get_or_create("websocket:chat-1")
    assert [m.get("content") for m in restored.messages] == ["existing history"]


def test_session_list_exports_user_id(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    record_session_owner(manager, "owned", "26")
    manager.get_or_create("websocket:anonymous").add_message("user", "hi")
    manager.save(manager.get_or_create("websocket:anonymous"))

    rows = {row["key"]: row for row in list_webui_sessions(manager)}

    assert rows["websocket:owned"]["user_id"] == "26"
    assert rows["websocket:anonymous"]["user_id"] is None
