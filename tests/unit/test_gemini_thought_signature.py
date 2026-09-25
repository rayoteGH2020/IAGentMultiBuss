"""Tests: Gemini thought_signature se reenvía en el historial del tool loop."""

from __future__ import annotations

import base64

from app.llm.chat_loop import (
    _GEMINI_MODEL_CONTENT_KEY,
    _THOUGHT_SIGNATURE_KEY,
    _to_gemini_contents,
)
from google.genai import types as genai_types


def test_to_gemini_contents_reuses_native_model_content() -> None:
    signature = b"opaque-thought-sig-bytes"
    native = genai_types.Content(
        role="model",
        parts=[
            genai_types.Part(
                function_call=genai_types.FunctionCall(
                    id="call-1",
                    name="list_doc_types",
                    args={},
                ),
                thought_signature=signature,
            )
        ],
    )
    _system, contents = _to_gemini_contents(
        [
            {"role": "user", "content": "¿Qué tipos hay?"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "call-1", "name": "list_doc_types", "arguments": {}}],
                _GEMINI_MODEL_CONTENT_KEY: native,
            },
        ]
    )
    assert len(contents) == 2
    assert contents[1] is native
    assert contents[1].parts is not None
    assert contents[1].parts[0].thought_signature == signature


def test_to_gemini_contents_rehydrates_thought_signature_from_tool_calls() -> None:
    signature = b"sig-from-db"
    encoded = base64.b64encode(signature).decode("ascii")
    _system, contents = _to_gemini_contents(
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-2",
                        "name": "list_doc_types",
                        "arguments": {},
                        _THOUGHT_SIGNATURE_KEY: encoded,
                    }
                ],
            }
        ]
    )
    assert len(contents) == 1
    part = contents[0].parts[0]
    assert part.function_call is not None
    assert part.function_call.name == "list_doc_types"
    assert part.thought_signature == signature


def test_to_gemini_contents_without_signature_still_builds_function_call() -> None:
    _system, contents = _to_gemini_contents(
        [
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": "c", "name": "search_documents", "arguments": {"q": "x"}},
                ],
            }
        ]
    )
    part = contents[0].parts[0]
    assert part.function_call is not None
    assert part.function_call.name == "search_documents"
    assert part.thought_signature is None
