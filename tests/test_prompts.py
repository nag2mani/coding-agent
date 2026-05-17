from pathlib import Path

from hermit.prompts import build_system_prompt
from hermit.tools import ToolRegistry
from hermit.tools.filesystem import make_read_file, make_write_file


def test_empty_registry_renders(tmp_path: Path) -> None:
    reg = ToolRegistry()
    prompt = build_system_prompt(tmp_path, reg)
    assert "hermit" in prompt
    assert "(no tools available)" in prompt


def test_memory_loaded_when_present(tmp_path: Path) -> None:
    (tmp_path / "MEMORY.md").write_text("user is nagmani", encoding="utf-8")
    reg = ToolRegistry()
    reg.register(make_read_file(tmp_path))
    reg.register(make_write_file(tmp_path))
    prompt = build_system_prompt(tmp_path, reg)
    assert "user is nagmani" in prompt
    assert "MEMORY.md" in prompt
    assert "read_file" in prompt
    assert "write_file" in prompt
    assert "[confirm]" in prompt  # write_file is gated


def test_missing_memory_omitted(tmp_path: Path) -> None:
    reg = ToolRegistry()
    prompt = build_system_prompt(tmp_path, reg)
    # behavior guidance mentions MEMORY.md by name, but the loaded-section header
    # should not appear when the file is absent
    assert "persistent user notes" not in prompt


def test_memory_truncation(tmp_path: Path) -> None:
    big = "x" * 5000
    (tmp_path / "MEMORY.md").write_text(big, encoding="utf-8")
    reg = ToolRegistry()
    prompt = build_system_prompt(tmp_path, reg)
    assert "[truncated]" in prompt
