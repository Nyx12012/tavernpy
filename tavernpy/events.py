from dataclasses import dataclass
from typing import Any

EVENTS = (
    "PlayerStateChanged",
    "PlayerJoined",
    "PlayerLeft",
    "PlayerKilled",
    "PlayerMovedChunk",
    "ObjectKilled",
    "InventoryChanged",
    "CommandExecuted",
    "ServerSettingsChanged",
    "PopulationModified",
    "AtmBalanceChanged",
    "TradeDeckUsed",
    "TrialStarted",
    "TrialFinished",
    "SocialTabletPlayerBanned",
    "SocialTabletPlayerReported",
    "ProfilingData",
)

LOG_EVENTS = ("TraceLog", "DebugLog", "InfoLog", "WarnLog", "ErrorLog", "FatalLog")

CHATTY = ("InventoryChanged", "PlayerMovedChunk", "ProfilingData")

JOIN_LEAVE = ("PlayerJoined", "PlayerLeft")

LIFECYCLE = ("ready", "close", "rejected", "event")

_BY_LOWER = {name.lower(): name for name in EVENTS + LOG_EVENTS}


def canonical(name) -> str:
    text = str(name).strip()
    if text in LIFECYCLE:
        return text
    return _BY_LOWER.get(text.lower(), text)


@dataclass(frozen=True)
class Event:
    type: str
    data: Any = None
    timestamp: str | None = None
    source: str = "feed"
    server_id: Any = None
