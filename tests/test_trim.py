from hermit.session import Message, trim_messages


def m(role, content, **kw):
    return Message(role=role, content=content, **kw)


def test_under_caps_unchanged() -> None:
    msgs = [m("user", "hi"), m("assistant", "hello")]
    assert trim_messages(msgs, max_messages=10, max_chars=1000) == msgs


def test_trims_to_message_cap() -> None:
    msgs = [m("user", f"u{i}") for i in range(20)]
    out = trim_messages(msgs, max_messages=5, max_chars=10_000)
    assert len(out) == 5
    assert out[-1].content == "u19"  # tail preserved


def test_trims_to_char_cap() -> None:
    msgs = [m("user", "x" * 1000) for _ in range(10)]
    out = trim_messages(msgs, max_messages=100, max_chars=3000)
    assert sum(len(x.content) for x in out) <= 3000 + 1000  # roughly enforced
    # last messages preserved
    assert out[-1] is msgs[-1]


def test_tool_message_never_stranded() -> None:
    msgs = [
        m("user", "u1"),
        m("assistant", "", tool_name="t", tool_calls=[{"name": "t", "arguments": {}}]),
        m("tool", "t-result", tool_name="t"),
        m("assistant", "a1"),
        m("user", "u2"),
        m("assistant", "a2"),
        m("user", "u3"),
        m("assistant", "a3"),
    ]
    out = trim_messages(msgs, max_messages=4, max_chars=10_000)
    # no leading orphan tool message
    assert out[0].role != "tool"
    # tail (last 4) is preserved
    assert out[-4:] == msgs[-4:]


def test_keeps_tail_floor() -> None:
    # even with absurd cap, last 4 are preserved
    msgs = [m("user", f"u{i}") for i in range(10)]
    out = trim_messages(msgs, max_messages=1, max_chars=1)
    assert len(out) == 4
    assert out == msgs[-4:]


def test_preserves_system_head() -> None:
    msgs = [
        m("system", "sys"),
        *[m("user", f"u{i}") for i in range(20)],
    ]
    out = trim_messages(msgs, max_messages=5, max_chars=10_000)
    assert out[0].role == "system"
    assert out[0].content == "sys"


def test_empty_safe() -> None:
    assert trim_messages([]) == []
