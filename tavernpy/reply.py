import json
import re
from dataclasses import dataclass, field
from typing import Any

from .errors import CommandFailedException

RESPONSE_TYPES = ["SystemMessage", "Subscription", "CommandResult"]
EXCEPTION_TEXT = re.compile(r"^[A-Za-z_][\w.]*Exception: ")


def response_type(value) -> str:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return RESPONSE_TYPES[value] if 0 <= value < len(RESPONSE_TYPES) else str(value)
    return "" if value is None else str(value)


def exception_text(ex):
    if ex is None:
        return None
    if isinstance(ex, str):
        return None if ex.strip() == "" else ex
    if isinstance(ex, dict):
        message = ex.get("Message") or ex.get("message")
        class_name = ex.get("ClassName") or ex.get("className")
        if message and class_name:
            return f"{class_name}: {message}"
        return message or class_name or json.dumps(ex)
    return str(ex)


def to_command_result(frame) -> dict:
    d = frame.get("data") if isinstance(frame, dict) else None
    if d is None:
        return {"type": "CommandResult", "data": {"ResultString": ""}}
    if not isinstance(d, dict):
        return {"type": "CommandResult", "data": {"ResultString": d if isinstance(d, str) else json.dumps(d)}}
    ex = exception_text(d.get("Exception"))
    if ex:
        return {"type": "CommandResult", "data": {"Exception": ex}}
    data = {}
    if d.get("ResultString") is not None:
        rs = d["ResultString"]
        data["ResultString"] = rs if isinstance(rs, str) else json.dumps(rs)
    if "Result" in d:
        data["Result"] = d["Result"]
    rs = data.get("ResultString")
    if isinstance(rs, str) and data.get("Result") is None and EXCEPTION_TEXT.match(rs):
        return {"type": "CommandResult", "data": {"Exception": rs}}
    if isinstance(rs, str) and data.get("Result") is not None and rs.startswith("System."):
        data["ResultString"] = ""
    return {"type": "CommandResult", "data": data}


def _blank(value) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def subscription_outcome(response) -> str:
    data = response.get("data") if isinstance(response, dict) else None
    data = data if isinstance(data, dict) else {}
    if data.get("Exception"):
        return "error: " + str(data["Exception"])
    r = data.get("Result")
    if _blank(r):
        r = data.get("ResultString")
    if r == 0 or r == "Success":
        return "Success"
    if r == 1 or r == "Unchanged":
        return "Unchanged"
    if r == 2 or r == "NotFound":
        return "NotFound"
    if isinstance(r, str) and r.startswith("Subscribed to "):
        return "Success"
    if isinstance(r, str) and r.startswith("Unsubscribed from "):
        return "Success"
    return "null" if r is None else str(r)


def roster_from_result(response):
    data = response.get("data") if isinstance(response, dict) else None
    r = data.get("Result") if isinstance(data, dict) else None
    if not isinstance(r, list):
        return None
    out = []
    for p in r:
        if not isinstance(p, dict):
            continue
        username = p.get("username") if p.get("username") is not None else p.get("Username")
        pid = p.get("id") if p.get("id") is not None else p.get("Identifier")
        if username is None or str(username).strip() == "":
            continue
        out.append({"username": username, "id": pid})
    return out


@dataclass(frozen=True)
class Player:
    username: str
    id: Any = None


@dataclass(frozen=True)
class Reply:
    command: str
    result: Any = None
    result_string: str | None = None
    error: str | None = None
    has_result: bool = field(default=False, repr=False)
    raw: dict = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_frame(cls, command, frame):
        data = to_command_result(frame)["data"]
        error = data.get("Exception")
        return cls(command=command, result=data.get("Result"), result_string=data.get("ResultString"),
                   error=str(error) if error else None, has_result="Result" in data,
                   raw=frame if isinstance(frame, dict) else {})

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def value(self):
        if not _blank(self.result):
            return self.result
        if not _blank(self.result_string):
            return self.result_string
        if self.has_result:
            return self.result
        return self.result_string

    @property
    def text(self) -> str:
        value = self.value
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value)
        except (TypeError, ValueError):
            return str(value)

    def json(self):
        value = self.value
        if value is None:
            return None
        if isinstance(value, (dict, list)):
            return value
        if not isinstance(value, str):
            return None
        text = value.strip()
        if text == "":
            return None
        try:
            parsed = json.loads(text)
        except ValueError:
            return None
        if isinstance(parsed, list) and len(parsed) == 1 and isinstance(parsed[0], dict):
            return parsed[0]
        return parsed

    def check(self):
        if self.error is not None:
            raise CommandFailedException(f"{self.command}: {self.error}", self)
        return self

    def roster(self):
        players = roster_from_result({"data": {"Result": self.result}})
        if players is None:
            return None
        return [Player(p["username"], p["id"]) for p in players]
