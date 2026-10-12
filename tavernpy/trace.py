import logging
import os
from datetime import datetime

log = logging.getLogger("tavernpy")

ENV = "TAVERN_TRACE"
MASK = "***"

_shared = {}


def trace_target(option):
    if option is False:
        return None
    if option is None:
        return os.environ.get(ENV) or None
    return option


class Trace:
    def __init__(self, target, label):
        self.target = target
        self.label = label
        self._stream = None
        self._path = None

    @property
    def active(self) -> bool:
        return self._stream is not None

    def open(self):
        if self._stream is not None:
            return
        if hasattr(self.target, "write"):
            self._stream = self.target
            return
        path = os.path.abspath(os.fspath(self.target))
        entry = _shared.get(path)
        if entry is None:
            entry = [open(path, "a", encoding="utf-8", buffering=1), 0]
            _shared[path] = entry
            log.info("Tracing console frames to %s", path)
        entry[1] += 1
        self._stream = entry[0]
        self._path = path

    def close(self):
        if self._stream is None:
            return
        self._stream = None
        path, self._path = self._path, None
        entry = _shared.get(path) if path else None
        if entry is None:
            return
        entry[1] -= 1
        if entry[1] <= 0:
            _shared.pop(path, None)
            entry[0].close()

    def write(self, direction, text, secret=None):
        if self._stream is None:
            return
        text = str(text)
        if secret:
            text = text.replace(secret, MASK)
        text = text.replace("\r", "\\r").replace("\n", "\\n")
        stamp = datetime.now().isoformat(timespec="milliseconds")
        try:
            self._stream.write(f"{stamp} {self.label} {direction} {text}\n")
            self._stream.flush()
        except (OSError, ValueError):
            pass
