"""The web phone's lines, read from WEB_PHONE_CALLER. Pure logic, no database."""

import pytest

from drivetruagent.api.web_phone import WebPhoneOff, caller_numbers


@pytest.mark.parametrize(
    ("setting", "expected"),
    [
        (None, [None]),
        ("", [None]),
        (" , ", [None]),
        ("(647) 555-0123", ["+16475550123"]),
        (
            "+16475550123, 416 555 0188,613-555-0142",
            ["+16475550123", "+14165550188", "+16135550142"],
        ),
        # Written twice is still one line.
        ("+16475550123,647 555 0123", ["+16475550123"]),
        ("+16475550123,,", ["+16475550123"]),
    ],
)
def test_each_number_is_a_line(setting: str | None, expected: list[str | None]) -> None:
    assert caller_numbers(setting) == expected


@pytest.mark.parametrize("setting", ["my cell", "+16475550123, my cell"])
def test_anything_that_is_not_a_number_turns_the_web_phone_off(setting: str) -> None:
    with pytest.raises(WebPhoneOff):
        caller_numbers(setting)
