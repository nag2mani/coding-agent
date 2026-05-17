from pathlib import Path

from hermit.session import Message, Session, SessionStore


def test_roundtrip(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    s = Session.new(model="gemma4:e4b")
    s.append(Message(role="user", content="hello"))
    s.append(Message(role="assistant", content="hi back"))
    store.save(s)

    loaded = store.load(s.id)
    assert loaded is not None
    assert loaded.id == s.id
    assert len(loaded.messages) == 2
    assert loaded.messages[0].role == "user"
    assert loaded.messages[0].content == "hello"
    assert loaded.title == "hello"


def test_title_from_first_user(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    s = Session.new(model="gemma4:e4b")
    s.append(Message(role="user", content="summarise the design doc"))
    store.save(s)
    assert store.load(s.id).title == "summarise the design doc"


def test_index_and_delete(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    s1 = Session.new(model="m")
    s1.append(Message(role="user", content="first"))
    s2 = Session.new(model="m")
    s2.append(Message(role="user", content="second"))
    store.save(s1)
    store.save(s2)

    rows = store.list()
    assert {r["id"] for r in rows} == {s1.id, s2.id}

    assert store.delete(s1.id) is True
    rows = store.list()
    assert {r["id"] for r in rows} == {s2.id}
    assert store.load(s1.id) is None


def test_atomic_write_leaves_no_tmp(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    s = Session.new(model="m")
    s.append(Message(role="user", content="hi"))
    store.save(s)
    tmps = list((tmp_path / "sessions").glob("*.tmp"))
    assert tmps == []


def test_tool_message_roundtrip(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    s = Session.new(model="m")
    s.append(
        Message(
            role="assistant",
            content="",
            tool_name="read_file",
            tool_input={"path": "x.md"},
        )
    )
    s.append(
        Message(
            role="tool",
            content="file contents",
            tool_name="read_file",
            tool_call_id="call_0",
        )
    )
    store.save(s)
    loaded = store.load(s.id)
    assert loaded.messages[0].tool_name == "read_file"
    assert loaded.messages[0].tool_input == {"path": "x.md"}
    assert loaded.messages[1].role == "tool"
    assert loaded.messages[1].content == "file contents"
