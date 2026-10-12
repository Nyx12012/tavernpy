import pytest

from tavernpy import CommandFailedException, Player, Reply
from tavernpy.reply import response_type, subscription_outcome, to_command_result


def frame(**data):
    return {"type": "CommandResult", "data": data, "commandId": 1}


def test_result_and_result_string_stay_apart():
    reply = Reply.from_frame("x", frame(Result={"a": 1}, ResultString="human line"))
    assert reply.result == {"a": 1}
    assert reply.result_string == "human line"
    assert reply.ok and reply.error is None
    assert reply.value == {"a": 1}


def test_bare_type_name_next_to_a_result_is_blanked():
    reply = Reply.from_frame("player list", frame(Result=[], ResultString="System.Linq.Enumerable+Iterator"))
    assert reply.result_string == ""
    assert reply.value == []


def test_type_name_without_a_result_is_kept():
    reply = Reply.from_frame("x", frame(Result=None, ResultString="System.Void"))
    assert reply.result_string == "System.Void"


def test_structured_exception_is_a_failure():
    reply = Reply.from_frame("x", frame(Result=None, ResultString=None,
                                        Exception={"ClassName": "System.Exception", "Message": "Command not found"}))
    assert not reply.ok
    assert reply.error == "System.Exception: Command not found"
    assert reply.result is None


def test_exception_message_only():
    assert Reply.from_frame("x", frame(Exception={"Message": "boom"})).error == "boom"


def test_exception_shaped_result_string_is_a_failure():
    reply = Reply.from_frame("x", frame(Result=None, ResultString="System.ArgumentException: bad arg"))
    assert not reply.ok
    assert reply.error == "System.ArgumentException: bad arg"


def test_exception_shaped_text_with_a_result_is_not_a_failure():
    reply = Reply.from_frame("x", frame(Result="value", ResultString="System.ArgumentException: bad arg"))
    assert reply.ok


def test_blank_exception_is_not_a_failure():
    assert Reply.from_frame("x", frame(Result=1, ResultString="1", Exception="  ")).ok


def test_check_raises_on_failure_and_returns_self_on_success():
    good = Reply.from_frame("x", frame(Result=1))
    assert good.check() is good
    bad = Reply.from_frame("kick", frame(Exception={"Message": "no such player"}))
    with pytest.raises(CommandFailedException, match="kick: no such player") as info:
        bad.check()
    assert info.value.reply is bad


def test_value_prefers_result_then_result_string():
    assert Reply.from_frame("x", frame(Result=None, ResultString="text")).value == "text"
    assert Reply.from_frame("x", frame(Result="  ", ResultString="text")).value == "text"
    assert Reply.from_frame("x", frame(Result=0, ResultString="0")).value == 0
    assert Reply.from_frame("x", frame()).value is None


def test_text():
    assert Reply.from_frame("x", frame(Result={"b": 2})).text == '{"b": 2}'
    assert Reply.from_frame("x", frame(ResultString="hello")).text == "hello"
    assert Reply.from_frame("x", frame()).text == ""


def test_json():
    assert Reply.from_frame("x", frame(ResultString='{"a": 1}')).json() == {"a": 1}
    assert Reply.from_frame("x", frame(ResultString='[{"a": 1}]')).json() == {"a": 1}
    assert Reply.from_frame("x", frame(ResultString="[1, 2]")).json() == [1, 2]
    assert Reply.from_frame("x", frame(ResultString="not json")).json() is None
    assert Reply.from_frame("x", frame(Result=[3])).json() == [3]
    assert Reply.from_frame("x", frame(Result=5)).json() is None


def test_raw_frame_is_kept():
    f = frame(Result=1)
    assert Reply.from_frame("x", f).raw is f


def test_non_dict_data():
    assert Reply.from_frame("x", {"type": "CommandResult", "data": "plain"}).text == "plain"
    assert Reply.from_frame("x", {"type": "CommandResult"}).text == ""


def test_roster_reads_both_casings_and_skips_blank_names():
    reply = Reply.from_frame("player list", frame(Result=[
        {"username": "Alice", "id": 1}, {"Username": "Bob", "Identifier": 2}, {"username": " "}, "junk"]))
    assert reply.roster() == [Player("Alice", 1), Player("Bob", 2)]
    assert Reply.from_frame("x", frame(Result=None)).roster() is None


def test_response_type_numeric_enum():
    assert response_type(0) == "SystemMessage"
    assert response_type(1) == "Subscription"
    assert response_type(2) == "CommandResult"
    assert response_type(7) == "7"
    assert response_type("CommandResult") == "CommandResult"
    assert response_type(None) == ""


@pytest.mark.parametrize("data, outcome", [
    ({"Result": 0}, "Success"), ({"Result": "Success"}, "Success"), ({"Result": 1}, "Unchanged"),
    ({"Result": 2}, "NotFound"), ({"Result": None, "ResultString": "Subscribed to X"}, "Success"),
    ({"Exception": "nope"}, "error: nope"), ({}, "null"), ({"Result": "Odd"}, "Odd"),
])
def test_subscription_outcome(data, outcome):
    assert subscription_outcome({"data": data}) == outcome


def test_to_command_result_serialises_a_non_string_result_string():
    assert to_command_result(frame(ResultString=[1]))["data"]["ResultString"] == "[1]"
