import tavernpy
from tavernpy import CHATTY, EVENTS, LOG_EVENTS, Event
from tavernpy.events import canonical


def test_catalog_has_23_unique_names():
    names = EVENTS + LOG_EVENTS
    assert len(names) == 23
    assert len(set(names)) == 23


def test_profiling_data_is_real_and_chatty():
    assert "ProfilingData" in EVENTS and "ProfilingData" in CHATTY


def test_log_streams():
    assert LOG_EVENTS == ("TraceLog", "DebugLog", "InfoLog", "WarnLog", "ErrorLog", "FatalLog")


def test_chatty_events_are_catalogued():
    assert set(CHATTY) <= set(EVENTS)


def test_canonical_fixes_case_of_known_names():
    assert canonical("playerkilled") == "PlayerKilled"
    assert canonical(" INFOLOG ") == "InfoLog"


def test_canonical_leaves_unknown_and_lifecycle_names():
    assert canonical("MyModEvent") == "MyModEvent"
    assert canonical("ready") == "ready"
    assert canonical("event") == "event"


def test_event_defaults():
    event = Event("PlayerKilled")
    assert event.data is None and event.source == "feed" and event.server_id is None


def test_public_surface():
    for name in tavernpy.__all__:
        assert hasattr(tavernpy, name), name
    assert tavernpy.DEFAULT_PORT == 1760
