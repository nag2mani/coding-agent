import asyncio
from pathlib import Path

from hermit.tools.filesystem import (
    WorkspaceEscape,
    make_read_file,
    make_write_file,
    resolve_in_workspace,
)


def run(coro):
    return asyncio.run(coro)


def test_resolve_inside_ok(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    p = resolve_in_workspace(tmp_path, "sub/x.md")
    assert str(p).startswith(str(tmp_path.resolve()))


def test_resolve_dotdot_blocked(tmp_path: Path) -> None:
    try:
        resolve_in_workspace(tmp_path, "../etc/passwd")
    except WorkspaceEscape:
        return
    raise AssertionError("dotdot should be blocked")


def test_resolve_absolute_blocked(tmp_path: Path) -> None:
    try:
        resolve_in_workspace(tmp_path, "/etc/passwd")
    except WorkspaceEscape:
        return
    raise AssertionError("absolute path should be blocked")


def test_read_missing_file_returns_error(tmp_path: Path) -> None:
    tool = make_read_file(tmp_path)
    out = run(tool.handler({"path": "nope.md"}))
    assert out.startswith("error: not found")


def test_read_writes_roundtrip(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("hello", encoding="utf-8")
    tool = make_read_file(tmp_path)
    out = run(tool.handler({"path": "a.md"}))
    assert out == "hello"


def test_write_creates_and_overwrites(tmp_path: Path) -> None:
    tool = make_write_file(tmp_path)
    run(tool.handler({"path": "new/x.md", "content": "first"}))
    assert (tmp_path / "new" / "x.md").read_text() == "first"
    run(tool.handler({"path": "new/x.md", "content": "second"}))
    assert (tmp_path / "new" / "x.md").read_text() == "second"


def test_write_workspace_escape_blocked(tmp_path: Path) -> None:
    tool = make_write_file(tmp_path)
    out = run(tool.handler({"path": "../escape.txt", "content": "x"}))
    assert "escapes workspace" in out


def test_read_truncation_marker(tmp_path: Path) -> None:
    big = "x" * 200
    (tmp_path / "big.txt").write_text(big, encoding="utf-8")
    tool = make_read_file(tmp_path)
    out = run(tool.handler({"path": "big.txt", "max_bytes": 50}))
    assert "[truncated]" in out
    assert out.startswith("x" * 50)
