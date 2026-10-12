import re

from .errors import BadPlayerNameException

SAFE_PLAYER = re.compile(r"[A-Za-z0-9 _.\-]{1,32}")
VERB = re.compile(r"[a-z][a-z\-]*")
CONSOLE_TEXT_MAX = 500


def is_safe_player_name(name) -> bool:
    return SAFE_PLAYER.fullmatch("" if name is None else str(name)) is not None


def assert_player_name(name) -> str:
    if not is_safe_player_name(name):
        raise BadPlayerNameException(f"unsafe player name: {name!r}")
    return str(name)


def quoted_player(name) -> str:
    return '"' + assert_player_name(name) + '"'


def player_cmd(verb, name, tail=None) -> str:
    if VERB.fullmatch(str(verb or "")) is None:
        raise ValueError("bad command verb")
    assert_player_name(name)
    out = f'player {verb} "{name}"'
    if tail is not None and str(tail) != "":
        t = str(tail)
        if re.search(r'["\r\n;]', t):
            raise ValueError("bad command arguments")
        out += " " + t
    return out


def _unsafe(ch: str) -> bool:
    code = ord(ch)
    return code < 0x20 or code == 0x7F or code == 0x22 or code == 0x5C


def sanitise_console_text(text, max_len=None) -> dict:
    raw = "" if text is None else str(text)
    limit = max_len if isinstance(max_len, int) and max_len > 0 else CONSOLE_TEXT_MAX
    clean = "".join(c for c in raw if not _unsafe(c))
    truncated = len(clean) > limit
    return {
        "text": clean[:limit] if truncated else clean,
        "removed": len(raw) - len(clean),
        "truncated": truncated,
        "empty": clean.strip() == "",
    }
