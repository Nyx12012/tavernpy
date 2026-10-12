import io
import json
import re

import pytest

from tavernpy import Console, ConsolePermissionsDenied, Tavern

from .conftest import FAST, wait_for
from .mock_console import MockConsole

LINE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3} (\S+) ([<>*]) (.*)$")


def console_for(port, token_file, **kw):
    return Console("127.0.0.1", port, token_file=str(token_file), **{**FAST, **kw})


def parse(path):
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = LINE.match(line)
        assert match, line
        out.append(match.groups())
    return out


async def test_trace_writes_every_frame_with_the_token_masked(mock, token_file, tmp_path):
    path = tmp_path / "trace.txt"
    console = console_for(mock.port, token_file, trace=str(path))
    await console.connect(timeout=5)
    await console.subscribe("InfoLog")
    await mock.push("InfoLog", "hello")
    reply = await console.command("player list")
    await console.aclose()
    text = path.read_text(encoding="utf-8")
    assert "good.token.sig" not in text
    lines = parse(path)
    label = f"127.0.0.1:{mock.port}"
    assert {line[0] for line in lines} == {label}
    assert lines[0] == (label, "*", f"open ws://127.0.0.1:{mock.port}")
    assert lines[1] == (label, ">", "***")
    sent = [json.loads(t) for _, d, t in lines if d == ">" and t != "***"]
    assert {"id": reply.raw["commandId"], "content": "player list"} in sent
    got = [json.loads(t) for _, d, t in lines if d == "<"]
    assert any(f.get("type") == "CommandResult" and f.get("commandId") == reply.raw["commandId"] for f in got)
    assert any(f.get("type") == "Subscription" and f.get("data") == "hello" for f in got)
    assert lines[-1] == (label, "*", "closed")


async def test_token_echoed_by_the_server_is_masked(tmp_path):
    mock = await MockConsole(refusal="invalid").start()
    bad = tmp_path / "bad.txt"
    bad.write_text("wrong.token.value", encoding="utf-8")
    path = tmp_path / "trace.txt"
    try:
        with pytest.raises(ConsolePermissionsDenied):
            await console_for(mock.port, bad, trace=str(path)).connect(timeout=5)
    finally:
        await mock.stop()
    text = path.read_text(encoding="utf-8")
    assert "wrong.token.value" not in text
    assert "Invalid Token ***" in text


async def test_env_var_turns_the_trace_on(mock, token_file, tmp_path, monkeypatch):
    path = tmp_path / "env-trace.txt"
    monkeypatch.setenv("TAVERN_TRACE", str(path))
    async with console_for(mock.port, token_file) as console:
        await console.command("player list")
    assert any(d == ">" and "player list" in t for _, d, t in parse(path))


async def test_trace_false_beats_the_env_var(mock, token_file, tmp_path, monkeypatch):
    path = tmp_path / "env-trace.txt"
    monkeypatch.setenv("TAVERN_TRACE", str(path))
    async with console_for(mock.port, token_file, trace=False) as console:
        await console.command("player list")
    assert not path.exists()


async def test_no_trace_by_default(mock, token_file, monkeypatch):
    monkeypatch.delenv("TAVERN_TRACE", raising=False)
    console = console_for(mock.port, token_file)
    assert console._trace is None
    await console.connect(timeout=5)
    await console.aclose()


async def test_stream_target_is_written_and_left_open(mock, token_file):
    stream = io.StringIO()
    async with console_for(mock.port, token_file, trace=stream) as console:
        await console.command("player list")
    assert not stream.closed
    assert "player list" in stream.getvalue()


async def test_consoles_share_one_trace_file(mock, token_file, tmp_path):
    path = tmp_path / "trace.txt"
    tavern = Tavern(**FAST, trace=str(path))
    await tavern.create_console("a", "127.0.0.1", mock.port, token_file=str(token_file), ensure_open=True)
    await tavern.create_console("b", "127.0.0.1", mock.port, token_file=str(token_file), ensure_open=True)
    await tavern.remove_console("a")
    await tavern.send_command_console("b", "player list")
    await tavern.aclose()
    lines = parse(path)
    assert {line[0] for line in lines} == {"a", "b"}
    assert any(label == "b" and d == ">" and "player list" in t for label, d, t in lines)


async def test_trace_survives_a_reconnect(mock, token_file, tmp_path):
    path = tmp_path / "trace.txt"
    console = console_for(mock.port, token_file, trace=str(path))
    ready = []
    console.on("ready", ready.append)
    await console.connect(timeout=5)
    await mock.drop_all()
    await wait_for(lambda: len(ready) >= 2 and console.connected)
    await console.command("player list")
    await console.aclose()
    opens = [t for _, d, t in parse(path) if d == "*" and t.startswith("open")]
    assert len(opens) == 2
    assert any(d == ">" and "player list" in t for _, d, t in parse(path))


async def test_unwritable_trace_path_is_logged_not_fatal(mock, token_file, tmp_path, caplog):
    console = console_for(mock.port, token_file, trace=str(tmp_path / "missing" / "trace.txt"))
    await console.connect(timeout=5)
    assert (await console.command("player list")).ok
    await console.aclose()
    assert "Cannot trace" in caplog.text
