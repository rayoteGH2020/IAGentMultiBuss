"""Tests unitarios de modelos de chat (sin BD)."""

from app.models import ChatMessageRole


def test_chat_message_role_values() -> None:
    assert ChatMessageRole.user == "user"
    assert ChatMessageRole.assistant == "assistant"
    assert ChatMessageRole.tool == "tool"
