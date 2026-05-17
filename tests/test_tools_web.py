import asyncio

import httpx
import respx

from hermit.tools.web import make_fetch_url


def run(coro):
    return asyncio.run(coro)


def test_disabled_by_default() -> None:
    tool = make_fetch_url(allow_network=False)
    out = run(tool.handler({"url": "https://example.com"}))
    assert out.startswith("error: network disabled")


def test_bad_scheme() -> None:
    tool = make_fetch_url(allow_network=True)
    out = run(tool.handler({"url": "ftp://example.com"}))
    assert "must be an http(s) URL" in out


def test_happy_path() -> None:
    tool = make_fetch_url(allow_network=True)
    with respx.mock:
        respx.get("https://example.com").mock(
            return_value=httpx.Response(
                200, text="<h1>Example Domain</h1>", headers={"content-type": "text/html"}
            )
        )
        out = run(tool.handler({"url": "https://example.com"}))
    assert "Example Domain" in out
    assert "[truncated]" not in out


def test_non_text_rejected() -> None:
    tool = make_fetch_url(allow_network=True)
    with respx.mock:
        respx.get("https://example.com/img.png").mock(
            return_value=httpx.Response(200, content=b"\x89PNG", headers={"content-type": "image/png"})
        )
        out = run(tool.handler({"url": "https://example.com/img.png"}))
    assert "non-text content-type" in out


def test_truncation() -> None:
    big = "x" * 1000
    tool = make_fetch_url(allow_network=True, max_bytes=200)
    with respx.mock:
        respx.get("https://example.com").mock(
            return_value=httpx.Response(200, text=big, headers={"content-type": "text/plain"})
        )
        out = run(tool.handler({"url": "https://example.com"}))
    assert "[truncated]" in out
    assert len(out) < 1000


def test_http_error() -> None:
    tool = make_fetch_url(allow_network=True)
    with respx.mock:
        respx.get("https://example.com").mock(return_value=httpx.Response(404, text="nope"))
        out = run(tool.handler({"url": "https://example.com"}))
    assert "HTTP 404" in out
