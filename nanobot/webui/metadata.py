"""Shared WebUI metadata keys."""

WEBUI_TURN_METADATA_KEY = "webui_turn_id"
WEBUI_SYSTEM_COMMAND_TURN_PREFIX = "webui-system:"
WEBSOCKET_TURN_OWNER_METADATA_KEY = "_websocket_turn_owner"
WEBUI_MESSAGE_SOURCE_METADATA_KEY = "_webui_message_source"

# Session metadata key recording the Visla user id that created/owns the
# WebUI chat (first writer wins; surfaced by the sidebar session list).
SESSION_OWNER_METADATA_KEY = "visla_user_id"
