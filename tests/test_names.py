import pytest

from tavernpy import BadPlayerNameException, is_safe_player_name, player_cmd, quoted_player, sanitise_console_text


@pytest.mark.parametrize("name", ["Alice", "Bob Smith", "a_b.c-d", "x" * 32, "12345"])
def test_safe_names(name):
    assert is_safe_player_name(name)
    assert quoted_player(name) == f'"{name}"'


@pytest.mark.parametrize("name", ["", None, "x" * 33, 'Bob"', "a;b", "line\nbreak", "back\\slash", "tab\t"])
def test_unsafe_names(name):
    assert not is_safe_player_name(name)
    with pytest.raises(BadPlayerNameException):
        quoted_player(name)


def test_bad_name_is_also_a_value_error():
    with pytest.raises(ValueError):
        quoted_player('"')


def test_player_cmd():
    assert player_cmd("kick", "Bob Smith") == 'player kick "Bob Smith"'
    assert player_cmd("set-home", "Alice", "1,2,3") == 'player set-home "Alice" 1,2,3'
    assert player_cmd("kill", "Alice", "") == 'player kill "Alice"'


@pytest.mark.parametrize("verb", ["", "Kick", "kick;", "1kill", None])
def test_player_cmd_bad_verb(verb):
    with pytest.raises(ValueError, match="verb"):
        player_cmd(verb, "Alice")


@pytest.mark.parametrize("tail", ['"x"', "a\nb", "a;b", "a\rb"])
def test_player_cmd_bad_tail(tail):
    with pytest.raises(ValueError, match="arguments"):
        player_cmd("message", "Alice", tail)


def test_player_cmd_bad_name():
    with pytest.raises(BadPlayerNameException):
        player_cmd("kick", "a;b")


def test_sanitise_strips_quotes_backslashes_and_controls():
    out = sanitise_console_text('He said "hi"\\\n\x7fok')
    assert out == {"text": "He said hiok", "removed": 5, "truncated": False, "empty": False}


def test_sanitise_truncates():
    out = sanitise_console_text("a" * 600)
    assert len(out["text"]) == 500 and out["truncated"]
    assert len(sanitise_console_text("abcdef", 3)["text"]) == 3


def test_sanitise_empty():
    assert sanitise_console_text(None)["empty"]
    assert sanitise_console_text(' "\n ')["empty"]
