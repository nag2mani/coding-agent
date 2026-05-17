"""fetch_url tool. The one place hermit reaches outside localhost."""

from __future__ import annotations

from typing import Any

import httpx

from hermit.tools import Tool


MAX_BYTES = 200_000
TIMEOUT_SEC = 15.0
_ALLOWED_TYPES = ("text", "json", "xml", "html")


def make_fetch_url(
    allow_network: bool,
    max_bytes: int = MAX_BYTES,
    timeout_sec: float = TIMEOUT_SEC,
) -> Tool:
    async def handler(args: dict[str, Any]) -> str:
        if not allow_network:
            return (
                "error: network disabled "
                "(set HERMIT_ALLOW_NETWORK=1 to enable fetch_url)"
            )

        url = args.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            return "error: 'url' must be an http(s) URL"

        try:
            async with httpx.AsyncClient(
                timeout=timeout_sec, follow_redirects=True
            ) as client:
                r = await client.get(url)
        except httpx.HTTPError as e:
            return f"error: request failed: {e}"

        if r.status_code >= 400:
            return f"error: HTTP {r.status_code}"

        ct = r.headers.get("content-type", "").lower()
        if not any(t in ct for t in _ALLOWED_TYPES):
            return f"error: non-text content-type: {ct or 'unknown'}"

        body = r.content
        truncated = len(body) > max_bytes
        body = body[:max_bytes]
        try:
            text = body.decode("utf-8", errors="replace")
        except UnicodeDecodeError:
            return "error: body not decodable as utf-8"
        return text + ("\n[truncated]" if truncated else "")

    return Tool(
        name="fetch_url",
        description=(
            "HTTP GET a URL and return the text body (max 200KB). "
            "Reference lookup only."
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Absolute http(s) URL."},
            },
            "required": ["url"],
        },
        handler=handler,
    )
