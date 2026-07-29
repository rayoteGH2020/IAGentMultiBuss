"""Prueba mínima de GOOGLE_API_KEY contra Gemini API (billing / conectividad)."""

from __future__ import annotations

import asyncio
import os
import sys

import httpx


async def main() -> None:
    key = os.environ.get("GOOGLE_API_KEY", "").strip()
    if not key:
        sys.stderr.write(
            "GOOGLE_API_KEY no está definida. Usa: infisical run -- uv run python scripts/test_gemini_key.py\n"
        )
        raise SystemExit(1)

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.5-flash:generateContent?key={key}"
    )
    payload = {"contents": [{"parts": [{"text": "Responde solo: ok"}]}]}

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(url, json=payload)

    print("status:", response.status_code)
    print(response.text[:800])


if __name__ == "__main__":
    asyncio.run(main())
